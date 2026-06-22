"""Quality gates and scrubbing helpers for sanitized submission exports."""

from __future__ import annotations

import re
import warnings
from collections.abc import Sequence
from typing import Any

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

SEC_FILES = {
    "business": "business_summary.md",
    "risk_factors": "risk_factors_summary.md",
    "mdna": "mdna_summary.md",
    "financial": "financial_statement_summary.md",
    "recent_event": "recent_event_summary.md",
}
DEFAULT_TICKERS = ["CL", "PEP", "TJX", "PM", "AMZN", "HBAN", "BLK", "GOOGL"]
FORBIDDEN_ZIP_SUBSTRINGS = (
    "originals/",
    "private_maps/",
    "smoke_excerpts/",
    ".env",
    "nvapi-",
    "NVIDIA_API_KEY",
    "/Users/",
    "/content/",
)
TAXONOMY_RE = re.compile(r"\b(?:us-gaap|xbrli|iso4217|utr|srt):|TICKER_[A-Z0-9_]+Member", re.I)
URL_RE = re.compile(r"https?://\S+|\bwww\.\S+", re.I)
PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?![A-Za-z0-9])"
)
EIN_RE = re.compile(r"\b\d{2}-\d{7}\b|\b(?:IRS\s+)?Employer Identification No\.?", re.I)
SEC_FILE_RE = re.compile(r"\b(?:000|001|002|003|005|033|333|811)-\d{4,8}\b")
ACCESSION_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b|\b\d{18}\b")
DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+\d{4}\b|\b\d{4}-\d{2}-\d{2}\b",
    re.I,
)
ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9 .'-]+(?:Street|St\.|Avenue|Ave\.|Road|Rd\.|Boulevard|Blvd\.|Drive|Dr\.|Lane|Ln\.|Way|Plaza|Suite)\b",
    re.I,
)
HEADER_RE = re.compile(
    r"Exact name of registrant|Commission File Number|SECURITIES AND EXCHANGE COMMISSION",
    re.I,
)
ROLE_RE = re.compile(
    r"\b(?:director|officer|chief|signer|auditor|by:|/s/|deloitte|pwc|ernst|kpmg)\b",
    re.I,
)
VOTE_RE = re.compile(
    r"\b(?:for\s+against\s+abstain|abstain|broker non-votes?|votes?)\b.*\d[\d,]{3,}",
    re.I,
)
TARGET_TICKER_RE = re.compile(r"\b(?:CL|PEP|TJX|PM|AMZN|HBAN|BLK|GOOGL)\b")
COMPANY_DATA: dict[str, dict[str, Any]] = {
    "CL": {
        "cik": "0000021665",
        "aliases": ["Colgate-Palmolive Company", "Colgate-Palmolive", "Colgate", "Palmolive"],
        "domains": ["colgatepalmolive.com", "colgate.com"],
        "people": ["Noel Wallace", "John Cummings"],
    },
    "PEP": {
        "cik": "0000077476",
        "aliases": ["PepsiCo, Inc.", "PepsiCo", "Pepsi", "Frito-Lay", "Quaker Oats"],
        "domains": ["pepsico.com", "pepsi.com"],
        "people": ["Ramon Laguarta", "Hugh Johnston"],
    },
    "TJX": {
        "cik": "0000109198",
        "aliases": ["The TJX Companies, Inc.", "TJX Companies", "T.J. Maxx"],
        "domains": ["tjx.com", "tjmaxx.com", "marshalls.com"],
        "people": ["Ernie Herrman"],
    },
    "PM": {
        "cik": "0001413329",
        "aliases": [
            "Philip Morris International Inc.",
            "Philip Morris International",
            "Philip Morris",
            "PMI",
        ],
        "domains": ["pmi.com"],
        "people": ["Jacek Olczak", "Emmanuel Babeau"],
    },
    "AMZN": {
        "cik": "0001018724",
        "aliases": ["Amazon.com, Inc.", "Amazon.com", "Amazon", "Amazon Web Services", "AWS"],
        "domains": ["amazon.com", "aws.amazon.com"],
        "people": ["Jeff Bezos", "Andy Jassy", "Brian Olsavsky"],
    },
    "HBAN": {
        "cik": "0000049196",
        "aliases": [
            "Huntington Bancshares Incorporated",
            "Huntington Bancshares",
            "Huntington National Bank",
            "Huntington Bank",
            "Huntington",
        ],
        "domains": ["huntington.com"],
        "people": ["Stephen Steinour"],
    },
    "BLK": {
        "cik": "0001364742",
        "aliases": ["BlackRock, Inc.", "BlackRock"],
        "domains": ["blackrock.com", "ishares.com"],
        "people": ["Larry Fink", "Robert Goldstein"],
    },
    "GOOGL": {
        "cik": "0001652044",
        "aliases": ["Alphabet Inc.", "Alphabet", "Google LLC", "Google"],
        "domains": ["abc.xyz", "google.com", "youtube.com"],
        "people": ["Sundar Pichai", "Ruth Porat"],
    },
}


