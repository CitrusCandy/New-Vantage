from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import logging
import os
import random
import re
import socket
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple
import urllib.error
import urllib.parse
import urllib.request

import feedparser
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.database.models import RawX, Topic

logger = logging.getLogger("app.ingestion.x")

DEFAULT_NITTER_INSTANCES = (
    # Instances shown as working in the supplied list.
    "https://shitter.thepixora.com",
    "https://nitter.meowing.monster",
    "https://nitter.netbub.com",
    "https://shi.meowing.de",
    "https://nitter.mingtiancup.me",
    "https://nitter.jaydenha.uk",
    "https://nitter.click",
    "https://nitter.xitter.cc",
    "https://x.n0g.xyz",
    "https://tw.eir-nya.gay",
    "https://xcoppy.uk",
    "https://bitter.st",
    # Additional instances and redirects supplied by the user.
    "https://tw1tter.com",
    "https://nr.evilasswebsite.com",
    "https://nitter.space",
    "https://nitt.tr",
    "https://twit.0r.cx",
    "https://xxcancel.com",
    "https://twiiit.com",
    # Instances listed as active but rate limited.
    "https://nitter.privacyredirect.com",
    "https://nitter.catsarch.com",
    "https://nitter.tiekoetter.com",
    "https://nt.venc.cc",
    "https://nitter.fullex.fr",
    "https://nitter.anoxinon.de",
    "https://nitter6.kabi.moe",
    "https://nitter.zebes.info",
    "https://nitter.wisg.net",
    "https://nitter.teamqq.de",
    "https://nitter.freedit.eu",
    # Tor-only instances from the supplied list; they are skipped without a proxy.
    "http://nitter.lesboswza6waq3itqo46rvf5a7dibxh32fumwg6fpssxj66spmcldy.onion",
    "http://dhgjnhxktgesvcddfwzqm4d4zmyz2x2jud6huweu5ywuv00v7qaqpjid.onion",
)

_instance_rotation_lock = threading.Lock()
_instance_rotation = 0
_instance_cooldowns: Dict[str, float] = {}


class NitterInstanceError(RuntimeError):
    """Raised when an instance is unavailable, rate-limited, or serving a bot wall."""


