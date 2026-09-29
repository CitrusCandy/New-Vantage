from abc import ABC, abstractmethod
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Set
import urllib.error
import urllib.request

from app.core.security import sanitize_url
from app.core.telemetry import ops_metrics
from app.llm.schemas import (
    PerspectiveItem,
    PerspectiveSynthesisOutput,
    SampleQuote,
)

logger = logging.getLogger("app.llm.perspective")


class BasePerspectiveSynthesizer(ABC):
    """Abstract interface for multi-perspective LLM synthesizers."""

    model_name: str

    @abstractmethod
    def synthesize(
        self,
        topic_title: str,
        cluster_payloads: List[Dict[str, Any]],
        total_sample_size: int,
    ) -> PerspectiveSynthesisOutput:
        """Synthesize multi-perspective analysis from representative cluster discourse items."""
        pass


class OpenAIPerspectiveSynthesizer(BasePerspectiveSynthesizer):
    """OpenAI-backed multi-perspective synthesizer supporting structured JSON output."""

    OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

    SYSTEM_PROMPT = """You are an objective, balanced, and insightful intelligence analyst for Vantage News.
Your mission is to analyze clustered public discourse from diverse sources (Google News, Reddit, X) on a specific given query/topic, identify distinct viewpoints, and synthesize a structured multi-perspective brief.

Instructions:
1. Identify 5 to 10 distinct, substantive perspectives or stances from the provided discourse clusters that DIRECTLY address the user's specific query. Aim for 10 distinct viewpoints when the discourse evidence is rich and multifaceted, and return at least 5 when the available evidence supports that many.
2. Ensure EVERY perspective directly addresses the core query topic. Do NOT output off-topic commentary, generic boilerplate, or unrelated subjects.
3. Represent diverse stakeholder angles (e.g. Economic/Commercial Opportunity, Regulatory & Policy Skepticism, Technical & Scientific Feasibility, Labor & Workforce Impacts, Consumer & Public Safety, Ethical & Environmental Considerations).
4. Do NOT output duplicate or near-duplicate perspectives. Each perspective must represent a genuinely distinct reasoning, viewpoint, or stakeholder concern.
5. For each perspective provide:
   - "title": A clear, specific, reader-friendly heading (6 to 12 words) summarizing the central viewpoint in plain language (e.g., "Focus on Clinical Workflow Gains and Diagnostic Turnaround Times", "Concerns Over Patient Data Privacy and Training Consent", "Questions Regarding Long-Term Infrastructure and Upfront Implementation Costs"). NEVER use vague labels like "Support", "Opposition", "Neutral", "Pro", "Con", or "Perspective 1" as the title.
   - "stance": A concise secondary stakeholder category label (e.g., "Clinical Proponents", "Privacy & Ethical Concerns", "Infrastructure & Cost Considerations", "Regulatory Oversight", "Consumer Sentiment").
   - "description": A meaningful, informative 2-4 sentence narrative explaining what people holding this perspective believe, the core reasons or concerns they emphasize, and how their position is situated relative to other perspectives on the topic. Use neutral framing ("Proponents emphasize...", "Critics argue that...", "Industry observers point to..."). Do not present any perspective as objectively correct.
   - "summary": A concise 1-2 sentence core takeaway.
   - "estimated_share": Estimated discourse share (0.0 to 1.0) based on cluster proportions.
   - "key_arguments": 2 to 4 concrete supporting arguments directly grounded in the cluster's text.
   - "sample_quotes": 1 to 3 representative verbatim quotes with authentic source and URL from the input data. When a cluster includes multiple platforms, include an X or Reddit quote when present so each platform's evidence can be traced in the result.
6. When the evidence is limited, sparse, or mixed, candidly describe the scope of evidence in "description" and "confidence_note". Never fabricate claims, sources, or quotes.
7. Output MUST be valid JSON adhering strictly to the required schema:
{
  "core_topic": "string",
  "perspectives": [
    {
      "title": "string",
      "stance": "string",
      "description": "string",
      "summary": "string",
      "estimated_share": 0.0,
      "key_arguments": ["string"],
      "sample_quotes": [
        {"text": "string", "source": "string", "url": "string"}
      ]
    }
  ],
  "confidence_note": "string"
}
"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ):
        self.api_key = (api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")).strip()
        self.model_name = (
            model_name
            or os.getenv("OPENAI_PERSPECTIVE_MODEL", "gpt-4o-mini")
        ).strip()
        self.timeout_seconds = timeout_seconds

    def synthesize(
        self,
        topic_title: str,
        cluster_payloads: List[Dict[str, Any]],
        total_sample_size: int,
    ) -> PerspectiveSynthesisOutput:
        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set. Please configure the OPENAI_API_KEY environment variable."
            )

        user_content = self._format_prompt_input(topic_title, cluster_payloads, total_sample_size)

        payload = {
            "model": self.model_name,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.2,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.OPENAI_CHAT_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        from app.core.resilience import BackoffStrategy, JitterMode, openai_breaker, retry_with_backoff

        def _do_openai_call():
            t_start = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    choice = resp_data["choices"][0]["message"]["content"]
                    parsed_json = json.loads(choice)
                    raw_output = PerspectiveSynthesisOutput.model_validate(parsed_json)
                    # Post-generation validation, deduplication, and URL sanitization
                    validated_output = validate_and_sanitize_perspectives(
                        raw_output=raw_output,
                        query=topic_title,
                        cluster_payloads=cluster_payloads,
                    )
                    latency_ms = (time.perf_counter() - t_start) * 1000.0
                    ops_metrics.record_source_execution("openai", success=True, latency_ms=latency_ms)
                    return validated_output
            except urllib.error.HTTPError as e:
                latency_ms = (time.perf_counter() - t_start) * 1000.0
                err_body = e.read().decode("utf-8", errors="ignore")
                ops_metrics.record_source_execution("openai", success=False, latency_ms=latency_ms, error_summary=f"HTTP {e.code}")
                logger.error("OpenAI Chat Completion error HTTP %d: %s", e.code, err_body)
                raise RuntimeError(f"OpenAI Perspective API error HTTP {e.code}: {err_body}") from e
            except urllib.error.URLError as e:
                latency_ms = (time.perf_counter() - t_start) * 1000.0
                is_to = "timed out" in str(e).lower()
                ops_metrics.record_source_execution("openai", success=False, latency_ms=latency_ms, is_timeout=is_to, error_summary=str(e)[:100])
                logger.error("OpenAI connection failed: %s", str(e))
                raise RuntimeError(f"OpenAI connection error: {str(e)}") from e
            except json.JSONDecodeError as e:
                latency_ms = (time.perf_counter() - t_start) * 1000.0
                ops_metrics.record_source_execution("openai", success=False, latency_ms=latency_ms, error_summary="Malformed JSON")
                logger.error("Failed to parse JSON response from LLM: %s", str(e))
                raise RuntimeError(f"Malformed JSON from LLM: {str(e)}") from e

        backoff = BackoffStrategy(base_delay=1.0, max_delay=8.0, multiplier=2.0, jitter_mode=JitterMode.FULL)
        call_with_retries = retry_with_backoff(
            max_attempts=3,
            backoff=backoff,
            retryable_exceptions=(RuntimeError, TimeoutError, OSError),
            reraise_last=True,
        )(_do_openai_call)

        return openai_breaker.execute(call_with_retries)

    @staticmethod
    def _format_prompt_input(
        topic_title: str,
        cluster_payloads: List[Dict[str, Any]],
        total_sample_size: int,
    ) -> str:
        lines = [
            f"User Query / Topic: {topic_title}",
            f"Total Analyzed Samples: {total_sample_size}",
            f"Target: Synthesize 5 to 10 distinct, substantive perspectives that directly address '{topic_title}'.",
            f"Discourse Clusters ({len(cluster_payloads)} total):",
            "",
        ]

        for c_idx, cluster in enumerate(cluster_payloads, start=1):
            c_id = cluster.get("cluster_id", c_idx)
            size = cluster.get("size", 0)
            share = cluster.get("share", round(size / max(total_sample_size, 1), 4))
            samples = cluster.get("representative_samples", [])

            lines.append(f"--- Cluster #{c_id} (Size: {size}, Estimated Share: {share * 100:.1f}%) ---")
            for s_idx, sample in enumerate(samples, start=1):
                src = sample.get("source", "unknown")
                author = sample.get("author_handle", "anonymous")
                url = sample.get("url", "N/A")
                text = sample.get("text_content", "").strip()
                lines.append(f"  [{s_idx}] Source: {src} | Author: {author} | URL: {url}")
                lines.append(f"      \"{text}\"")
            lines.append("")

        return "\n".join(lines)


VAGUE_TITLES: Set[str] = {
    "support", "opposition", "neutral", "pro", "con", "positive", "negative",
    "perspective 1", "perspective 2", "perspective 3", "perspective 4", "perspective 5",
    "perspective group 1", "perspective group 2", "perspective group 3",
    "group 1", "group 2", "group 3", "group 4", "group 5",
    "general", "other", "stance", "viewpoint", "overview",
}


def _clean_title(raw_title: Optional[str], fallback_summary: str, query: str) -> str:
    """Ensure every perspective has a specific, informative, reader-friendly title."""
    if raw_title and raw_title.strip():
        clean = raw_title.strip()
        # Strip generic prefixes like "Perspective: " or "Topic - "
        clean = re.sub(r"^(perspective|stance|viewpoint)\s*[\d:#-]*\s*", "", clean, flags=re.IGNORECASE)
        clean = clean.strip().rstrip(".:")
        if clean.lower() not in VAGUE_TITLES and len(clean) >= 8:
            return clean

    # Synthesize title from first sentence of summary or key arguments
    if fallback_summary:
        first_sentence = fallback_summary.split(".")[0].strip()
        first_sentence = re.sub(r"^[\"\']|[\"\']$", "", first_sentence).strip()
        if 10 <= len(first_sentence) <= 85:
            return first_sentence
        elif len(first_sentence) > 85:
            words = first_sentence.split()
            return " ".join(words[:10]) + "..."

    return f"Key Perspectives and Discourse on {query}"


def validate_and_sanitize_perspectives(
    raw_output: PerspectiveSynthesisOutput,
    query: str,
    cluster_payloads: Optional[List[Dict[str, Any]]] = None,
) -> PerspectiveSynthesisOutput:
    """Validate query relevance, deduplicate near-identical perspectives, eliminate vague headings, and sanitize URLs."""
    from app.processing.relevance import calculate_query_relevance

    validated_perspectives: List[PerspectiveItem] = []
    seen_titles: Set[str] = set()

    for p in raw_output.perspectives:
        # 1. Sanitize quote URLs
        for q in p.sample_quotes:
            q.url = sanitize_url(q.url)
            source = (q.source or "").strip().lower().replace("_", " ").replace("-", " ")
            url = (q.url or "").lower()
            if (
                source == "x" or "twitter" in source or "nitter" in source or
                "tw1tter" in source or "x.com/" in url or "twitter.com/" in url or
                "nitter" in url or "tw1tter" in url
            ):
                q.source = "x"
            elif "reddit" in source or "reddit.com/" in url:
                q.source = "reddit"
            elif source == "news" or "google" in source or "rss" in source:
                q.source = "google_news"

        # 2. Derive & clean title, stance, description
        cand_title = p.title or p.heading or p.type
        clean_title_str = _clean_title(cand_title, p.summary, query)
        p.title = clean_title_str
        p.heading = clean_title_str

        # Clean stance category
        if not p.stance:
            p.stance = p.type if (p.type and p.type.lower() not in VAGUE_TITLES) else "Stakeholder Perspective"
        p.type = p.title

        # Meaningful contextual description
        if not p.description or len(p.description.strip()) < 15:
            p.description = p.summary or f"Observed public discourse viewpoint regarding {query}."

        # 3. Deduplicate exact or near-identical perspective titles
        norm_title = p.title.lower().strip()
        if norm_title in seen_titles:
            logger.info("Dropping duplicate perspective title: '%s'", p.title)
            continue
        seen_titles.add(norm_title)

        # 4. Validate query relevance
        comb_text = f"{p.title} {p.stance} {p.description} {p.summary} {' '.join(p.key_arguments)}"
        score, is_rel = calculate_query_relevance(query=query, text=comb_text)
        if not is_rel:
            logger.warning("Dropping off-query perspective: '%s' (score=%.2f)", p.title, score)
            continue

        validated_perspectives.append(p)

    # Normalize estimated_share if perspectives exist
    if validated_perspectives:
        total_share = sum(p.estimated_share for p in validated_perspectives)
        if total_share > 0:
            for p in validated_perspectives:
                p.estimated_share = round(p.estimated_share / total_share, 4)

    confidence_note = raw_output.confidence_note
    if not validated_perspectives:
        confidence_note = f"Limited evidence: No sufficiently relevant perspectives could be validated for query '{query}'."

    return PerspectiveSynthesisOutput(
        core_topic=raw_output.core_topic or query,
        perspectives=validated_perspectives,
        confidence_note=confidence_note,
    )


class MockPerspectiveSynthesizer(BasePerspectiveSynthesizer):
    """Deterministic offline mock synthesizer extracting grounded, reader-friendly perspectives from input samples."""

    model_name = "mock-perspective-synthesizer-v2"

    def synthesize(
        self,
        topic_title: str,
        cluster_payloads: List[Dict[str, Any]],
        total_sample_size: int,
    ) -> PerspectiveSynthesisOutput:
        perspectives: List[PerspectiveItem] = []

        all_samples: List[Dict[str, Any]] = []
        for cluster in cluster_payloads:
            all_samples.extend(cluster.get("representative_samples", []))

        if not cluster_payloads or not all_samples or total_sample_size < 2:
            return PerspectiveSynthesisOutput(
                core_topic=topic_title,
                perspectives=[],
                confidence_note=f"Limited evidence: Only {total_sample_size} sources available for topic '{topic_title}'. Insufficient grounded discourse to synthesize perspectives.",
            )

        # Thematic stance angle archetypes to assign contextual flavor when clustering
        angle_archetypes = [
            ("Advocacy & Economic Opportunity", "Proponents highlight commercial potential, operational efficiency, and rapid technological advancement."),
            ("Regulatory & Safety Skepticism", "Critics and policy observers emphasize regulatory compliance, risk mitigation, and oversight requirements."),
            ("Technical Feasibility & Implementation", "Practitioners focus on infrastructure readiness, integration complexity, and deployment hurdles."),
            ("Workforce & Public Impact", "Industry participants and commentators examine labor transitions, consumer trust, and societal effects."),
            ("Cost & Resource Allocation", "Financial analysts and stakeholders weigh high initial capital expenditure against projected long-term ROI."),
        ]

        for idx, cluster in enumerate(cluster_payloads):
            c_id = cluster.get("cluster_id", idx + 1)
            size = cluster.get("size", 1)
            share = cluster.get("share", round(size / max(total_sample_size, 1), 4))
            samples = cluster.get("representative_samples", [])

            primary_sample = samples[0] if samples else {}
            sample_text = primary_sample.get("text_content", "").strip()
            sample_src = primary_sample.get("source", "news")
            sample_url = sanitize_url(primary_sample.get("url"))

            archetype_idx = idx % len(angle_archetypes)
            stance_label, context_theme = angle_archetypes[archetype_idx]

            # Derive reader-friendly title from cluster content
            heading = f"Perspective on {topic_title} Implementation and Impact"
            if sample_text:
                first_sent = sample_text.split(".")[0].strip()
                first_sent = re.sub(r"[^\w\s-]", "", first_sent).strip()
                if 12 <= len(first_sent) <= 75:
                    heading = first_sent
                elif len(first_sent) > 75:
                    words = first_sent.split()
                    heading = " ".join(words[:9])
                else:
                    heading = f"{stance_label}: {topic_title}"

            quotes: List[SampleQuote] = []
            for s in samples[:2]:
                text = s.get("text_content", "").strip()
                if text:
                    quotes.append(
                        SampleQuote(
                            text=text[:160] + ("..." if len(text) > 160 else ""),
                            source=s.get("source", "news"),
                            url=sanitize_url(s.get("url")),
                        )
                    )

            if not quotes and sample_text:
                quotes.append(
                    SampleQuote(
                        text=sample_text[:160] + ("..." if len(sample_text) > 160 else ""),
                        source=sample_src,
                        url=sample_url,
                    )
                )

            summary_text = (
                f"Discourse analysis for {topic_title} representing approximately {round(share * 100, 1)}% of sampled discussions. "
                f"{context_theme}"
            )
            if sample_text:
                summary_text += f" Source observation: \"{sample_text[:100]}...\""

            description_text = (
                f"Participants representing this viewpoint emphasize {stance_label.lower()} regarding {topic_title}. "
                f"Drawing upon {size} verified records across {sample_src.upper()}, this perspective highlights practical considerations "
                f"and distinguishes itself by focusing on concrete evidence and stakeholder impacts."
            )

            key_arguments = [
                f"Viewpoint established from {size} recorded items in cluster #{c_id}.",
                f"Directly addresses {topic_title} with focus on {stance_label.lower()}.",
                f"Grounded in verified {sample_src} reporting and discussion.",
            ]

            perspectives.append(
                PerspectiveItem(
                    title=heading,
                    heading=heading,
                    type=heading,
                    stance=stance_label,
                    description=description_text,
                    summary=summary_text,
                    estimated_share=share,
                    key_arguments=key_arguments,
                    sample_quotes=quotes,
                )
            )

        # Normalize shares to sum to 1.0
        total_s = sum(p.estimated_share for p in perspectives)
        if total_s > 0:
            for p in perspectives:
                p.estimated_share = round(p.estimated_share / total_s, 4)

        return PerspectiveSynthesisOutput(
            core_topic=topic_title,
            perspectives=perspectives,
            confidence_note=(
                f"Synthesized {len(perspectives)} distinct perspectives across {len(cluster_payloads)} clusters "
                f"from {total_sample_size} verified discourse records."
            ),
        )


def get_perspective_synthesizer(
    provider_type: Optional[str] = None,
) -> BasePerspectiveSynthesizer:
    """Factory for active LLM perspective synthesizer."""
    ptype = (provider_type or os.getenv("LLM_PROVIDER", "")).lower()
    openai_key = os.getenv("OPENAI_API_KEY", "")
    from app.processing.embeddings import is_valid_openai_key
    if ptype == "mock" or not is_valid_openai_key(openai_key) or (ptype != "openai" and not is_valid_openai_key(openai_key)):
        return MockPerspectiveSynthesizer()
    return OpenAIPerspectiveSynthesizer(api_key=openai_key)

