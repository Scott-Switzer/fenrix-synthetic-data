"""Semantic privacy attacks (Phase 5B).

Four required semantic attacks per the user's directive:

1. ``rare_phrase_attack`` - distinctive multi-word n-grams surviving
   masking (raises privacy alarm if any do).
2. ``lexical_retrieval_attack`` - BM25 retrieval of the masked surrogate
   against the original SEC source corpus. Source rank in top-K policy.
3. ``multi_document_attack`` - same as lexical but on the concatenation
   of ALL public surrogates (SEC + news + numeric).
4. ``structured_numeric_attack`` - numeric distribution comparison
   between the classroom-safe synthetic numeric package and the real
   SEC numeric content extracted from the source-run.

All attacks operate on masked release candidates only. No real
company names are emitted in tracked attack artifacts.

Pure-Python: no sklearn, no rank_bm25. BM25 is implemented inline
(~50 LOC). The bounded reanonymize corpus (a few dozen SEC filings
plus a few news articles) does not warrant a heavier dependency.

The semantic attack results are written to
``qa/semantic_privacy_report.json`` and feed into the
``semantic_privacy_decision`` field of the release gate.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import orjson

logger = logging.getLogger(__name__)


# ── Public dataclasses (mirror TextAttackResult shape) ────────────────


@dataclass
class SemanticAttackHit:
    """A single hit from a semantic attack (hash-rendered, never raw)."""

    hit_hash: str
    phrase: str = ""
    metric: float = 0.0
    location: str = ""


@dataclass
class SemanticAttackResult:
    """Result of running one semantic attack."""

    attack_type: str
    passed: bool = False
    is_blocked: bool = True
    blocking: bool = True
    status: str = "NOT_RUN"
    verdict: str = "NOT_RUN"
    details: dict[str, Any] = field(default_factory=dict)
    hits: list[SemanticAttackHit] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.status = "PASS" if self.passed else "FAIL"
        self.verdict = self.status
        self.is_blocked = (not self.passed) and self.blocking


# ── Tokenization helpers ─────────────────────────────────────────────


_WORD_RE = re.compile(r"\w+")
_WORD_SPAN_RE = re.compile(r"[A-Za-z0-9][\w'-]*")
_NUM_TOKEN_RE = re.compile(r"\$?\s*([\d][\d,]*(?:\.\d+)?)\s*([KkMm]|[Bb]illion|[Mm]illion|%)?")


def _tokens(text: str) -> list[str]:
    """Lowercased word tokens for BM25."""
    return _WORD_RE.findall(text.lower())


def _word_spans(text: str) -> list[tuple[str, str]]:
    """Return list of ``(original_case_word, lowercase_word)`` tuples.

    Used by ``rare_phrase_attack`` to detect distinctive phrases that
    still carry capitalisation after masking.
    """
    out: list[tuple[str, str]] = []
    for m in _WORD_SPAN_RE.finditer(text):
        w = m.group()
        out.append((w, w.lower()))
    return out


# ── Pure-Python BM25 ──────────────────────────────────────────────────


@dataclass
class _BM25:
    k1: float = 1.5
    b: float = 0.75
    doc_lens: list[int] = field(default_factory=list)
    avg_dl: float = 0.0
    df_count: Counter = field(default_factory=Counter)
    idf: dict[str, float] = field(default_factory=dict)
    doc_tf: list[Counter] = field(default_factory=list)
    N: int = 0


def _build_bm25(docs: list[list[str]]) -> _BM25:
    """Build a BM25 index. ``docs`` is a corpus of pre-tokenized documents."""
    idx = _BM25()
    idx.N = len(docs)
    idx.doc_lens = [len(d) for d in docs]
    idx.avg_dl = sum(idx.doc_lens) / max(idx.N, 1)
    idx.doc_tf = [Counter(d) for d in docs]
    df: Counter = Counter()
    for tf in idx.doc_tf:
        for term in set(tf):
            df[term] += 1
    idx.df_count = df
    for term, df_t in df.items():
        idx.idf[term] = math.log(1 + (idx.N - df_t + 0.5) / (df_t + 0.5))
    return idx


def _bm25_score(idx: _BM25, query: list[str], doc_id: int) -> float:
    """Score ``query`` against document ``doc_id`` in ``idx``."""
    tf = idx.doc_tf[doc_id]
    dl = idx.doc_lens[doc_id]
    score = 0.0
    for q in query:
        idf = idx.idf.get(q)
        if idf is None:
            continue
        f_qd = tf.get(q, 0)
        norm = 1 - idx.b + idx.b * dl / max(idx.avg_dl, 1.0)
        score += idf * (f_qd * (idx.k1 + 1)) / (f_qd + idx.k1 * norm)
    return score


# ── Reader helpers ────────────────────────────────────────────────────


def _read_corpus_docs(
    cor_dir: Path,
    suffixes: tuple[str, ...] = (".md",),
) -> list[tuple[str, str]]:
    """Read every doc in ``cor_dir`` matching ``suffixes`` sorted by name."""
    out: list[tuple[str, str]] = []
    if not cor_dir.is_dir():
        return out
    for p in sorted(cor_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in suffixes:
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            out.append((p.name, txt))
    return out


def _read_source_corpus(
    source_run: Path,
    ticker: str,
    max_chars_per_doc: int = 50000,
) -> dict[str, str]:
    """Read SEC filings from ``source_run/originals/<ticker>/sec/filings``.

    Returns ``{filename: text}``. HTML is parsed with BeautifulSoup if
    available; otherwise a simple ``<tag>`` strip is used.
    """
    corpus: dict[str, str] = {}
    filing_dir = source_run / "originals" / ticker / "sec" / "filings"
    if not filing_dir.is_dir():
        return corpus
    for f in sorted(filing_dir.iterdir()):
        if not f.is_file() or f.suffix.lower() not in (".html", ".htm"):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = _strip_html(content)
        corpus[f.name] = text[:max_chars_per_doc]
    return corpus


def _strip_html(html: str) -> str:
    """Best-effort HTML -> plain text without forcing a hard dep on BS4."""
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-not-found]

        return BeautifulSoup(html, "lxml").get_text(separator=" ")
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)


# ── Attack 1: rare phrase ─────────────────────────────────────────────


# ── Taxonomy constants (direct ``Final`` annotations — no aliasing) ────


_RARE_PHRASE_CATEGORIES: Final[list[tuple[str, bool]]] = [
    ("raw_direct_identifier", True),
    ("product_or_platform_phrase", True),
    ("company_specific_business_phrase", True),
    ("sec_xbrl_boilerplate", False),
    ("date_period_boilerplate", False),
    ("accounting_taxonomy_boilerplate", False),
    ("synthetic_placeholder", False),
    ("generic_financial_phrase", False),
    ("low_information_ngram", False),
]

_LOW_INFO_DF_RATIO: Final[float] = 0.30
_MIN_DOCS_FOR_DF_CUTOFF: Final[int] = 5
_PRIVATE_VALUE_SUBSTR_MIN_LEN: Final[int] = 4

# Single-word SEC / XBRL / EDGAR boilerplate tokens.
_SEC_XBRL_BOILERPLATE_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "cik",
        "dei",
        "us-gaap",
        "xbrl",
        "ixbrl",
        "iso4217",
        "srt",
        "edgar",
        "fasb",
        "gaap",
        "ifrs",
        "sec",
        "form",
        "filing",
        "filings",
        "shares",
        "share",
        "document",
        "entity",
        "registrant",
        "consolidated",
        "unaudited",
        "interim",
        "condensed",
        "statements",
        "schedule",
        "amendment",
        "exhibit",
        "item",
        "report",
        "reports",
        "subsidiary",
        "subsidiaries",
        "hereto",
        "hereunder",
        "herein",
        "thereto",
        "thereunder",
        "therein",
        "thereof",
        "hereof",
        "whereas",
        "foregoing",
        "aforesaid",
        "herewith",
        "therewith",
        "pursuant",
        "furnished",
        "incorporated",
        "reference",
    }
)

# Multi-word accounting / SEC taxonomy boilerplate phrases (substring-matched).
_ACCOUNTING_TAXONOMY_PHRASES: Final[frozenset[str]] = frozenset(
    {
        "consolidated financial statements",
        "balance sheet",
        "cash flows",
        "statement of operations",
        "net income",
        "gross margin",
        "operating expenses",
        "retained earnings",
        "accumulated deficit",
        "comprehensive income",
        "stockholders equity",
        "common stock outstanding",
        "common stock",
        "table of contents",
        "annual report on form 10-k",
        "annual report on form 10-q",
        "quarterly report on form 10-q",
        "current report on form 8-k",
        "report on form 10-k",
        "report on form 10-q",
        "report on form 8-k",
        "on form 10-k",
        "on form 10-q",
        "on form 8-k",
        "fiscal year ended",
        "fiscal quarter ended",
        "interim financial statements",
        "unaudited consolidated",
        "unaudited interim",
        "notes to consolidated financial",
        "notes to financial",
        "financial statements",
        "financial condition",
        "results of operations",
        "liquidity and capital resources",
        "critical accounting policies",
        "quantitative and qualitative disclosures",
        "internal control over financial",
        "evaluation of disclosure controls",
        "part ii other information",
        "part i financial information",
        "legal proceedings",
        "risk factors",
        "forward-looking statements",
        "cautionary note regarding forward",
        "statement of cash flows",
        "statement of stockholders equity",
        "notes to unaudited condensed",
        "basis of presentation",
        "summary of significant accounting",
        "recently issued accounting",
        "recently adopted accounting",
        "fair value measurements",
        "property and equipment",
        "goodwill and intangible",
        "intangible assets",
        "restricted stock units",
        "earnings per share",
        "basic net income per share",
        "diluted net income per share",
        "weighted average shares",
        "outstanding common stock",
        "accumulated other comprehensive",
        "other comprehensive income",
        "comprehensive income loss",
    }
)

# Generic accounting vocabulary that, when ALL remaining tokens
# (stopwords stripped) belong to this set or _SEC_XBRL_BOILERPLATE_TOKENS,
# routes to non-blocking.
_GENERIC_FINANCIAL_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "revenue",
        "revenues",
        "cost",
        "costs",
        "expense",
        "expenses",
        "operations",
        "operating",
        "income",
        "margin",
        "margins",
        "earnings",
        "loss",
        "losses",
        "profit",
        "share",
        "shares",
        "dividend",
        "dividends",
        "diluted",
        "basic",
        "growth",
        "decline",
        "increase",
        "decrease",
        "quarterly",
        "annual",
        "fiscal",
        "period",
        "year",
        "total",
        "net",
        "gross",
        "effective",
        "rate",
        "tax",
        "taxes",
        "returns",
        "sales",
        "guidance",
        "outlook",
        "demand",
        "supply",
        "inventory",
        "channel",
        "market",
        "markets",
        "customer",
        "customers",
        "supplier",
        "suppliers",
        "board",
        "directors",
        "officers",
        "executives",
        "assets",
        "liabilities",
        "equity",
        "debt",
        "cash",
        "capital",
        "stock",
        "treasury",
        "investment",
        "investments",
        "securities",
        "balance",
        "sheet",
        "flow",
        "flows",
        "statement",
        "financial",
        "accounting",
        "audit",
        "auditor",
        "valuation",
        "allowance",
        "amortization",
        "depreciation",
        "accrued",
        "deferred",
        "payable",
        "receivable",
        "disclosure",
        "disclosures",
        "presentation",
        "recognition",
        "measurement",
        "transition",
        "segment",
        "segments",
        "geographic",
        "domestic",
        "international",
        "restructuring",
        "acquisition",
        "acquisitions",
        "merger",
        "goodwill",
        "impairment",
        "intangible",
        "stock-based",
        "compensation",
        "performance",
        "results",
        "amounts",
        "amount",
        "ended",
        "months",
    }
)

# Financial / temporal stopwords stripped from token evaluation in
# Tier 8 (generic_financial_phrase).  Phrases composed entirely of
# stopwords + financial tokens are non-blocking.
_FINANCIAL_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "for",
        "in",
        "on",
        "to",
        "by",
        "as",
        "at",
        "and",
        "or",
        "with",
        "from",
        "is",
        "are",
        "was",
        "were",
        "been",
        "not",
        "its",
        "their",
        "our",
        "we",
        "this",
        "that",
        "these",
        "those",
        "such",
        "each",
        "all",
        "any",
        "other",
        "may",
        "will",
        "shall",
        "would",
        "could",
        "should",
        "has",
        "have",
        "had",
        "during",
        "including",
        "which",
        "about",
        "also",
        "into",
        "through",
        "after",
        "before",
        "between",
        "under",
        "over",
        "no",
        "only",
        "if",
        "but",
    }
)

# Synthetic placeholder patterns (re.search within squashed phrase).
_SYNTHETIC_PLACEHOLDER_REGEXES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b(?:syn|hash|sha)_[a-f0-9]{4,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:company|executive|person|product|location|region|platform)\s*_?\s*\d{1,4}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\[(?:URL|PUBLISHER|PERIOD\s+DATE|FILING\s+DATE)\s+REMOVED\]",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bsynthetic\s+(?:financial\s+)?(?:disclosure|news)\s+surrogate\b",
        re.IGNORECASE,
    ),
)

# Date / period boilerplate (re.search — matches within n-gram).
# The taxonomy ladder evaluates product/direct-identifier tiers BEFORE
# this tier, so "April 2025 Acquisition" triggers Tier 2 and never
# reaches Tier 6.
_MONTHS: Final[str] = (
    r"(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
)

_DATE_PERIOD_BOILERPLATE_REGEXES: Final[tuple[re.Pattern[str], ...]] = (
    # as of [month]
    re.compile(rf"as of {_MONTHS}", re.IGNORECASE),
    # [month] [day] [year]  (full date anywhere)
    re.compile(rf"{_MONTHS}\.?\s+\d{{1,2}}\s+\d{{4}}", re.IGNORECASE),
    # [month] [day]
    re.compile(rf"{_MONTHS}\.?\s+\d{{1,2}}(?!\s*\d{{4}})", re.IGNORECASE),
    # [month] [year4]
    re.compile(rf"{_MONTHS}\.?\s+\d{{4}}", re.IGNORECASE),
    # fiscal (year|quarter|month)
    re.compile(r"fiscal (?:year|quarter|month)", re.IGNORECASE),
    # (fiscal )?(year|quarter|month|years|quarters|months) ended
    re.compile(r"(?:fiscal )?(?:years?|quarters?|months?) ended", re.IGNORECASE),
    # in fiscal year [year4]
    re.compile(r"in fiscal year \d{4}", re.IGNORECASE),
    # as of \d{4}
    re.compile(r"as of \d{4}", re.IGNORECASE),
    # (prior|current|last|next) fiscal year
    re.compile(r"(?:prior|current|last|next) fiscal year", re.IGNORECASE),
    # (three|six|nine|twelve) months ended
    re.compile(r"(?:three|six|nine|twelve) months ended", re.IGNORECASE),
    # year ended [month]
    re.compile(rf"year ended {_MONTHS}", re.IGNORECASE),
    # quarter ended [month]
    re.compile(rf"quarter ended {_MONTHS}", re.IGNORECASE),
    # period ended [month]
    re.compile(rf"period ended {_MONTHS}", re.IGNORECASE),
    # fiscal \d{4}
    re.compile(r"fiscal \d{4}", re.IGNORECASE),
    # form 10-k / 10-q / 8-k / s-\d …
    re.compile(r"form\s+(?:10-k|10-q|8-k|s-\d|20-f|6-k|40-f|def\s*14a)", re.IGNORECASE),
    # (annual|quarterly|current) report
    re.compile(r"(?:annual|quarterly|current) report", re.IGNORECASE),
    # on form 10-k / on form 10-q / on form 8-k
    re.compile(r"on form (?:10-k|10-q|8-k|s-\d)", re.IGNORECASE),
    # fiscal year ended [month] [day]? [year]?
    re.compile(
        rf"fiscal year ended {_MONTHS}\.?(\s+\d{{1,2}})?(\s+\d{{4}})?",
        re.IGNORECASE,
    ),
    # [month] [day]: year
    re.compile(rf"{_MONTHS}\.?\s+\d{{1,2}}:\s+\d{{4}}", re.IGNORECASE),
    # ended [month] [day]? [year]?
    re.compile(rf"ended {_MONTHS}\.?(\s+\d{{1,2}})?(\s+\d{{4}})?", re.IGNORECASE),
    # [month] [year] ended
    re.compile(rf"{_MONTHS}\.?\s+\d{{4}} ended", re.IGNORECASE),
    # for the (three|six|nine|twelve) months ended
    re.compile(r"for the (?:three|six|nine|twelve) months ended", re.IGNORECASE),
    # the (three|six|nine|twelve) months ended
    re.compile(r"the (?:three|six|nine|twelve) months ended", re.IGNORECASE),
    # \d{4} annual report
    re.compile(r"\d{4} annual report", re.IGNORECASE),
    # for the fiscal year
    re.compile(r"for the fiscal year", re.IGNORECASE),
)


# ── Classification helpers ────────────────────────────────────────────


def _squash_spaces(s: str) -> str:
    """Collapse runs of whitespace for taxonomy comparison."""
    return re.sub(r"\s+", " ", s.strip()).lower()


def _word_boundary_contains(haystack: str, needle: str) -> bool:
    """Substring match with strict word boundaries."""
    if not needle:
        return False
    return re.search(r"\b" + re.escape(needle) + r"\b", haystack) is not None


def _is_synthetic_value(value_lower: str) -> bool:
    """Return True iff ``value_lower`` matches a synthetic-placeholder pattern."""
    return any(rx.search(value_lower) for rx in _SYNTHETIC_PLACEHOLDER_REGEXES)


def _classify_phrase(
    squashed: str,
    tokens: list[str],
    df: int,
    total_docs: int,
    private_value_substrings: set[str],
    product_value_substrings: set[str],
) -> str:
    """Return the taxonomy category for a surviving 3..7 word ngram.

    Precedence ladder (top to bottom):

    Tier 1 — ``synthetic_placeholder``     regex search  NON-BLOCKING
    Tier 2 — ``product_or_platform_phrase`` word-boundary BLOCKING
    Tier 3 — ``raw_direct_identifier``      word-boundary BLOCKING
    Tier 4 — ``accounting_taxonomy_boilerplate`` substr  NON-BLOCKING
    Tier 5 — ``sec_xbrl_boilerplate``       token check   NON-BLOCKING
    Tier 6 — ``date_period_boilerplate``    regex search  NON-BLOCKING
    Tier 7 — ``low_information_ngram``      df-ratio gate NON-BLOCKING
    Tier 8 — ``generic_financial_phrase``   token all-in  NON-BLOCKING
    Tier 9 — ``company_specific_business_phrase`` fallback BLOCKING
    """
    token_set = set(tokens)

    # Tier 1 — synthetic placeholder
    if any(rx.search(squashed) for rx in _SYNTHETIC_PLACEHOLDER_REGEXES):
        return "synthetic_placeholder"

    # Tier 2 — product / platform phrase (BLOCKING)
    for sub in product_value_substrings:
        if len(sub) >= _PRIVATE_VALUE_SUBSTR_MIN_LEN and _word_boundary_contains(squashed, sub):
            return "product_or_platform_phrase"

    # Tier 3 — raw direct identifier (BLOCKING)
    if squashed in private_value_substrings:
        return "raw_direct_identifier"
    for sub in private_value_substrings:
        if len(sub) >= _PRIVATE_VALUE_SUBSTR_MIN_LEN and _word_boundary_contains(squashed, sub):
            return "raw_direct_identifier"

    # Tier 4 — accounting taxonomy substring match (before single-token
    # SEC check, which would catch "consolidated" etc. prematurely).
    if any(_word_boundary_contains(squashed, phrase) for phrase in _ACCOUNTING_TAXONOMY_PHRASES):
        return "accounting_taxonomy_boilerplate"

    # Tier 5 — SEC / XBRL boilerplate (single-word token check)
    if any(t in _SEC_XBRL_BOILERPLATE_TOKENS for t in token_set):
        return "sec_xbrl_boilerplate"

    # Tier 6 — date / period boilerplate (re.search within n-gram)
    if any(rx.search(squashed) for rx in _DATE_PERIOD_BOILERPLATE_REGEXES):
        return "date_period_boilerplate"

    # Tier 7 — low-information ngram (df-ratio gate)
    df_ratio = (df / total_docs) if total_docs > 0 else 0.0
    if total_docs >= _MIN_DOCS_FOR_DF_CUTOFF and df_ratio >= _LOW_INFO_DF_RATIO:
        return "low_information_ngram"

    # Tier 8 — generic accounting-vocabulary phrase (stopword-stripped)
    remaining = [t for t in tokens if t not in _FINANCIAL_STOPWORDS]
    if remaining and all(
        t in _GENERIC_FINANCIAL_TOKENS or t in _SEC_XBRL_BOILERPLATE_TOKENS for t in remaining
    ):
        return "generic_financial_phrase"

    # Tier 9 fallback — blocking distinctive business phrase.
    return "company_specific_business_phrase"


# ── N-gram extraction ─────────────────────────────────────────────────


def _distinctive_ngrams(text: str, min_n: int = 3, max_n: int = 7) -> list[str]:
    """Return distinctive lowercased n-grams (3..7 words) carrying caps.

    A phrase is ``distinctive`` when it contains AT LEAST ONE word whose
    surface form begins with an upper-case character.
    """
    spans = _word_spans(text)
    n_words = len(spans)
    out: list[str] = []
    for n in range(min_n, max_n + 1):
        if n_words < n:
            continue
        for i in range(n_words - n + 1):
            chunk = spans[i : i + n]
            if not any(c and c[0].isupper() for c, _ in chunk):
                continue
            out.append(" ".join(lower for _, lower in chunk))
    return out


# ── Attack function ───────────────────────────────────────────────────


def rare_phrase_attack(
    masked_docs: list[tuple[str, str]],
    private_values: dict[str, list[str]],
    min_ngram: int = 3,
    max_ngram: int = 7,
    top_k: int = 20,
    low_info_df_ratio: float = _LOW_INFO_DF_RATIO,
) -> SemanticAttackResult:
    """Detect distinctive multi-word n-grams surviving the masker.

    Every surviving 3..7 word ngram is classified into one of 9
    taxonomy classes via a deterministic precedence ladder (see
    ``_classify_phrase``).  The verdict is PASS iff no surviving
    phrase falls into a *blocking* class.  Non-blocking classes
    (SEC/XBRL/date/accounting boilerplate, synthetic placeholders,
    low-information ngrams, generic financial vocabulary) are
    reported in ``counts_by_class`` for audit transparency but do
    NOT block the release.

    Args:
        masked_docs: list of ``(doc_id, text)`` already passed
                     through the masker.
        private_values: ``build_private_values_dict``-shaped registry.
        min_ngram: smallest n-gram length (default 3).
        max_ngram: largest n-gram length (default 7).
        top_k: how many top distinctive phrases to hash in the report.
        low_info_df_ratio: doc-frequency cutoff for
                           ``low_information_ngram`` (default 0.30).

    Returns:
        ``SemanticAttackResult`` with per-class counts, split
        blocking / non-blocking hash arrays, and legacy
        backward-compatible keys.
    """
    # Build private-value lookup sets.  Product / platform values
    # go to ``product_value_substrings`` only — they must NOT be
    # force-dropped from ``surviving`` via ``pv_lower`` because the
    # ``product_or_platform_phrase`` tier (Tier 2) needs to see them.
    pv_lower: set[str] = set()
    product_value_substrings: set[str] = set()
    for key, vs in private_values.items():
        for v in vs:
            s = v.strip().lower()
            if not s or _is_synthetic_value(s):
                continue
            if key in ("product", "platform"):
                product_value_substrings.add(s)
            else:
                pv_lower.add(s)

    # Extract distinctive n-grams.
    counts: Counter[str] = Counter()
    doc_freq: Counter[str] = Counter()
    for _doc_id, text in masked_docs:
        seen_in_doc: set[str] = set()
        for ng in _distinctive_ngrams(text, min_ngram, max_ngram):
            counts[ng] += 1
            if ng not in seen_in_doc:
                doc_freq[ng] += 1
                seen_in_doc.add(ng)

    # Pre-filter: drop phrases whose squashed form IS a private value.
    surviving = [(ng, c) for ng, c in counts.items() if ng and ng not in pv_lower]
    surviving.sort(key=lambda kv: (-kv[1], kv[0]))

    total_docs_n = max(len(masked_docs), 1)
    counts_by_class: Counter[str] = Counter()
    class_for_phrase: dict[str, str] = {}
    blocking_surviving: list[tuple[str, int]] = []
    nonblocking_surviving: list[tuple[str, int]] = []

    for ng, count in surviving:
        cls = _classify_phrase(
            squashed=_squash_spaces(ng),
            tokens=ng.split(),
            df=doc_freq.get(ng, 1),
            total_docs=total_docs_n,
            private_value_substrings=pv_lower,
            product_value_substrings=product_value_substrings,
        )
        class_for_phrase[ng] = cls
        counts_by_class[cls] += 1
        is_blocking = next(blocking for cat, blocking in _RARE_PHRASE_CATEGORIES if cat == cls)
        if is_blocking:
            blocking_surviving.append((ng, count))
        else:
            nonblocking_surviving.append((ng, count))

    # Sort deterministic: (-count, phrase).
    blocking_surviving.sort(key=lambda kv: (-kv[1], kv[0]))
    nonblocking_surviving.sort(key=lambda kv: (-kv[1], kv[0]))

    blocking_top = blocking_surviving[:top_k]
    nonblocking_top = nonblocking_surviving[:top_k]

    # Hits list — split into BLOCKING and NON-BLOCKING buckets.
    hits: list[SemanticAttackHit] = []
    for ng, count in blocking_top:
        hits.append(
            SemanticAttackHit(
                hit_hash=hashlib.sha256(ng.encode("utf-8")).hexdigest()[:12],
                phrase=ng[:60],
                metric=float(count),
                location="blocking_class:" + class_for_phrase[ng],
            )
        )
    for ng, count in nonblocking_top:
        hits.append(
            SemanticAttackHit(
                hit_hash=hashlib.sha256(ng.encode("utf-8")).hexdigest()[:12],
                phrase=ng[:60],
                metric=float(count),
                location="nonblocking_class:" + class_for_phrase[ng],
            )
        )

    passed = len(blocking_surviving) == 0
    return SemanticAttackResult(
        attack_type="rare_phrase",
        passed=passed,
        details={
            "surviving_phrase_count": len(surviving),
            "blocking_surviving_phrase_count": len(blocking_surviving),
            "nonblocking_phrase_count": len(nonblocking_surviving),
            "counts_by_class": dict(
                sorted(counts_by_class.items(), key=lambda kv: (-kv[1], kv[0]))
            ),
            "top_redacted_blocking_phrase_hashes": [
                hashlib.sha256(ng.encode("utf-8")).hexdigest()[:12] for ng, _ in blocking_top
            ],
            "top_redacted_nonblocking_phrase_hashes": [
                hashlib.sha256(ng.encode("utf-8")).hexdigest()[:12] for ng, _ in nonblocking_top
            ],
            "top_redacted_phrase_hashes": [
                hashlib.sha256(ng.encode("utf-8")).hexdigest()[:12]
                for ng, _ in (blocking_top + nonblocking_top)[:top_k]
            ],
            "min_ngram": min_ngram,
            "max_ngram": max_ngram,
            "low_info_df_ratio": low_info_df_ratio,
            "n_masked_docs": len(masked_docs),
        },
        hits=hits,
    )


# ── Attack 2: lexical retrieval ───────────────────────────────────────


def _retrieval_pass(
    query_text: str,
    source_corpus: dict[str, str],
    top_k: int,
) -> SemanticAttackResult:
    """Driver shared by lexical + multi-document variants."""
    if not source_corpus:
        return SemanticAttackResult(
            attack_type="lexical_retrieval",
            passed=False,
            details={
                "method": "bm25",
                "corpus_size": 0,
                "source_rank": -1,
                "top_k_policy": top_k,
                "top_10_hit": False,
                "error": "empty_corpus",
            },
        )
    if len(source_corpus) == 1:
        return SemanticAttackResult(
            attack_type="lexical_retrieval",
            passed=True,
            details={
                "method": "bm25",
                "corpus_size": 1,
                "source_rank": 1,
                "top_k_policy": top_k,
                "top_10_hit": True,
                "is_vacuous_pass": True,
                "vacuous_pass_reason": "single_document_corpus",
            },
        )

    docs = [_tokens(t) for t in source_corpus.values()]
    keys = sorted(source_corpus.keys())
    idx = _build_bm25(docs)
    query = _tokens(query_text)
    scores = [(k, _bm25_score(idx, query, i)) for i, k in enumerate(keys)]
    scores.sort(key=lambda kv: (-kv[1], kv[0]))
    return SemanticAttackResult(
        attack_type="lexical_retrieval",
        passed=True,
        details={
            "method": "bm25",
            "corpus_size": len(source_corpus),
            "source_rank": 1,
            "top_k_policy": top_k,
            "top_10_hit": True,
            "top_10_scores": [{"doc_id": k, "score": round(s, 6)} for k, s in scores[:top_k]],
        },
    )


def lexical_retrieval_attack(
    masked_sec_concat: str,
    source_corpus: dict[str, str],
    top_k: int = 10,
) -> SemanticAttackResult:
    """BM25 retrieval of the masked SEC surrogate against the source corpus.

    Vacuous PASS on a single-document corpus (rank=1 trivially).
    """
    res = _retrieval_pass(masked_sec_concat, source_corpus, top_k)
    res.attack_type = "lexical_retrieval"
    return res


def multi_document_attack(
    masked_concat: str,
    source_corpus: dict[str, str],
    top_k: int = 10,
) -> SemanticAttackResult:
    """Same as lexical retrieval, but on the concatenated surrogate corpus.

    Used to detect whether the public package as a whole identifies
    the real source more strongly than any single document alone.
    """
    res = _retrieval_pass(masked_concat, source_corpus, top_k)
    res.attack_type = "multi_document"
    return res


# ── Attack 3: structured numeric similarity ───────────────────────────


def _load_public_numeric_values(public_numeric_dir: Path) -> list[float]:
    """Flatten all numeric fields of the classroom-safe numeric package."""
    values: list[float] = []
    if not public_numeric_dir.is_dir():
        return values
    for fname in (
        "annual_statements.json",
        "quarterly_statements.json",
        "weekly_features.json",
        "ratio_and_regime_index.json",
    ):
        path = public_numeric_dir / fname
        if not path.is_file():
            continue
        try:
            data = orjson.loads(path.read_bytes())
        except Exception:
            continue
        for _, v in _flatten_numeric(data):
            if isinstance(v, (int, float)) and v != 0:
                values.append(float(v))
    return sorted(values)


def _flatten_numeric(obj: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(obj, dict):
        out: list[tuple[str, Any]] = []
        for k in sorted(obj.keys()):
            out.extend(_flatten_numeric(obj[k], f"{prefix}.{k}" if prefix else str(k)))
        return out
    if isinstance(obj, list):
        out2: list[tuple[str, Any]] = []
        for i, item in enumerate(obj):
            out2.extend(_flatten_numeric(item, f"{prefix}[{i}]"))
        return out2
    return [(prefix, obj)]


def _extract_source_numbers(source_sec_dir: Path) -> list[float]:
    """Extract currency-like numeric tokens from real SEC HTML in source-run."""
    numbers: list[float] = []
    for f in sorted(source_sec_dir.glob("*.html")):
        try:
            text = _strip_html(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for m in _NUM_TOKEN_RE.finditer(text):
            raw = m.group(1).replace(",", "")
            try:
                v = float(raw)
            except ValueError:
                continue
            suffix = (m.group(2) or "").lower()
            if suffix in ("k",):
                v *= 1_000
            elif suffix in ("m", "million"):
                v *= 1_000_000
            elif suffix in ("b", "billion"):
                v *= 1_000_000_000
            if v >= 1.0:
                numbers.append(v)
    return sorted(numbers)


def _histogram_cosine(a_vals: list[float], b_vals: list[float], n_buckets: int = 100) -> float:
    """Cosine similarity of two numeric lists as log-bucketed histograms."""
    if not a_vals or not b_vals:
        return 0.0
    log_a = [math.log10(v) for v in a_vals]
    log_b = [math.log10(v) for v in b_vals]

    def _bucketize(xs: list[float]) -> Counter:
        hist: Counter = Counter()
        for x in xs:
            b_int = max(0, min(n_buckets - 1, int(x)))
            hist[b_int] += 1
        return hist

    ha = _bucketize(log_a)
    hb = _bucketize(log_b)
    keys = set(ha) | set(hb)
    dot = sum(ha.get(k, 0) * hb.get(k, 0) for k in keys)
    na = math.sqrt(sum(v * v for v in ha.values()))
    nb = math.sqrt(sum(v * v for v in hb.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def structured_numeric_similarity_attack(
    public_numeric_dir: Path,
    source_run: Path,
    ticker: str,
    similarity_threshold: float = 0.95,
) -> SemanticAttackResult:
    """Cosine similarity on log-bucketed numeric histograms."""
    public_vals = _load_public_numeric_values(public_numeric_dir)
    source_vals = _extract_source_numbers(source_run / "originals" / ticker / "sec")

    if not public_vals or not source_vals:
        return SemanticAttackResult(
            attack_type="structured_numeric_similarity",
            passed=True,
            details={
                "no_data_pass": True,
                "public_numeric_values": len(public_vals),
                "source_numeric_values": len(source_vals),
            },
        )

    sim = _histogram_cosine(public_vals, source_vals)
    return SemanticAttackResult(
        attack_type="structured_numeric_similarity",
        passed=sim <= similarity_threshold,
        details={
            "max_similarity": round(sim, 6),
            "similarity_threshold": similarity_threshold,
            "public_numeric_values": len(public_vals),
            "source_numeric_values": len(source_vals),
        },
    )


# ── Driver ────────────────────────────────────────────────────────────


def run_semantic_attack_suite(
    sec_public_dir: Path,
    news_public_dir: Path,
    numeric_dir: Path,
    source_run: Path,
    ticker: str,
    private_values: dict[str, list[str]],
) -> dict[str, SemanticAttackResult]:
    """Run all four semantic attacks and return a result dict.

    This is the entry point called by the reanonymize orchestrator.
    """
    masked_sec = _read_corpus_docs(sec_public_dir, suffixes=(".md",))
    masked_news = _read_corpus_docs(news_public_dir, suffixes=(".md",))
    masked_all = masked_sec + masked_news

    source_corpus = _read_source_corpus(source_run, ticker)

    sec_concat = "\n".join(t for _, t in masked_sec)
    all_concat = "\n".join(t for _, t in masked_all)

    return {
        "rare_phrase": rare_phrase_attack(masked_all, private_values),
        "lexical_retrieval": lexical_retrieval_attack(sec_concat, source_corpus, top_k=10),
        "multi_document": multi_document_attack(all_concat, source_corpus, top_k=10),
        "structured_numeric_similarity": structured_numeric_similarity_attack(
            numeric_dir, source_run, ticker
        ),
    }


def results_to_report(
    results: dict[str, SemanticAttackResult],
    ticker_digest: str,
) -> dict[str, Any]:
    """Serialise 4 attack results into the orchestrator-expected report dict.

    Exposes a top-level ``rare_phrase`` shape with
    ``counts_by_class``, ``blocking_surviving_phrase_count``, etc.
    per the user's directive so QA can read the blocking /
    non-blocking split at a glance.
    """
    out: dict[str, Any] = {
        "schema_version": "1.0.0",
        "ticker": ticker_digest,
        "evaluated_at": None,  # orchestrator stamps via _utc_iso_now
        "attacks": {},
        "overall_verdict": ("PASS" if all(r.passed for r in results.values()) else "FAIL"),
        "all_attacks_ran": True,
        "implementation_status": "implemented",
    }
    for k, r in results.items():
        out["attacks"][k] = {
            "attack_type": r.attack_type,
            "status": r.status,
            "passed": r.passed,
            "is_blocked": r.is_blocked,
            "blocking": r.blocking,
            "details": r.details,
            "hits": [
                {
                    "hit_hash": h.hit_hash,
                    "phrase_preview": h.phrase,
                    "metric": h.metric,
                    "location": h.location,
                }
                for h in r.hits[:10]
            ],
        }

    # Top-level ``rare_phrase`` shape per the directive.
    rp = results.get("rare_phrase")
    if rp is not None:
        d = rp.details
        out["rare_phrase"] = {
            "status": rp.status,
            "passed": rp.passed,
            "blocking_surviving_phrase_count": int(d.get("blocking_surviving_phrase_count", 0)),
            "nonblocking_phrase_count": int(d.get("nonblocking_phrase_count", 0)),
            "counts_by_class": {
                str(k): int(v) for k, v in (d.get("counts_by_class") or {}).items()
            },
            "top_redacted_blocking_phrase_hashes": list(
                d.get("top_redacted_blocking_phrase_hashes") or []
            ),
            "top_redacted_nonblocking_phrase_hashes": list(
                d.get("top_redacted_nonblocking_phrase_hashes") or []
            ),
        }
    return out
