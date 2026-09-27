"""Query relevance filtering and semantic evaluation utilities for VantageNews."""

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import unicodedata

logger = logging.getLogger("app.processing.relevance")

# Common English stopwords to ignore when computing content token overlap
STOPWORDS: Set[str] = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't",
    "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i",
    "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's",
    "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought",
    "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she",
    "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're",
    "they've", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves", "vs", "versus", "news", "update", "latest", "discussion"
}

# Domain acronym and synonym anchor map for robust paraphrase & entity matching
ALIAS_MAP: Dict[str, Set[str]] = {
    "ai": {"artificial intelligence", "machine learning", "deep learning", "agentic", "agent", "llm", "llms"},
    "artificial intelligence": {"ai", "machine learning", "deep learning", "agentic", "llm"},
    "ev": {"electric vehicle", "electric vehicles", "evs", "battery electric", "tesla", "solid state battery", "solid-state battery"},
    "evs": {"electric vehicle", "electric vehicles", "ev", "battery electric", "tesla"},
    "electric vehicle": {"ev", "evs", "electric vehicles", "battery electric", "solid-state battery"},
    "electric vehicles": {"ev", "evs", "electric vehicle", "battery electric", "solid-state battery"},
    "btc": {"bitcoin", "crypto", "cryptocurrency", "satoshi"},
    "bitcoin": {"btc", "crypto", "cryptocurrency", "satoshi"},
    "crypto": {"cryptocurrency", "bitcoin", "btc", "ethereum", "eth", "web3"},
    "cryptocurrency": {"crypto", "bitcoin", "btc", "ethereum", "eth", "blockchain"},
    "cbdc": {"central bank digital currency", "digital currency", "digital rupee", "digital dollar", "digital euro"},
    "eu": {"european union", "europe", "brussels"},
    "european union": {"eu", "europe", "brussels"},
    "global warming": {"climate change", "greenhouse gases", "carbon emissions", "climate crisis", "rising temperatures", "net zero"},
    "climate change": {"global warming", "carbon emissions", "greenhouse gas", "net zero", "climate crisis", "rising temperatures"},
    "llm": {"large language model", "large language models", "gpt", "foundation model", "ai", "claude"},
    "large language models": {"llm", "llms", "gpt", "foundation model", "ai"},
    "nuclear": {"atomic", "fission", "fusion", "smr", "reactor", "uranium"},
    "quantum": {"qubit", "qubits", "superconducting", "quantum computing", "quantum computer", "transmon", "trapped ion", "rydberg"},
    "quantum computing": {"quantum", "qubit", "qubits", "superconducting", "transmon", "trapped ion", "rydberg"},
    "next gen": {"next-gen", "future", "advanced", "emerging"},
}


def normalize_stem(token: str) -> str:
    """Normalize common English grammatical suffixes to base stems."""
    w = token.lower().strip()
    if len(w) <= 3:
        return w
    
    suffixes = (
        "ization", "isation", "ational", "ation", "ities", "ility",
        "ments", "ment", "ative", "tive", "ness", "able", "ible",
        "ting", "ling", "ring", "ning", "ming", "sing", "ing",
        "ers", "ors", "ies", "ied", "ive", "ted", "led", "red",
        "ned", "med", "sed", "ped", "ded", "ked", "ed", "es",
        "er", "or", "al", "ic", "ly", "s"
    )
    for suffix in suffixes:
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[:-len(suffix)]
    return w


def extract_content_tokens(text: str) -> List[str]:
    """Extract significant lowercased word tokens from text, removing punctuation and stopwords."""
    if not text:
        return []
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    clean = re.sub(r"[^\w\s-]", " ", normalized.lower())
    clean = re.sub(r"[-_]+", " ", clean)
    tokens = [t.strip() for t in clean.split() if len(t.strip()) > 1]
    return [t for t in tokens if t not in STOPWORDS]


def _token_matches(q_token: str, d_token: str) -> bool:
    """Check if query token matches document token via exact equality, stem equality, or prefix."""
    if q_token == d_token:
        return True
    
    # Check normalized stems
    q_stem = normalize_stem(q_token)
    d_stem = normalize_stem(d_token)
    if q_stem == d_stem:
        return True
    if len(min(q_stem, d_stem)) >= 4 and (q_stem.startswith(d_stem) or d_stem.startswith(q_stem)):
        return True
            
    # Check common prefix for longer words (e.g. regulation vs regulatory)
    min_len = min(len(q_token), len(d_token))
    if min_len >= 5:
        common_len = 0
        for i in range(min_len):
            if q_token[i] == d_token[i]:
                common_len += 1
            else:
                break
        if common_len >= 5 and (common_len / min_len) >= 0.70:
            return True
            
    return False


