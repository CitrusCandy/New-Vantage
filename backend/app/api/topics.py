from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import logging
import re
from threading import Event, Lock
from typing import Any, Dict, List, Optional, Set
import unicodedata

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.audit import record_audit_event
from app.core import resource_governor
from app.core.security import SecurityRole, require_operator, sanitize_search_query, validate_slug
from app.database.database import SessionLocal, get_db
from app.database.models import RawX, Topic
from app.database.schemas import (
    TopicCreate,
    TopicDetailResponse,
    TopicResponse,
    TopicUpdate,
)
from app.ingestion.merge_pipeline import MergePipeline
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.x import XScraper
from app.llm.pipeline import PerspectivePipeline
from app.processing.cluster_pipeline import ClusterPipeline

logger = logging.getLogger("app.api.topics")

router = APIRouter(prefix="/topics", tags=["Topics"])
_x_background_jobs_lock = Lock()
_active_x_background_jobs: Set[int] = set()
_x_background_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="vantage-x-enrichment")


def _update_x_job_state(
    db: Session,
    topic: Topic,
    *,
    ingestion_status: Optional[str] = None,
    analysis_status: Optional[str] = None,
    message: Optional[str] = None,
) -> None:
    current_topic = (
        db.query(Topic)
        .populate_existing()
        .with_for_update()
        .filter(Topic.id == topic.id)
        .first()
    )
    if not current_topic:
        return

    coverage = dict(current_topic.source_coverage or {})
    if ingestion_status is not None:
        coverage["x_ingestion_status"] = ingestion_status
    if analysis_status is not None:
        coverage["x_analysis_status"] = analysis_status
    if message is not None:
        coverage["x_ingestion_message"] = message[:240]
    coverage["x_ingestion_updated_at"] = datetime.utcnow().isoformat()
    current_topic.source_coverage = coverage
    db.add(current_topic)
    db.commit()
    db.refresh(current_topic)


def _release_x_merge_gate(gate: Event) -> None:
    """Let background X work merge only after the first result has been sent."""
    gate.set()


