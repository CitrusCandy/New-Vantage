from abc import ABC, abstractmethod
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
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
4. Do NOT output duplicate or near-duplicate perspectives (e.g. paraphrased versions of the same argument). Each perspective must represent a genuinely distinct reasoning, viewpoint, or stakeholder concern.
5. For each perspective:
   - Provide a clear, neutral 'type' name (e.g. 'Enterprise Productivity Proponents', 'Privacy & Surveillance Concerns', 'Open Standards Advocates').
   - Estimate the discourse share (0.0 to 1.0 or percentage) based on cluster proportions.
   - Write a concise narrative summary directly addressing the query.
   - List 2 to 4 concrete key arguments.
   - Provide 1 to 3 representative sample quotes verbatim or near-verbatim, strictly attributing the correct source and URL from the input data.
6. When the evidence is limited or sparse, return only the distinct viewpoints genuinely supported by the gathered sources, and explicitly state in 'confidence_note' that evidence coverage is limited. Never fabricate claims, sources, or quotes.
7. Output MUST be valid JSON adhering strictly to the required schema:
{
  "core_topic": "string",
  "perspectives": [
    {
      "type": "string",
      "estimated_share": 0.0,
      "summary": "string",
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


def validate_and_sanitize_perspectives(
    raw_output: PerspectiveSynthesisOutput,
    query: str,
    cluster_payloads: Optional[List[Dict[str, Any]]] = None,
) -> PerspectiveSynthesisOutput:
    """Validate query relevance, deduplicate near-identical perspectives, and sanitize URLs."""
    from app.processing.relevance import calculate_query_relevance

    validated_perspectives: List[PerspectiveItem] = []
    seen_types: Set[str] = set()

    for p in raw_output.perspectives:
        # 1. Sanitize quote URLs
        for q in p.sample_quotes:
            q.url = sanitize_url(q.url)

        # 2. Deduplicate exact or near-identical perspective types
        norm_type = p.type.lower().strip()
        if norm_type in seen_types:
            logger.info("Dropping duplicate perspective type: '%s'", p.type)
            continue
        seen_types.add(norm_type)

        # 3. Validate query relevance
        comb_text = f"{p.type} {p.summary} {' '.join(p.key_arguments)}"
        score, is_rel = calculate_query_relevance(query=query, text=comb_text)
        if not is_rel:
            logger.warning("Dropping off-query perspective: '%s' (score=%.2f)", p.type, score)
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
    """Deterministic offline mock synthesizer extracting grounded perspectives from input samples."""

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

        for cluster in cluster_payloads:
            c_id = cluster.get("cluster_id", len(perspectives) + 1)
            size = cluster.get("size", 1)
            share = cluster.get("share", round(size / max(total_sample_size, 1), 4))
            samples = cluster.get("representative_samples", [])

            primary_sample = samples[0] if samples else {}
            sample_text = primary_sample.get("text_content", "").strip()
            sample_src = primary_sample.get("source", "news")
            sample_url = sanitize_url(primary_sample.get("url"))

            # Derive perspective type from cluster index and sample content
            type_label = f"{topic_title} - Perspective Group {c_id}"
            if sample_text:
                first_sentence = sample_text.split(".")[0].strip()
                if 5 < len(first_sentence) <= 60:
                    type_label = f"{topic_title}: {first_sentence}"

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

            summary = f"Discourse analysis for {topic_title} representing {round(share * 100, 1)}% of observed viewpoints."
            if sample_text:
                summary += f" Key finding: '{sample_text[:100]}...'"

            perspectives.append(
                PerspectiveItem(
                    type=type_label,
                    estimated_share=share,
                    summary=summary,
                    key_arguments=[
                        f"Discourse viewpoint representing {size} recorded items in cluster #{c_id}.",
                        f"Grounded in verified {sample_src} reporting and discussion.",
                    ],
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
    if ptype == "mock" or (not ptype and not os.getenv("OPENAI_API_KEY")):
        return MockPerspectiveSynthesizer()
    return OpenAIPerspectiveSynthesizer()