def _has_word_or_phrase(pattern: str, text: str) -> bool:
    """Check if pattern exists as a whole word or whole phrase in text."""
    escaped = re.escape(pattern)
    return bool(re.search(rf"\b{escaped}\b", text, flags=re.IGNORECASE))


def calculate_query_relevance(
    query: str,
    text: str,
    title: Optional[str] = None,
) -> Tuple[float, bool]:
    """
    Calculate query-to-document relevance score (0.0 to 1.0) and boolean decision.
    
    Evaluates:
    1. Exact query phrase containment.
    2. Acronym & entity alias containment (e.g. EV, AI, BTC, CBDC, EU) with word boundaries.
    3. Content token recall with stemming & morphological variation.
    4. Guard against keyword-stuffed noise with low substantive overlap.
    
    Returns:
        (score, is_relevant)
    """
    if not query or not query.strip():
        return 1.0, True

    if not text and not title:
        return 0.0, False

    query_norm = query.lower().strip()
    full_text = f"{title or ''} {text or ''}".lower().strip()

    # 1. Exact query phrase match: 1.0
    if _has_word_or_phrase(query_norm, full_text):
        return 1.0, True

    # 2. Extract significant content tokens
    query_tokens = extract_content_tokens(query)
    if not query_tokens:
        query_tokens = [t.lower().strip() for t in query.split() if len(t.strip()) > 0]

    if not query_tokens:
        return 1.0, True

    doc_tokens = extract_content_tokens(full_text)
    if not doc_tokens:
        doc_tokens = full_text.split()

    doc_token_set = set(doc_tokens)

    # 3. Check acronym / entity alias match with word boundaries
    for alias_key, expansions in ALIAS_MAP.items():
        if _has_word_or_phrase(alias_key, query_norm):
            for exp in expansions:
                if _has_word_or_phrase(exp, full_text):
                    return 0.95, True
        elif any(_has_word_or_phrase(exp, query_norm) for exp in expansions):
            if _has_word_or_phrase(alias_key, full_text) or any(_has_word_or_phrase(exp, full_text) for exp in expansions):
                return 0.90, True

    # 4. Token recall computation
    matched_tokens: List[str] = []
    for q in query_tokens:
        matched = False
        # Direct set lookup
        if q in doc_token_set:
            matched_tokens.append(q)
            continue
        # Stem and prefix comparison
        for d in doc_token_set:
            if _token_matches(q, d):
                matched = True
                matched_tokens.append(q)
                break
        if not matched and q in ALIAS_MAP:
            if any(_has_word_or_phrase(exp, full_text) for exp in ALIAS_MAP[q]):
                matched_tokens.append(q)

    token_recall = len(matched_tokens) / float(len(query_tokens))

    # Single-token query: must match
    # Multi-token query: require at least 1 content token for short queries (<=4 content tokens)
    min_required_tokens = 1 if len(query_tokens) <= 4 else 2
    min_recall = max(0.20, (1.0 / len(query_tokens)) - 0.05)
    is_rel = len(matched_tokens) >= min_required_tokens and token_recall >= min_recall

    return round(token_recall, 3), is_rel


def filter_relevant_items(
    query: str,
    items: List[Any],
    text_extractor: Callable[[Any], str] = lambda x: getattr(x, "text_content", ""),
    title_extractor: Callable[[Any], Optional[str]] = lambda x: getattr(x, "title", None) if hasattr(x, "title") else None,
    min_score_threshold: float = 0.25,
) -> Tuple[List[Any], int]:
    """
    Filter a collection of raw discourse items against the user query.
    
    Returns:
        (relevant_items, filtered_out_count)
    """
    relevant = []
    filtered_count = 0

    for item in items:
        text = text_extractor(item)
        title = title_extractor(item)
        score, is_rel = calculate_query_relevance(query=query, text=text, title=title)
        if is_rel and score >= min_score_threshold:
            relevant.append(item)
        else:
            filtered_count += 1

    return relevant, filtered_count