class XScraper:
    """Fetch public X posts via an optional Xquik API and fallback Nitter RSS feeds."""

    source_name = "x"

    def __init__(self, max_retries: int = 2, backoff_seconds: float = 1.0):
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.nitter_instances = self._load_nitter_instances()

    @staticmethod
    def _env_int(name: str, default: int, minimum: int = 0) -> int:
        try:
            return max(minimum, int(os.getenv(name, str(default))))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _env_float(name: str, default: float, minimum: float = 0.1) -> float:
        try:
            return max(minimum, float(os.getenv(name, str(default))))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _load_nitter_instances() -> List[str]:
        configured = os.getenv("NITTER_INSTANCES", "").strip()
        candidates = configured.split(",") if configured else DEFAULT_NITTER_INSTANCES
        normalized: List[str] = []
        seen = set()

        for candidate in candidates:
            value = candidate.strip().rstrip("/")
            if not value:
                continue
            parts = urllib.parse.urlsplit(value if "://" in value else f"https://{value}")
            if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
                logger.warning("[x] Ignoring invalid Nitter instance URL")
                continue
            base_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
            key = base_url.lower()
            if key not in seen:
                normalized.append(base_url)
                seen.add(key)
        return normalized

    def parse_html_and_persist(
        self,
        html_content: str,
        topic_id: int,
        db: Session,
        limit: int = 50,
    ) -> List[RawX]:
        """Parse rendered X/Nitter HTML and stage records (kept for compatibility)."""
        if not html_content:
            return []

        soup = BeautifulSoup(html_content, "html.parser")
        tweet_elements = soup.find_all("article") or soup.find_all("div", class_=re.compile(r"tweet|post"))

        staged_records: List[RawX] = []
        for index, elem in enumerate(tweet_elements):
            if len(staged_records) >= limit:
                break
            try:
                text_elem = elem.find("div", {"data-testid": "tweetText"}) or elem.find("div", class_=re.compile(r"text|content"))
                text = text_elem.get_text(separator=" ", strip=True) if text_elem else elem.get_text(separator=" ", strip=True)
                if not text or len(text) < 10:
                    continue

                handle = None
                user_elem = elem.find("div", {"data-testid": "User-Name"}) or elem.find("span", string=re.compile(r"^@\w+"))
                if user_elem:
                    handle_match = re.search(r"@([\w]+)", user_elem.get_text())
                    if handle_match:
                        handle = handle_match.group(1)

                tweet_id = None
                link_elem = elem.find("a", href=re.compile(r"/status/(\d+)"))
                if link_elem and "href" in link_elem.attrs:
                    id_match = re.search(r"/status/(\d+)", link_elem["href"])
                    if id_match:
                        tweet_id = id_match.group(1)
                if not tweet_id:
                    tweet_id = f"mock_tx_{topic_id}_{index}_{int(time.time())}"

                time_elem = elem.find("time")
                posted_at = datetime.now(timezone.utc).replace(tzinfo=None)
                if time_elem and time_elem.get("datetime"):
                    try:
                        posted_at = datetime.fromisoformat(time_elem["datetime"].replace("Z", "+00:00")).replace(tzinfo=None)
                    except (TypeError, ValueError):
                        pass

                record = RawX(
                    slug_id=topic_id,
                    tweet_id=tweet_id,
                    text=text,
                    likes=self._extract_metric(elem, "like"),
                    retweets=self._extract_metric(elem, "retweet"),
                    replies=self._extract_metric(elem, "reply"),
                    handle=handle or "anonymous",
                    posted_at=posted_at,
                )
                db.add(record)
                staged_records.append(record)
            except Exception as exc:
                logger.warning("[x] Error parsing tweet element #%d: %s", index, exc)
        db.commit()
        logger.info("[x] Staged %d HTML records for Topic ID %d", len(staged_records), topic_id)
        return staged_records

    @staticmethod
    def _extract_metric(elem, test_id_substr: str) -> int:
        target = elem.find(attrs={"data-testid": re.compile(test_id_substr, re.I)}) or elem.find(attrs={"aria-label": re.compile(test_id_substr, re.I)})
        if target:
            text = target.get_text(strip=True) or target.get("aria-label", "")
            match = re.search(r"(\d+(?:,\d+)*(?:\.\d+)?[kKmM]?)", text)
            if match:
                val_str = match.group(1).replace(",", "")
                if val_str.lower().endswith("k"):
                    return int(float(val_str[:-1]) * 1000)
                if val_str.lower().endswith("m"):
                    return int(float(val_str[:-1]) * 1000000)
                try:
                    return int(float(val_str))
                except ValueError:
                    pass
        return 0

    @staticmethod
    def _entry_text(entry) -> str:
        for key in ("content", "summary", "description", "title"):
            value = getattr(entry, key, None)
            if key == "content" and value:
                value = value[0].get("value", "") if isinstance(value, list) else value
            if value:
                text = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) >= 10:
                    return text
        return ""

    @staticmethod
    def _entry_handle(entry, link: str) -> str:
        author_detail = getattr(entry, "author_detail", None) or {}
        author = getattr(entry, "author", None) or author_detail.get("name") or ""
        match = re.search(r"@?([A-Za-z0-9_]{1,64})", str(author).strip())
        if match:
            handle = match.group(1)
        else:
            match = re.search(r"/(?:@)?([A-Za-z0-9_]{1,64})/status/", link)
            handle = match.group(1) if match else "anonymous"
        return handle.lstrip("@")

    @staticmethod
    def _entry_datetime(entry) -> datetime:
        published = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
        if published:
            try:
                return datetime(*published[:6])
            except (TypeError, ValueError):
                pass
        published_text = getattr(entry, "published", None) or getattr(entry, "updated", None)
        if published_text:
            try:
                parsed = parsedate_to_datetime(published_text)
                return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
            except (TypeError, ValueError, OverflowError):
                pass
        return datetime.now(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _retry_after_seconds(headers) -> Optional[float]:
        value = headers.get("Retry-After") if headers else None
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    @staticmethod
    def _is_bot_or_rate_limit_page(body: bytes) -> bool:
        prefix = body[:512].decode("utf-8", errors="ignore").lstrip("\ufeff \t\r\n").lower()
        # Tweets themselves can mention captchas and rate limits; only scan
        # challenge text when the response is not an RSS/Atom document.
        if prefix.startswith(("<?xml", "<rss", "<feed", "<rdf:rdf")):
            return False
        sample = body[:12000].decode("utf-8", errors="ignore").lower()
        markers = (
            "verify you are human",
            "checking your browser",
            "attention required",
            "cf-chl-",
            "captcha",
            "rate limit exceeded",
            "too many requests",
            "automated requests",
        )
        return any(marker in sample for marker in markers)

    @staticmethod
    def _is_retryable_error(error: Exception) -> bool:
        if isinstance(error, urllib.error.HTTPError):
            return error.code in (403, 408, 425, 429, 500, 502, 503, 504)
        if isinstance(error, (TimeoutError, socket.timeout)):
            return True
        if isinstance(error, urllib.error.URLError):
            return isinstance(error.reason, (TimeoutError, socket.timeout))
        return isinstance(error, NitterInstanceError)

    @staticmethod
    def _round_robin(instances: Sequence[str]) -> List[str]:
        global _instance_rotation
        if not instances:
            return []
        with _instance_rotation_lock:
            start = _instance_rotation % len(instances)
            _instance_rotation += 1
        return list(instances[start:]) + list(instances[:start])

    @staticmethod
    def _instance_cooldown_remaining(host: str) -> float:
        with _instance_rotation_lock:
            expires_at = _instance_cooldowns.get(host.lower(), 0.0)
            if expires_at <= time.monotonic():
                _instance_cooldowns.pop(host.lower(), None)
                return 0.0
            return expires_at - time.monotonic()

    @staticmethod
    def _cool_down_instance(
        host: str,
        error: Exception,
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        if isinstance(error, urllib.error.HTTPError) and error.code == 429:
            cooldown = XScraper._env_float("NITTER_RATE_LIMIT_COOLDOWN_SECONDS", 300.0)
            if retry_after_seconds is not None:
                max_retry_after = XScraper._env_float("NITTER_MAX_RETRY_AFTER_SECONDS", 30.0)
                cooldown = max(cooldown, min(retry_after_seconds, max_retry_after))
        elif (isinstance(error, urllib.error.HTTPError) and error.code == 403) or isinstance(error, NitterInstanceError):
            cooldown = XScraper._env_float("NITTER_BOT_CHECK_COOLDOWN_SECONDS", 900.0)
        else:
            cooldown = XScraper._env_float("NITTER_FAILURE_COOLDOWN_SECONDS", 60.0)
        with _instance_rotation_lock:
            _instance_cooldowns[host.lower()] = time.monotonic() + cooldown

    def _parse_nitter_feed(self, payload: bytes, topic_id: int, limit: int) -> Tuple[List[RawX], bool]:
        parsed = feedparser.parse(payload)
        version = getattr(parsed, "version", "")
        entries = getattr(parsed, "entries", []) or []
        if not version and not entries:
            raise NitterInstanceError("response was not a readable RSS/Atom feed")

        records: List[RawX] = []
        seen_ids = set()
        for entry in entries:
            if len(records) >= limit:
                break
            text = self._entry_text(entry)
            if not text:
                continue
            link = str(getattr(entry, "link", "") or "")
            id_match = re.search(r"/status/(\d+)", link)
            posted_at = self._entry_datetime(entry)
            tweet_id = id_match.group(1) if id_match else "nitter_" + hashlib.sha256(
                f"{link}\n{text}\n{posted_at.isoformat()}".encode("utf-8")
            ).hexdigest()[:40]
            if tweet_id in seen_ids:
                continue
            seen_ids.add(tweet_id)
            records.append(
                RawX(
                    slug_id=topic_id,
                    tweet_id=tweet_id,
                    text=text,
                    likes=0,
                    retweets=0,
                    replies=0,
                    handle=self._entry_handle(entry, link),
                    posted_at=posted_at,
                )
            )
        return records, True

    def _fetch_nitter(
        self,
        topic: Topic,
        db: Session,
        limit: int,
        timeout_seconds: float,
    ) -> List[RawX]:
        if not self.nitter_instances:
            raise NitterInstanceError("no Nitter instances are configured")

        max_instances = min(len(self.nitter_instances), self._env_int("NITTER_MAX_INSTANCES", 8, 1))
        max_successful = self._env_int("NITTER_MAX_SUCCESSFUL_INSTANCES", 2, 1)
        retries = self._env_int("NITTER_MAX_RETRIES", 1)
        # Keep each host request bounded while allowing the background sweep the
        # longer window needed to rotate through slow or rate-limited instances.
        request_timeout = min(timeout_seconds, self._env_float("NITTER_REQUEST_TIMEOUT_SECONDS", 8.0))
        total_timeout = min(120.0, self._env_float("NITTER_TOTAL_TIMEOUT_SECONDS", 120.0))
        backoff_base = self._env_float("NITTER_BACKOFF_BASE_SECONDS", 1.0)
        backoff_max = self._env_float("NITTER_BACKOFF_MAX_SECONDS", 2.0)
        deadline = time.monotonic() + total_timeout
        tor_proxy = os.getenv("NITTER_HTTP_PROXY", "").strip()
        hosts_checked = 0
        healthy_instances = 0
        staged_by_id = {}
        existing_ids = {
            tweet_id
            for (tweet_id,) in db.query(RawX.tweet_id).filter(RawX.slug_id == topic.id).all()
            if tweet_id
        }
        last_error: Optional[Exception] = None

        for base_url in self._round_robin(self.nitter_instances):
            if hosts_checked >= max_instances or healthy_instances >= max_successful or len(staged_by_id) >= limit:
                break
            if time.monotonic() >= deadline:
                break
            host = urllib.parse.urlsplit(base_url).hostname or ""
            if host.endswith(".onion") and not tor_proxy:
                logger.debug("[x] Skipping Tor-only Nitter instance %s because NITTER_HTTP_PROXY is unset", host)
                continue
            cooldown_remaining = self._instance_cooldown_remaining(host)
            if cooldown_remaining > 0:
                logger.debug("[x] Skipping Nitter instance %s for %.0fs after a recent failure", host, cooldown_remaining)
                continue
            hosts_checked += 1
            query = urllib.parse.urlencode({"f": "tweets", "q": topic.title})
            feed_url = f"{base_url}/search/rss?{query}"
            host = urllib.parse.urlsplit(base_url).netloc
            opener = (
                urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": tor_proxy, "https": tor_proxy})
                )
                if host.endswith(".onion")
                else None
            )
            instance_ok = False
            instance_error: Optional[Exception] = None
            instance_retry_after: Optional[float] = None

            for attempt in range(retries + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                request = urllib.request.Request(
                    feed_url,
                    headers={
                        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
                        "User-Agent": "VantageNews/2.0 (+public-discourse-aggregator)",
                    },
                )
                try:
                    response_context = (
                        opener.open(request, timeout=min(request_timeout, remaining))
                        if opener
                        else urllib.request.urlopen(request, timeout=min(request_timeout, remaining))
                    )
                    with response_context as response:
                        payload = response.read()
                    if self._is_bot_or_rate_limit_page(payload):
                        raise NitterInstanceError("instance returned a bot-check or rate-limit page")
                    records, _ = self._parse_nitter_feed(payload, topic.id, limit)
                    instance_ok = True
                    healthy_instances += 1
                    new_instance_records = []
                    for record in records:
                        if record.tweet_id not in staged_by_id and record.tweet_id not in existing_ids:
                            staged_by_id[record.tweet_id] = record
                            existing_ids.add(record.tweet_id)
                            new_instance_records.append(record)
                            if len(staged_by_id) >= limit:
                                break
                    if new_instance_records:
                        # Commit per successful instance so the status endpoint
                        # and result page can show Nitter evidence as it arrives.
                        db.add_all(new_instance_records)
                        db.commit()
                    logger.info("[x] Nitter instance %s returned %d matching posts", host, len(records))
                    break
                except Exception as exc:
                    if instance_ok:
                        # Do not mistake a staging database error for a bad
                        # instance and retry the same response.
                        db.rollback()
                        raise
                    last_error = exc
                    instance_error = exc
                    instance_retry_after = (
                        self._retry_after_seconds(exc.headers)
                        if isinstance(exc, urllib.error.HTTPError)
                        else None
                    )
                    retryable = self._is_retryable_error(exc)
                    # A blocked/rate-limited instance will not become available
                    # during an in-request retry. Cool it down and try the next.
                    if isinstance(exc, NitterInstanceError) or (
                        isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 429)
                    ):
                        retryable = False
                    if not retryable or attempt >= retries:
                        break

                    delay = min(backoff_max, backoff_base * (2 ** attempt), max(0.0, deadline - time.monotonic()))
                    if delay > 0:
                        # A small jitter avoids synchronized retries across workers.
                        sleep_for = max(
                            0.0,
                            min(deadline - time.monotonic(), backoff_max, delay * random.uniform(0.8, 1.2)),
                        )
                        if sleep_for:
                            time.sleep(sleep_for)

            if not instance_ok:
                if instance_error:
                    self._cool_down_instance(host, instance_error, instance_retry_after)
                logger.warning("[x] Nitter fallback instance %s failed; checking the next instance", host)
            else:
                with _instance_rotation_lock:
                    _instance_cooldowns.pop(host.lower(), None)

        if not healthy_instances:
            if last_error:
                raise NitterInstanceError(f"all {hosts_checked} checked Nitter instances failed") from last_error
            raise NitterInstanceError(f"no Nitter instance responded within {total_timeout:.0f}s")

        staged = list(staged_by_id.values())[:limit]
        logger.info(
            "[x] Staged %d records via Nitter RSS for Topic ID %d (%d instance(s) responded)",
            len(staged),
            topic.id,
            healthy_instances,
        )
        return staged

    @staticmethod
    def _fetch_xquik(topic: Topic, db: Session, limit: int, timeout_seconds: float, base_url: str, api_key: str) -> List[RawX]:
        import json

        params = urllib.parse.urlencode({"q": topic.title, "limit": min(limit, 100)})
        request = urllib.request.Request(
            f"{base_url}/search?{params}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-API-Key": api_key,
                "User-Agent": "VantageNews/2.0",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))

        items = payload.get("tweets") or payload.get("data") or []
        staged: List[RawX] = []
        for index, item in enumerate(items):
            if len(staged) >= limit or not isinstance(item, dict):
                continue
            text = (item.get("text") or item.get("content") or "").strip()
            if len(text) < 10:
                continue
            posted_at = datetime.now(timezone.utc).replace(tzinfo=None)
            timestamp = item.get("created_at") or item.get("posted_at")
            if timestamp:
                try:
                    posted_at = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError:
                    pass
            record = RawX(
                slug_id=topic.id,
                tweet_id=str(item.get("id") or item.get("tweet_id") or f"tx_{topic.id}_{index}"),
                text=text,
                likes=int(item.get("likes") or item.get("like_count") or 0),
                retweets=int(item.get("retweets") or item.get("retweet_count") or 0),
                replies=int(item.get("replies") or item.get("reply_count") or 0),
                handle=str(item.get("author_handle") or item.get("username") or "anonymous").lstrip("@"),
                posted_at=posted_at,
            )
            db.add(record)
            staged.append(record)
        db.commit()
        return staged

    def fetch_and_stage(
        self,
        topic: Topic,
        db: Session,
        limit: int = 50,
        timeout_seconds: float = 10.0,
    ) -> List[RawX]:
        """Try Xquik first, then rotate through public Nitter RSS instances."""
        from app.core.resilience import BackoffStrategy, JitterMode, retry_with_backoff, x_breaker
        from app.core.telemetry import ops_metrics

        started = time.perf_counter()
        api_key = os.getenv("XQUIK_API_KEY", "").strip()
        base_url = os.getenv("XQUIK_BASE_URL", "https://xquik.com/api/v1").strip().rstrip("/")
        api_error: Optional[Exception] = None
        api_succeeded = False

        try:
            if api_key and x_breaker.allow_request():
                logger.info(
                    "Fetching [x] posts for topic '%s' via Xquik (limit=%d, timeout=%.1fs)",
                    topic.title,
                    limit,
                    timeout_seconds,
                )
                backoff = BackoffStrategy(
                    base_delay=self.backoff_seconds,
                    max_delay=5.0,
                    multiplier=2.0,
                    jitter_mode=JitterMode.FULL,
                )
                fetch_with_retries = retry_with_backoff(
                    max_attempts=max(1, self.max_retries),
                    backoff=backoff,
                    retryable_exceptions=(Exception,),
                    reraise_last=True,
                )(
                    lambda: self._fetch_xquik(topic, db, limit, timeout_seconds, base_url, api_key)
                )
                try:
                    api_records = x_breaker.execute(fetch_with_retries)
                    api_succeeded = True
                    if api_records:
                        latency_ms = (time.perf_counter() - started) * 1000.0
                        ops_metrics.record_source_execution("x", success=True, latency_ms=latency_ms, item_count=len(api_records))
                        logger.info("[x] Staged %d records via Xquik for Topic ID %d", len(api_records), topic.id)
                        return api_records
                    logger.info("[x] Xquik returned no records; falling back to Nitter RSS")
                except Exception as exc:
                    api_error = exc
                    logger.warning("[x] Xquik request failed; falling back to Nitter RSS: %s", exc)
            elif api_key:
                logger.info("[x] Xquik circuit is open; trying Nitter RSS fallback")
            else:
                logger.info("[x] XQUIK_API_KEY is absent; using Nitter RSS fallback")

            if os.getenv("NITTER_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
                if api_succeeded:
                    ops_metrics.record_source_execution("x", success=True, latency_ms=(time.perf_counter() - started) * 1000.0, item_count=0)
                    return []
                if api_error:
                    latency_ms = (time.perf_counter() - started) * 1000.0
                    ops_metrics.record_source_execution(
                        "x",
                        success=False,
                        latency_ms=latency_ms,
                        is_timeout="timed out" in str(api_error).lower(),
                        error_summary=str(api_error)[:100],
                    )
                    return []
                logger.info("[x] Nitter fallback is disabled; X ingestion safely skipped")
                return []

            records = self._fetch_nitter(topic, db, limit, timeout_seconds)
            x_breaker.record_success()
            latency_ms = (time.perf_counter() - started) * 1000.0
            ops_metrics.record_source_execution("x", success=True, latency_ms=latency_ms, item_count=len(records))
            return records
        except Exception as exc:
            if not api_error and not api_succeeded:
                try:
                    x_breaker.record_failure(exc)
                except Exception:
                    logger.debug("[x] Could not update X circuit breaker after fallback failure", exc_info=True)
            latency_ms = (time.perf_counter() - started) * 1000.0
            is_timeout = isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in str(exc).lower()
            ops_metrics.record_source_execution(
                "x",
                success=False,
                latency_ms=latency_ms,
                is_timeout=is_timeout,
                error_summary=str(exc)[:100],
            )
            logger.error("[x] Fail-soft: all configured X sources failed for topic '%s': %s", topic.title, exc)
            return []