def _run_deferred_x_enrichment(
    topic_id: int,
    limit_per_source: int,
    min_volume_threshold: int,
    base_result_sent: Event,
    fetch_finished: Event,
) -> None:
    """Fetch X concurrently, then merge and refresh after the first result is sent."""
    db = SessionLocal()
    fetch_error: Optional[Exception] = None
    try:
        topic = db.query(Topic).filter(Topic.id == topic_id).first()
        if not topic:
            fetch_finished.set()
            base_result_sent.wait()
            return

        _update_x_job_state(
            db,
            topic,
            ingestion_status="running",
            analysis_status="pending",
            message="Searching X/Twitter instances; new posts appear here as they are staged.",
        )
        request_recorded = False
        try:
            allowed, reason = resource_governor.external_governor.check_request_allowed("x")
            if not allowed:
                raise RuntimeError(f"X ingestion deferred by request governor: {reason}")
            resource_governor.external_governor.record_request_start("x")
            resource_governor.cost_tracker.record_external_request("x")
            request_recorded = True
            scraper = XScraper()
            records = scraper.fetch_and_stage(
                topic,
                db,
                limit=limit_per_source,
                timeout_seconds=10.0,
            )
        except Exception as exc:
            fetch_error = exc
            records = []
        finally:
            if request_recorded:
                try:
                    resource_governor.external_governor.record_request_end("x")
                except Exception:
                    logger.debug("Could not close X external-request accounting", exc_info=True)
            fetch_finished.set()

        _update_x_job_state(
            db,
            topic,
            ingestion_status="ready",
            analysis_status="pending",
            message=(
                "X/Twitter data is staged and will be merged after the first result is sent."
                if not fetch_error
                else "X/Twitter request finished with an error; finalizing the first result."
            ),
        )
        base_result_sent.wait()

        if fetch_error:
            raise fetch_error

        baseline_raw_id = int((topic.source_coverage or {}).get("x_ingestion_baseline_raw_id", 0))
        staged_count = db.query(RawX).filter(
            RawX.slug_id == topic_id,
            RawX.id > baseline_raw_id,
        ).count()
        if not records and staged_count == 0:
            _update_x_job_state(
                db,
                topic,
                ingestion_status="complete",
                analysis_status="skipped",
                message="X/Twitter search finished without additional posts.",
            )
            return

        _update_x_job_state(
            db,
            topic,
            ingestion_status="merging",
            analysis_status="pending",
            message=f"Staged {max(len(records), staged_count)} new X/Twitter posts; merging them into the result.",
        )
        merge_result = MergePipeline().merge_topic_staging_data(topic=topic, db=db)
        topic = db.query(Topic).filter(Topic.id == topic_id).first()
        added_x = (merge_result.get("source_breakdown") or {}).get("x", 0)
        if not topic or not added_x:
            if topic:
                _update_x_job_state(
                    db,
                    topic,
                    ingestion_status="complete",
                    analysis_status="skipped",
                    message="X/Twitter search finished; no new records needed analysis.",
                )
            return

        _update_x_job_state(
            db,
            topic,
            ingestion_status="complete",
            analysis_status="running",
            message=f"Added {added_x} X/Twitter posts; refreshing the perspectives.",
        )
        cluster_result = ClusterPipeline().run_for_topic(
            topic=topic,
            db=db,
            min_volume_threshold=min_volume_threshold,
        )
        PerspectivePipeline().run_synthesis_for_topic(
            topic=topic,
            db=db,
            min_volume_threshold=min_volume_threshold,
            cluster_data=cluster_result,
        )

        from app.workers.trending import TrendingScorer

        topic = db.query(Topic).filter(Topic.id == topic_id).first()
        if topic:
            score = TrendingScorer().calculate_topic_score(topic=topic, db=db)
            topic.trending_score = score.final_score
            db.commit()
            db.refresh(topic)
            _update_x_job_state(
                db,
                topic,
                ingestion_status="complete",
                analysis_status="complete",
                message=f"X/Twitter enrichment finished with {added_x} new posts.",
            )
    except Exception as exc:
        fetch_finished.set()
        base_result_sent.wait()
        db.rollback()
        logger.exception("Deferred X/Twitter enrichment failed for topic ID %d", topic_id)
        topic = db.query(Topic).filter(Topic.id == topic_id).first()
        if topic:
            try:
                _update_x_job_state(
                    db,
                    topic,
                    ingestion_status="failed",
                    analysis_status="failed",
                    message=f"X/Twitter enrichment failed: {exc}",
                )
            except Exception:
                db.rollback()
                logger.exception("Could not save X/Twitter failure status for topic ID %d", topic_id)
    finally:
        db.close()
        with _x_background_jobs_lock:
            _active_x_background_jobs.discard(topic_id)


def _enforce_rate_limit(key: str):
    """Check rate limit for the given key and raise HTTP 429 if exceeded."""
    allowed, retry_after = resource_governor.rate_limiter.check_rate_limit(key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for {key}. Retry after {retry_after}s.",
            headers={"Retry-After": str(int(retry_after))},
        )


def generate_slug(text: str) -> str:
    """Generate a clean, URL-safe slug from text."""
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^\w\s-]", "", normalized.lower()).strip()
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug or "topic"


def get_unique_slug(db: Session, base_slug: str, current_topic_id: Optional[int] = None) -> str:
    """Ensure generated slug is unique by appending numerical suffixes if necessary."""
    slug = base_slug
    counter = 1
    while True:
        query = db.query(Topic).filter(Topic.slug == slug)
        if current_topic_id is not None:
            query = query.filter(Topic.id != current_topic_id)
        if not query.first():
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1