def html_to_text(html: str) -> str:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "meta", "link"]):
        tag.decompose()
    for tag in soup.find_all(style=re.compile("display\\s*:\\s*none", re.I)):
        tag.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()


def scrub_text(text: str, private_map: dict[str, dict[str, str]] | None = None) -> str:
    result = URL_RE.sub("[URL_REMOVED]", text)
    for regex, replacement in (
        (PHONE_RE, "[PHONE_REMOVED]"),
        (EIN_RE, "[EIN_REMOVED]"),
        (SEC_FILE_RE, "[FILING_ID]"),
        (ACCESSION_RE, "[FILING_ID]"),
        (DATE_RE, "[DATE_REMOVED]"),
        (ADDRESS_RE, "[ADDRESS_REMOVED]"),
    ):
        result = regex.sub(replacement, result)
    lines = []
    for line in result.splitlines():
        if HEADER_RE.search(line) or ROLE_RE.search(line) or VOTE_RE.search(line):
            lines.append("[IDENTITY_FIELD_REMOVED]")
        else:
            lines.append(line)
    result = "\n".join(lines)
    if private_map:
        for bucket in private_map.values():
            for original, pseudo in sorted(
                bucket.items(), key=lambda item: len(item[0]), reverse=True
            ):
                result = re.sub(
                    rf"(?<![A-Za-z0-9]){re.escape(original)}(?![A-Za-z0-9])",
                    pseudo,
                    result,
                    flags=re.I,
                )
    return re.sub(r"[ \t]+", " ", result).strip()


def rejection_reason(text: str) -> str | None:
    compact = re.sub(r"\s+", " ", text).strip()
    sentences = re.findall(r"[A-Z][^.!?]{25,}[.!?]", compact)
    noisy = sum(1 for line in text.splitlines() if TAXONOMY_RE.search(line))
    if len(re.sub(r"[^A-Za-z]", "", compact)) < 500:
        return "fewer than 500 meaningful characters"
    if len(sentences) < 5:
        return "fewer than 5 usable sentences"
    if noisy >= 3 or noisy > max(1, len(text.splitlines()) // 6):
        return "taxonomy or XBRL noise"
    if re.fullmatch(r"(?:item\s+\d+[a-z]?\.?|page\s+\d+|table of contents|\s)+", compact, re.I):
        return "heading-only section"
    return None


def summarize(text: str, private_map: dict[str, dict[str, str]], label: str) -> tuple[str, str]:
    reason = rejection_reason(text)
    if reason:
        return f"# {label}\n\nUNAVAILABLE: {reason}.\n", reason
    clean = scrub_text(text, private_map)
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", clean)
        if len(sentence.strip()) > 35 and not TAXONOMY_RE.search(sentence)
    ]
    summary = " ".join(sentences[:8])[:2200].strip()
    if rejection_reason(summary):
        return (
            f"# {label}\n\nUNAVAILABLE: sanitized text did not meet quality threshold.\n",
            "sanitized quality threshold",
        )
    return f"# {label}\n\n{summary}\n", "OK"


