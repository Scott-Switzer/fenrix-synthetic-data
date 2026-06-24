"""Minimal NVIDIA artifact verifier for sanitized submission bundles.

This is a *verifier* (red-team reviewer), not the source of truth and not a
full-document reanonymize orchestrator. It reviews only public artifact text
and emits QA reports. If the provider is unavailable, the build continues and
the report is marked INCOMPLETE.

Security contract:
* ``NVIDIA_API_KEY`` is read from env only.
* The key is never printed, logged, or written to artifacts.
* Only public artifact text is sent to the provider.
* Originals, private maps, API keys, and local paths are never sent.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

from .submission_quality import build_recent_event_summary

NVIDIA_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_MODEL = "meta/llama-3.1-70b-instruct"
MAX_REPAIR_PASSES = 2
MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 2.0

_FORBIDDEN_PAYLOAD_SUBSTRINGS = (
    "originals/",
    "private_maps/",
    "smoke_excerpts/",
    ".env",
    "nvapi-",
    "NVIDIA_API_KEY",
    "/Users/",
    "/content/",
)

_REPAIRABLE_FILENAMES = {
    "recent_event_summary.md",
    "business_summary.md",
    "risk_factors_summary.md",
    "mdna_summary.md",
    "financial_statement_summary.md",
}

_REVIEWER_SYSTEM_PROMPT = (
    "You are a privacy red-team reviewer for anonymized financial documents. "
    "Review the provided public artifact text only. Do NOT attempt to identify "
    "the real company. Flag whether the text appears searchable, identity-leaking, "
    "raw-filing-like, XBRL-garbage, or unusable.\n\n"
    "IMPORTANT: Tokens like COMPANY_001, COMPANY_002, TICKER_001, CIK_001 and "
    "similar COMPANY_NNN / TICKER_NNN / CIK_NNN patterns are intentional safe "
    "pseudonyms. They are NOT direct identifiers. Do NOT classify these "
    "pseudonyms as direct identifiers or identity risks.\n\n"
    "Only classify as direct_identifier when REAL company names, REAL tickers, "
    "REAL CIKs, REAL domains, REAL URLs, REAL people names, REAL addresses, "
    "raw SEC filing IDs (accession numbers, file numbers), or raw issuer-specific "
    "metadata appear in the text.\n\n"
    "If the only evidence of a risk is a public pseudonym token like COMPANY_001, "
    "the status must NOT be REVIEW_REQUIRED or FAIL.\n\n"
    "Respond with JSON only matching the schema: "
    '{"status":"PASS"|"REVIEW_REQUIRED"|"FAIL","risks":['
    '{"file":"...","risk_type":"direct_identifier|search_fingerprint|'
    'numeric_fingerprint|raw_filing_leak|xbrl_garbage|document_quality|other",'
    '"severity":"low|medium|high","evidence":"short non-sensitive excerpt",'
    '"recommended_fix":"..."}],"summary":"..."}'
)


_PSEUDONYM_RE = re.compile(r"\b(?:COMPANY|TICKER|CIK)_\d{3}\b")
_REAL_IDENTIFIER_PATTERNS = [
    re.compile(r"https?://\S+", re.I),
    re.compile(r"\b\d{10}-\d{2}-\d{6}\b"),
    re.compile(r"\b(?:000|001|002|003|005|033|333|811)-\d{4,8}\b"),
    re.compile(
        r"\b\d{1,6}\s+[A-Za-z0-9 .'-]+(?:Street|St\.|Avenue|Ave\.|Road|Rd\.|Boulevard|Blvd\.|Drive|Dr\.|Lane|Ln\.|Way|Plaza|Suite)\b",
        re.I,
    ),
    re.compile(
        r"(?<![A-Za-z0-9])(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}(?![A-Za-z0-9])"
    ),
    re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b"),
]


def _is_pseudonym_only_risk(evidence: str) -> bool:
    """Return True if the evidence contains only pseudonym tokens and no real identifiers."""
    pseudonym_matches = _PSEUDONYM_RE.findall(evidence)
    if not pseudonym_matches:
        return False
    stripped = _PSEUDONYM_RE.sub("", evidence)
    for pattern in _REAL_IDENTIFIER_PATTERNS:
        if pattern.search(stripped):
            return False
    return True


def _suppress_pseudonym_risks(report: dict[str, Any]) -> dict[str, Any]:
    """Drop risks whose evidence is only public pseudonym tokens.

    If all risks are dropped, downgrade status from REVIEW_REQUIRED/FAIL to PASS.
    """
    risks = report.get("risks", [])
    kept = [r for r in risks if not _is_pseudonym_only_risk(r.get("evidence", ""))]
    dropped = len(risks) - len(kept)
    report["risks"] = kept
    if dropped > 0:
        existing = report.get("suppressed_pseudonym_risks", 0)
        report["suppressed_pseudonym_risks"] = existing + dropped
    if not kept and report.get("status") in {"REVIEW_REQUIRED", "FAIL"}:
        report["status"] = "PASS"
        prev_summary = report.get("summary", "")
        report["summary"] = (
            f"All flagged risks were public pseudonym tokens (false positives). "
            f"{prev_summary}".strip()
        )
    return report


def nvidia_available() -> bool:
    """Return True iff NVIDIA_API_KEY is present in the environment."""
    return bool(os.environ.get("NVIDIA_API_KEY", "").strip())
    """Return True iff NVIDIA_API_KEY is present in the environment."""
    return bool(os.environ.get("NVIDIA_API_KEY", "").strip())


def _model() -> str:
    return os.environ.get("NVIDIA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _is_safe_for_payload(rel_path: str, text: str) -> bool:
    if any(bad in rel_path for bad in _FORBIDDEN_PAYLOAD_SUBSTRINGS):
        return False
    if any(bad in text for bad in _FORBIDDEN_PAYLOAD_SUBSTRINGS):
        return False
    return True


def _collect_public_files(
    artifact_root: Path, max_chars_per_file: int = 6000, company_dir: Path | None = None
) -> list[dict[str, str]]:
    """Collect sanitized public artifact text. Never includes originals/private maps.

    If ``company_dir`` is given, only that company's public files are collected.
    """
    files: list[dict[str, str]] = []
    if company_dir is not None:
        scan_root = company_dir
        base = artifact_root
    else:
        public_dir = artifact_root / "anonymized"
        if not public_dir.is_dir():
            return files
        scan_root = public_dir
        base = artifact_root
    if not scan_root.is_dir():
        return files
    for path in sorted(scan_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".md", ".json", ".csv", ".txt"}:
            continue
        rel = path.relative_to(base).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _is_safe_for_payload(rel, text):
            continue
        files.append({"file": rel, "text": text[:max_chars_per_file]})
    return files


def _build_user_prompt(company_id: str, file_chunks: list[dict[str, str]]) -> str:
    parts = [f"Company public ID: {company_id}", "", "Public artifact text:"]
    for chunk in file_chunks:
        parts.append(f"\n--- FILE: {chunk['file']} ---\n{chunk['text']}\n")
    parts.append(
        "\nReview the above public artifact text for residual identity risk. "
        "Respond with JSON only."
    )
    return "\n".join(parts)


def _call_nvidia(company_id: str, file_chunks: list[dict[str, str]]) -> dict[str, Any]:
    """Call NVIDIA chat completions with retry/backoff. Returns parsed JSON or error dict."""
    if httpx is None:
        return {
            "status": "INCOMPLETE",
            "reason": "httpx package not available",
            "risks": [],
        }
    api_key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if not api_key:
        return {"status": "INCOMPLETE", "reason": "provider credential not set", "risks": []}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _model(),
        "messages": [
            {"role": "system", "content": _REVIEWER_SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(company_id, file_chunks)},
        ],
        "temperature": 0.0,
        "max_tokens": 2048,
    }
    last_exc_name = ""
    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(NVIDIA_ENDPOINT, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            last_exc_name = type(exc).__name__
            if attempt < MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF_BASE ** (attempt + 1))
                continue
            return {
                "status": "INCOMPLETE",
                "reason": f"provider request failed: {last_exc_name}",
                "risks": [],
            }
        except (httpx.TimeoutException, httpx.NetworkError, OSError) as exc:
            last_exc_name = type(exc).__name__
            if attempt < MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF_BASE ** (attempt + 1))
                continue
            return {
                "status": "INCOMPLETE",
                "reason": f"provider request failed: {last_exc_name}",
                "risks": [],
            }
        content = ""
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return {
                "status": "INCOMPLETE",
                "reason": "malformed provider response: missing choices",
                "risks": [],
            }
        parsed = _parse_review_json(content)
        if parsed is None:
            return {
                "status": "INCOMPLETE",
                "reason": "malformed provider response: JSON parse failed",
                "raw_excerpt": content[:200],
                "risks": [],
            }
        return _suppress_pseudonym_risks(parsed)
    return {
        "status": "INCOMPLETE",
        "reason": f"provider request failed after {MAX_RETRIES} retries: {last_exc_name}",
        "risks": [],
    }


def _parse_review_json(content: str) -> dict[str, Any] | None:
    """Parse NVIDIA JSON response safely. Returns None if unparseable."""
    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        return None
    try:
        result = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict):
        return None
    status = str(result.get("status", "")).upper()
    if status not in {"PASS", "REVIEW_REQUIRED", "FAIL"}:
        return None
    risks_raw = result.get("risks", [])
    if not isinstance(risks_raw, list):
        risks_raw = []
    risks = []
    for risk in risks_raw:
        if not isinstance(risk, dict):
            continue
        risks.append(
            {
                "file": str(risk.get("file", ""))[:200],
                "risk_type": str(risk.get("risk_type", "other"))[:60],
                "severity": str(risk.get("severity", "low"))[:20],
                "evidence": str(risk.get("evidence", ""))[:300],
                "recommended_fix": str(risk.get("recommended_fix", ""))[:300],
            }
        )
    return {
        "status": status,
        "risks": risks,
        "summary": str(result.get("summary", ""))[:500],
    }


def _needs_repair(report: dict[str, Any]) -> bool:
    if report.get("status") not in {"REVIEW_REQUIRED", "FAIL"}:
        return False
    repair_types = {
        "raw_filing_leak",
        "direct_identifier",
        "xbrl_garbage",
        "document_quality",
    }
    for risk in report.get("risks", []):
        if risk.get("risk_type") in repair_types:
            return True
    return False


def _apply_deterministic_repair(artifact_root: Path, risk: dict[str, Any]) -> bool:
    """Apply deterministic repair for a known file type. Returns True if repaired."""
    file_rel = risk.get("file", "")
    if not file_rel:
        return False
    path = artifact_root / file_rel
    if not path.is_file():
        return False
    filename = path.name
    if filename == "recent_event_summary.md":
        company_id = _infer_company_id_from_path(file_rel)
        body, _ = build_recent_event_summary("", company_id, "8-K")
        path.write_text(body, encoding="utf-8")
        return True
    if filename in _REPAIRABLE_FILENAMES:
        # Shorten to a safe generalized header; drop raw text.
        label = filename.replace("_summary.md", "").replace("_", " ").title()
        path.write_text(
            f"# {label}\n\nUNAVAILABLE: summary generalized to remove residual risk.\n",
            encoding="utf-8",
        )
        return True
    return False


def _infer_company_id_from_path(file_rel: str) -> str:
    parts = file_rel.split("/")
    for part in parts:
        if re.fullmatch(r"COMPANY_\d{3}", part):
            return part
    return "COMPANY_UNKNOWN"


def verify_public_artifact_with_nvidia(
    artifact_root: Path,
    max_chars_per_file: int = 6000,
    company_dir: Path | None = None,
) -> dict[str, Any]:
    """Verify the public artifact with NVIDIA. Returns a report dict.

    Never sends originals or private maps. If the provider is unavailable or
    returns malformed output, the report status is INCOMPLETE and no exception
    is raised. If ``company_dir`` is given, only that company's files are
    reviewed.
    """
    artifact_root = Path(artifact_root)
    file_chunks = _collect_public_files(artifact_root, max_chars_per_file, company_dir)
    if not file_chunks:
        return {
            "schema_version": "1.0",
            "status": "INCOMPLETE",
            "reason": "no public artifact files found to review",
            "risks": [],
            "files_reviewed": 0,
            "repair_passes": 0,
        }
    company_id = _infer_company_id_from_path(file_chunks[0]["file"])
    if not nvidia_available():
        return {
            "schema_version": "1.0",
            "company_id": company_id,
            "status": "INCOMPLETE",
            "reason": "provider credential not set",
            "risks": [],
            "files_reviewed": len(file_chunks),
            "repair_passes": 0,
        }
    report = _call_nvidia(company_id, file_chunks)
    report["schema_version"] = "1.0"
    report["company_id"] = company_id
    report["files_reviewed"] = len(file_chunks)
    repair_passes = 0
    while _needs_repair(report) and repair_passes < MAX_REPAIR_PASSES:
        repair_passes += 1
        repaired = []
        for risk in list(report.get("risks", [])):
            if _apply_deterministic_repair(artifact_root, risk):
                repaired.append(risk.get("file", ""))
        if not repaired:
            break
        file_chunks = _collect_public_files(artifact_root, max_chars_per_file, company_dir)
        report = _call_nvidia(company_id, file_chunks)
        report["schema_version"] = "1.0"
        report["company_id"] = company_id
        report["files_reviewed"] = len(file_chunks)
    report["repair_passes"] = repair_passes
    return report


def write_nvidia_artifact_report(report: dict[str, Any], qa_dir: Path) -> None:
    """Write NVIDIA review JSON and a human-readable markdown summary."""
    qa_dir = Path(qa_dir)
    qa_dir.mkdir(parents=True, exist_ok=True)
    safe_report = _redact_report(report)
    (qa_dir / "nvidia_artifact_review.json").write_text(
        json.dumps(safe_report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    status = str(safe_report.get("status", "INCOMPLETE")).upper()
    summary = str(safe_report.get("summary", ""))
    risks = safe_report.get("risks", [])
    lines = [
        "# NVIDIA Artifact Review Summary",
        "",
        f"* Status: {status}",
        f"* Files reviewed: {safe_report.get('files_reviewed', 0)}",
        f"* Repair passes: {safe_report.get('repair_passes', 0)}",
        "",
    ]
    if summary:
        lines.append(f"## Summary\n\n{summary}\n")
    if risks:
        lines.append("## Risks\n")
        for risk in risks:
            lines.append(
                f"* **{risk.get('file', '?')}** "
                f"({risk.get('risk_type', 'other')}, {risk.get('severity', 'low')}): "
                f"{risk.get('evidence', '')}"
            )
    (qa_dir / "nvidia_artifact_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _redact_report(report: dict[str, Any]) -> dict[str, Any]:
    """Ensure no API key or local path leaks into the report."""
    text = json.dumps(report)
    if "nvapi-" in text or "NVIDIA_API_KEY" in text or "/Users/" in text or "/content/" in text:
        cleaned = re.sub(r"nvapi-[A-Za-z0-9_-]+", "[REDACTED]", text)
        cleaned = re.sub(r"NVIDIA_API_KEY", "[REDACTED]", cleaned)
        cleaned = re.sub(r"/Users/[^\"\\s]+", "[REDACTED]", cleaned)
        cleaned = re.sub(r"/content/[^\"\\s]+", "[REDACTED]", cleaned)
        try:
            redacted: dict[str, Any] = json.loads(cleaned)
            return redacted
        except json.JSONDecodeError:
            return {"status": "INCOMPLETE", "reason": "report redaction failed"}
    return report


def rerun_nvidia_qa(
    artifact_root: Path,
) -> dict[str, Any]:
    """Rerun NVIDIA QA against an existing artifact's public files only.

    Does NOT recollect SEC/yfinance data. Does NOT touch originals or
    private_maps. Updates:
    - qa/nvidia_artifact_review.json
    - qa/nvidia_artifact_summary.md
    - anonymized/COMPANY_*/qa/nvidia_review.json
    """
    artifact_root = Path(artifact_root)
    anonymized_dir = artifact_root / "anonymized"
    if not anonymized_dir.is_dir():
        return {"status": "INCOMPLETE", "reason": "no anonymized directory found"}
    company_dirs = sorted(
        d for d in anonymized_dir.iterdir() if d.is_dir() and d.name.startswith("COMPANY_")
    )
    if not company_dirs:
        return {"status": "INCOMPLETE", "reason": "no company directories found"}
    last_report: dict[str, Any] = {
        "schema_version": "1.0",
        "status": "INCOMPLETE",
        "reason": "no companies processed",
        "risks": [],
        "files_reviewed": 0,
        "repair_passes": 0,
    }
    for company_dir in company_dirs:
        report = verify_public_artifact_with_nvidia(artifact_root, company_dir=company_dir)
        status = str(report.get("status", "INCOMPLETE")).upper()
        company_id = report.get("company_id", company_dir.name)
        decision = {
            "PASS": "PASS",
            "REVIEW_REQUIRED": "REVIEW_REQUIRED",
            "FAIL": "FAIL",
        }.get(status, "NOT_RUN")
        per_company = {
            "schema_version": "1.0",
            "company_id": company_id,
            "status": status,
            "decision": decision,
            "reason": report.get("reason", report.get("summary", "")),
            "files_reviewed": report.get("files_reviewed", 0),
            "repair_passes": report.get("repair_passes", 0),
            "risks": report.get("risks", []),
        }
        if "suppressed_pseudonym_risks" in report:
            per_company["suppressed_pseudonym_risks"] = report["suppressed_pseudonym_risks"]
        qa_dir = company_dir / "qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        (qa_dir / "nvidia_review.json").write_text(
            json.dumps(_redact_report(per_company), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        last_report = per_company
    write_nvidia_artifact_report(last_report, artifact_root / "qa")
    return last_report