@router.post(
    "",
    response_model=TopicResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new topic",
)
def create_topic(
    topic_in: TopicCreate,
    db: Session = Depends(get_db),
):
    _enforce_rate_limit("public:topic_create")
    """Create a new topic with initialized search/trending metrics and URL-safe slug."""
    raw_slug = topic_in.slug if topic_in.slug else generate_slug(topic_in.title)
    unique_slug = get_unique_slug(db, raw_slug)

    initial_coverage = topic_in.source_coverage or {
        "google_news": 0,
        "reddit": 0,
        "x": 0,
        "total_combined": 0,
    }

    topic = Topic(
        title=topic_in.title.strip(),
        slug=unique_slug,
        search_count=0,
        trending_score=0.0,
        source_coverage=initial_coverage,
        last_clustered_at=None,
        updated_at=datetime.utcnow(),
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return topic


@router.get(
    "",
    response_model=List[TopicResponse],
    summary="List all topics",
)
def list_topics(
    search: Optional[str] = Query(
        default=None,
        description="Optional filter by topic title or slug",
    ),
    db: Session = Depends(get_db),
):
    """Retrieve topics ordered by updated_at descending with optional search filtering."""
    _enforce_rate_limit("public:topic_list")
    query = db.query(Topic)
    if search:
        clean_search = sanitize_search_query(search)
        if clean_search:
            search_term = f"%{clean_search}%"
            query = query.filter(
                or_(
                    Topic.title.ilike(search_term, escape="\\"),
                    Topic.slug.ilike(search_term, escape="\\"),
                )
            )
    return query.order_by(Topic.updated_at.desc()).limit(200).all()


@router.get(
    "/trending",
    response_model=List[TopicResponse],
    summary="Get top trending topics",
)
def get_trending_topics(
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of trending topics to return",
    ),
    min_score: float = Query(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum trending score threshold",
    ),
    db: Session = Depends(get_db),
):
    """Retrieve topics sorted by trending_score descending with optional minimum score filtering."""
    return (
        db.query(Topic)
        .filter(Topic.trending_score >= min_score)
        .order_by(Topic.trending_score.desc(), Topic.updated_at.desc())
        .limit(limit)
        .all()
    )


@router.get(
    "/{slug}",
    response_model=TopicDetailResponse,
    summary="Get a topic by slug",
)
def get_topic_by_slug(
    slug: str,
    db: Session = Depends(get_db),
):
    """Retrieve a single topic along with its perspectives by slug."""
    _enforce_rate_limit("public:topic_detail")
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )
    return topic


@router.get(
    "/{slug}/ingestion-status",
    summary="Get live source ingestion progress for a topic",
)
def get_topic_ingestion_status(
    slug: str,
    db: Session = Depends(get_db),
):
    """Return X staging progress while the deferred Nitter sweep is running."""
    _enforce_rate_limit("public:topic_detail")
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    coverage = dict(topic.source_coverage or {})
    ingestion_status = coverage.get("x_ingestion_status", "complete")
    analysis_status = coverage.get("x_analysis_status", "complete")
    active_statuses = {"pending", "running", "ready", "merging"}
    started_at = coverage.get("x_ingestion_started_at")
    job_pending = ingestion_status in active_statuses or analysis_status in {"pending", "running"}
    if job_pending and started_at:
        try:
            started = datetime.fromisoformat(started_at)
            elapsed = (datetime.utcnow() - started).total_seconds()
            with _x_background_jobs_lock:
                job_is_active = topic.id in _active_x_background_jobs
            if elapsed > 300 and not job_is_active:
                if ingestion_status in active_statuses:
                    ingestion_status = "failed"
                analysis_status = "failed"
                coverage["x_ingestion_status"] = ingestion_status
                coverage["x_analysis_status"] = analysis_status
                coverage["x_ingestion_message"] = "X/Twitter background work did not finish. Refresh analysis to retry."
                topic.source_coverage = coverage
                db.commit()
        except (TypeError, ValueError):
            pass

    baseline_raw_id = int(coverage.get("x_ingestion_baseline_raw_id", 0))
    new_count = db.query(RawX).filter(
        RawX.slug_id == topic.id,
        RawX.id > baseline_raw_id,
    ).count()
    new_posts = (
        db.query(RawX)
        .filter(RawX.slug_id == topic.id, RawX.id > baseline_raw_id)
        .order_by(RawX.id.desc())
        .limit(3)
        .all()
    )
    return {
        "topic_slug": topic.slug,
        "x_status": ingestion_status,
        "analysis_status": analysis_status,
        "x_new_count": new_count,
        "x_merged_count": int((topic.source_coverage or {}).get("x", 0)),
        "x_baseline_coverage": int(coverage.get("x_ingestion_baseline_coverage", 0)),
        "x_posts": [
            {
                "tweet_id": post.tweet_id,
                "handle": post.handle,
                "text": post.text[:500],
                "posted_at": post.posted_at.isoformat() if post.posted_at else None,
            }
            for post in reversed(new_posts)
        ],
        "message": coverage.get("x_ingestion_message", ""),
        "updated_at": coverage.get("x_ingestion_updated_at"),
    }