def section(text: str, start: str, ends: Sequence[str]) -> str:
    text = re.sub(r"\s+", " ", text)
    starts = list(re.finditer(start, text, re.I))
    best = ""
    for match in starts:
        tail = text[match.end() :]
        offsets = [m.start() for token in ends if (m := re.search(token, tail, re.I))]
        end = match.end() + min(offsets) if offsets else min(len(text), match.start() + 80_000)
        candidate = text[match.start() : end]
        if len(candidate) > len(best):
            best = candidate
    return best


def bin_value(value: Any) -> str:
    try:
        number = abs(float(value))
        raw = float(value)
    except (TypeError, ValueError):
        return "UNAVAILABLE"
    if number != number:
        return "UNAVAILABLE"
    if raw < 0:
        return "negative"
    if number < 1_000_000_000:
        return "small"
    if number < 10_000_000_000:
        return "medium"
    if number < 100_000_000_000:
        return "large"
    return "mega"


EVENT_CATEGORY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("shareholder", "shareholder meeting"),
    ("annual meeting", "shareholder meeting"),
    ("auditor", "accounting and audit matter"),
    ("accounting", "accounting and audit matter"),
    ("executive compensation", "executive compensation matter"),
    ("compensation", "executive compensation matter"),
    ("financing", "financing/capital markets matter"),
    ("debt", "financing/capital markets matter"),
    ("note offering", "financing/capital markets matter"),
    ("capital markets", "financing/capital markets matter"),
    ("operational", "operational/business update"),
    ("business update", "operational/business update"),
    ("restructuring", "operational/business update"),
)


def infer_event_category(text: str) -> str:
    lowered = re.sub(r"\s+", " ", text).lower()
    for keyword, category in EVENT_CATEGORY_KEYWORDS:
        if keyword in lowered:
            return category
    return "general current-report disclosure"


def build_recent_event_summary(
    event_text: str,
    company_id: str,
    source_form: str = "8-K",
) -> tuple[str, str]:
    """Build a safe, sanitized recent-event summary.

    Never copies raw 8-K cover-page text into the public output. The summary
    is a fixed template plus a coarse event category inferred from safe item
    labels. Returns (markdown_body, status_reason).
    """
    category = infer_event_category(event_text) if event_text and event_text.strip() else ""
    event_section = (
        f"## Event Type\n\nRecent current-report filing.\n\nInferred event category: {category}.\n"
        if category
        else "## Event Type\n\nRecent current-report filing.\n"
    )
    body = (
        f"# Recent Event Summary\n\n"
        f"* Company: {company_id}\n"
        f"* Source form: {source_form}\n"
        f"* Summary status: OK\n"
        f"* Relative filing period: RECENT_PERIOD\n\n"
        f"{event_section}\n"
        f"## Sanitized Summary\n\n"
        f"A recent Form 8-K-style current report was available for this company. "
        f"The public artifact does not include the raw filing body because "
        f"current-report cover pages and signatures contain direct identifiers.\n\n"
        f"## Identity Risk Removed\n\n"
        f"* street address\n"
        f"* city/state/zip\n"
        f"* phone number\n"
        f"* tax employer ID\n"
        f"* SEC filing identifier numbers\n"
        f"* filing identifier\n"
        f"* filing URL\n"
        f"* leadership and signatory names\n"
        f"* audit firm names\n"
        f"* vote tables and exact vote counts\n"
        f"* raw filing headers and execution blocks\n"
    )
    return body, "OK"


def brief_topic(text: str) -> str:
    lowered = text.lower()
    if "earn" in lowered:
        return "earnings"
    if "stock" in lowered or "share" in lowered:
        return "market_performance"
    return "corporate_update"