@router.patch(
    "/{slug}",
    response_model=TopicResponse,
    summary="Update a topic",
    dependencies=[Depends(require_operator)],
)
def update_topic(
    slug: str,
    topic_in: TopicUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Update mutable topic fields (title, source coverage). Protected operational endpoint."""
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    if topic_in.title is not None:
        topic.title = topic_in.title.strip()
    if topic_in.source_coverage is not None:
        topic.source_coverage = topic_in.source_coverage

    topic.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(topic)
    record_audit_event(
        action="topic_updated",
        actor=getattr(request.state, "actor", "operator"),
        role=getattr(request.state, "role", SecurityRole.OPERATOR).value if hasattr(getattr(request.state, "role", None), "value") else str(getattr(request.state, "role", "operator")),
        resource=f"/api/topics/{slug}",
        ip_address=request.client.host if request.client else None,
        status="allowed",
        details={"slug": slug, "title": topic.title},
        db=db,
    )
    return topic


@router.delete(
    "/{slug}",
    status_code=status.HTTP_200_OK,
    summary="Delete a topic",
    dependencies=[Depends(require_operator)],
)
def delete_topic(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Safely delete a topic and its associated child records."""
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    topic_title = topic.title
    db.delete(topic)
    db.commit()
    record_audit_event(
        action="topic_deleted",
        actor=getattr(request.state, "actor", "operator"),
        role=getattr(request.state, "role", SecurityRole.OPERATOR).value if hasattr(getattr(request.state, "role", None), "value") else str(getattr(request.state, "role", "operator")),
        resource=f"/api/topics/{slug}",
        ip_address=request.client.host if request.client else None,
        status="allowed",
        details={"slug": slug, "title": topic_title},
        db=db,
    )
    return {"message": f"Topic '{slug}' and its associated records have been deleted successfully"}


@router.post(
    "/{slug}/ingest",
    status_code=status.HTTP_200_OK,
    summary="Trigger multi-source ingestion into staging tables and merge",
)
def ingest_topic(
    slug: str,
    limit_per_source: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Trigger independent scrapers (Google News, Reddit, X) into staging tables followed by merge."""
    _enforce_rate_limit("public:ingestion")
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    clamped_limit = resource_governor.budget_manager.clamp("max_ingestion_items_per_source", limit_per_source)
    pipeline = IngestionPipeline()
    return pipeline.run(topic=topic, db=db, limit_per_source=clamped_limit)


@router.post(
    "/{slug}/merge",
    status_code=status.HTTP_200_OK,
    summary="Merge staging tables into combined_raw_data",
)
def merge_topic_staging(
    slug: str,
    db: Session = Depends(get_db),
):
    """Execute merge/normalization from raw_google_news, raw_reddit, raw_x into combined_raw_data."""
    _enforce_rate_limit("public:merge")
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    pipeline = MergePipeline()
    return pipeline.merge_topic_staging_data(topic=topic, db=db)


@router.post(
    "/{slug}/cluster",
    status_code=status.HTTP_200_OK,
    summary="Trigger clustering for a topic",
)
def cluster_topic(
    slug: str,
    min_volume_threshold: int = Query(
        default=5,
        ge=2,
        description="Minimum usable discourse posts required before clustering",
    ),
    db: Session = Depends(get_db),
):
    """Run preprocessing, embeddings generation, HDBSCAN clustering on combined dataset."""
    _enforce_rate_limit("public:clustering")
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    pipeline = ClusterPipeline()
    return pipeline.run_for_topic(
        topic=topic,
        db=db,
        min_volume_threshold=min_volume_threshold,
    )


@router.post(
    "/{slug}/synthesize",
    status_code=status.HTTP_200_OK,
    summary="Trigger LLM perspective synthesis for a topic",
)
def synthesize_topic_perspectives(
    slug: str,
    min_volume_threshold: int = Query(
        default=5,
        ge=2,
        description="Minimum usable discourse posts required before synthesis",
    ),
    db: Session = Depends(get_db),
):
    """Extract representative cluster samples and synthesize structured perspectives using LLM."""
    _enforce_rate_limit("public:synthesis")
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    pipeline = PerspectivePipeline()
    return pipeline.run_synthesis_for_topic(
        topic=topic,
        db=db,
        min_volume_threshold=min_volume_threshold,
    )


@router.post(
    "/{slug}/run-pipeline",
    status_code=status.HTTP_200_OK,
    summary="Return news and Reddit analysis while X/Twitter enrichment continues in the background",
)
def run_full_pipeline(
    slug: str,
    background_tasks: BackgroundTasks,
    limit_per_source: int = Query(default=100, ge=1, le=200),
    min_volume_threshold: int = Query(default=5, ge=2),
    db: Session = Depends(get_db),
):
    """Run the first analysis from news/Reddit and continue X enrichment asynchronously."""
    _enforce_rate_limit("public:pipeline")
    topic = db.query(Topic).filter(Topic.slug == slug).first()
    if not topic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Topic with slug '{slug}' not found",
        )

    # Enforce pipeline concurrency limit
    if not resource_governor.concurrency_governor.acquire("pipeline", holder_id=slug):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Maximum concurrent pipelines reached. Please try again later.",
            headers={"Retry-After": "30"},
        )

    clamped_limit = resource_governor.budget_manager.clamp("max_ingestion_items_per_source", limit_per_source)
    resource_governor.cost_tracker.record_pipeline_invocation()

    from app.core.telemetry import PipelineTimingTracker
    tracker = PipelineTimingTracker("end_to_end_pipeline", topic_slug=slug)
    base_result_sent = Event()
    fetch_finished = Event()
    start_x_job = False
    x_worker_submitted = False
    x_job_start_error: Optional[str] = None
    x_started_at: Optional[str] = None
    baseline_raw_id = 0
    baseline_x_coverage = int((topic.source_coverage or {}).get("x", 0))
    prior_x_state = dict(topic.source_coverage or {})

    try:
        # Start X immediately beside the Google News/Reddit work. The X worker
        # may finish early and be included in the first merge; otherwise it
        # continues independently while the initial result is prepared.
        with _x_background_jobs_lock:
            start_x_job = topic.id not in _active_x_background_jobs
            if start_x_job:
                _active_x_background_jobs.add(topic.id)

        if start_x_job:
            baseline_raw_id = db.query(func.max(RawX.id)).filter(RawX.slug_id == topic.id).scalar() or 0
            x_started_at = datetime.utcnow().isoformat()
            coverage = dict(topic.source_coverage or {})
            coverage.update({
                "x_ingestion_status": "pending",
                "x_analysis_status": "pending",
                "x_ingestion_message": "X/Twitter search started alongside Google News and Reddit RSS.",
                "x_ingestion_started_at": x_started_at,
                "x_ingestion_updated_at": x_started_at,
                "x_ingestion_baseline_raw_id": int(baseline_raw_id),
                "x_ingestion_baseline_coverage": baseline_x_coverage,
                "x_ingestion_new_count": 0,
            })
            topic.source_coverage = coverage
            db.commit()
            db.refresh(topic)
            try:
                _x_background_executor.submit(
                    _run_deferred_x_enrichment,
                    topic.id,
                    clamped_limit,
                    min_volume_threshold,
                    base_result_sent,
                    fetch_finished,
                )
                x_worker_submitted = True
            except Exception as exc:
                with _x_background_jobs_lock:
                    _active_x_background_jobs.discard(topic.id)
                fetch_finished.set()
                base_result_sent.set()
                x_job_start_error = str(exc)
                _update_x_job_state(
                    db,
                    topic,
                    ingestion_status="failed",
                    analysis_status="failed",
                    message=f"Could not start X/Twitter enrichment: {exc}",
                )
                start_x_job = False

        # 1. Ingest & Merge
        with tracker.track("ingestion_and_merge"):
            ingestion_pipeline = IngestionPipeline()
            ingestion_res = ingestion_pipeline.run(
                topic=topic,
                db=db,
                limit_per_source=clamped_limit,
                defer_x=True,
            )

        # If Nitter finished before the news/Reddit merge, those posts are
        # already part of the first result and should be reported as such.
        initial_x_added = (ingestion_res.get("merge_result", {}).get("source_breakdown") or {}).get("x", 0)
        if initial_x_added:
            ingestion_res["staging_counts"]["x"] = initial_x_added
            ingestion_res["total_staged"] = sum(ingestion_res["staging_counts"].values())
            ingestion_res["provider_diagnostics"]["x"] = {
                "source": "x",
                "status": "success",
                "records_staged": initial_x_added,
                "message": f"Successfully staged {initial_x_added} X/Twitter posts before the first result",
            }

        # 2. HDBSCAN Cluster
        with tracker.track("hdbscan_clustering"):
            cluster_pipeline = ClusterPipeline()
            cluster_res = cluster_pipeline.run_for_topic(
                topic=topic,
                db=db,
                min_volume_threshold=min_volume_threshold,
            )

        # 3. LLM Synthesis
        synthesis_res = None
        if cluster_res.get("status") == "success":
            with tracker.track("llm_perspective_synthesis"):
                perspective_pipeline = PerspectivePipeline()
                synthesis_res = perspective_pipeline.run_synthesis_for_topic(
                    topic=topic,
                    db=db,
                    min_volume_threshold=min_volume_threshold,
                    cluster_data=cluster_res,
                )

        # 4. Refresh trending score
        with tracker.track("trending_score_recalculation"):
            from app.workers.trending import TrendingScorer
            scorer = TrendingScorer()
            score_breakdown = scorer.calculate_topic_score(topic=topic, db=db)
            topic.trending_score = score_breakdown.final_score
            db.commit()
            db.refresh(topic)

        timings_summary = tracker.finish(status="success")

        # Publish the news/Reddit result now. X collection continues after the
        # response if it was not already included in the initial merge.
        coverage = dict(topic.source_coverage or {})
        coverage["pipeline_status"] = "complete"
        if start_x_job:
            x_fetch_ready = fetch_finished.is_set()
            coverage.update({
                "x_ingestion_status": "ready" if x_fetch_ready else "running",
                "x_analysis_status": "pending",
                "x_ingestion_message": (
                    "X/Twitter requests finished; finalizing the first result."
                    if x_fetch_ready
                    else "News and Reddit are ready; X/Twitter instance checks are continuing live."
                ),
                "x_ingestion_started_at": x_started_at,
                "x_ingestion_updated_at": datetime.utcnow().isoformat(),
                "x_ingestion_baseline_raw_id": int(baseline_raw_id),
                "x_ingestion_baseline_coverage": baseline_x_coverage,
                "x_ingestion_new_count": 0,
            })
        elif x_job_start_error:
            coverage.update({
                "x_ingestion_status": "failed",
                "x_analysis_status": "failed",
                "x_ingestion_message": f"Could not start X/Twitter enrichment: {x_job_start_error}"[:240],
            })
        else:
            for key in (
                "x_ingestion_status",
                "x_analysis_status",
                "x_ingestion_message",
                "x_ingestion_started_at",
                "x_ingestion_updated_at",
                "x_ingestion_baseline_raw_id",
                "x_ingestion_baseline_coverage",
            ):
                if key in prior_x_state:
                    coverage[key] = prior_x_state[key]
            if not coverage.get("x_ingestion_status"):
                coverage.update({
                    "x_ingestion_status": "running",
                    "x_analysis_status": "running",
                    "x_ingestion_message": "X/Twitter enrichment for this topic is already running.",
                })
        coverage["pipeline_status"] = "complete"
        topic.source_coverage = coverage
        db.commit()
        db.refresh(topic)

        if start_x_job:
            background_tasks.add_task(_release_x_merge_gate, base_result_sent)

        return {
            "status": "success",
            "topic": {
                "id": topic.id,
                "slug": topic.slug,
                "title": topic.title,
                "trending_score": topic.trending_score,
                "source_coverage": topic.source_coverage,
                "perspectives_count": len(topic.perspectives) if topic.perspectives else 0,
            },
            "ingestion": ingestion_res,
            "clustering": cluster_res,
            "synthesis": synthesis_res,
            "timings": timings_summary,
        }
    except Exception:
        base_result_sent.set()
        if start_x_job and not x_worker_submitted:
            fetch_finished.set()
            with _x_background_jobs_lock:
                _active_x_background_jobs.discard(topic.id)
        tracker.finish(status="failed")
        raise
    finally:
        resource_governor.concurrency_governor.release("pipeline", holder_id=slug)


@router.post(
    "/submit-and-analyze",
    status_code=status.HTTP_200_OK,
    summary="Submit a topic and immediately execute automatic 100-item multi-source pipeline",
)
def submit_and_analyze_topic(
    topic_in: TopicCreate,
    background_tasks: BackgroundTasks,
    limit_per_source: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Automatic topic submission endpoint: Creates or retrieves topic and executes the complete 100-item ingestion, deduplication, clustering, and perspective synthesis pipeline."""
    _enforce_rate_limit("public:topic_create")
    title_clean = topic_in.title.strip()
    if not title_clean:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Topic title cannot be empty",
        )

    base_slug = topic_in.slug if topic_in.slug else generate_slug(title_clean)

    # Check if a topic with this slug or title already exists
    existing_topic = db.query(Topic).filter(
        or_(Topic.slug == base_slug, Topic.title.ilike(title_clean))
    ).first()

    if existing_topic:
        target_topic = existing_topic
    else:
        unique_slug = get_unique_slug(db, base_slug)
        target_topic = Topic(
            title=title_clean,
            slug=unique_slug,
            search_count=0,
            trending_score=0.0,
            source_coverage={
                "google_news": 0,
                "reddit": 0,
                "x": 0,
                "total_combined": 0,
                "target_items": 100,
            },
            last_clustered_at=None,
            updated_at=datetime.utcnow(),
        )
        db.add(target_topic)
        db.commit()
        db.refresh(target_topic)

    # Automatically execute complete pipeline for this topic
    return run_full_pipeline(
        slug=target_topic.slug,
        limit_per_source=limit_per_source,
        min_volume_threshold=5,
        background_tasks=background_tasks,
        db=db,
    )


