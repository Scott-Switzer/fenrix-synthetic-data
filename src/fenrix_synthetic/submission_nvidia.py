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
from pathlib import Path
from typing import Any

import httpx

from .submission_quality import build_recent_event_summary

NVIDIA_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_MODEL = "meta/llama-3.1-70b-instruct"
MAX_REPAIR_PASSES = 2

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
    "raw-filing-like, XBRL-garbage, or unusable. "
    "Respond with JSON only matching the schema: "
    '{"status":"PASS"|"REVIEW_REQUIRED"|"FAIL","risks":['
    '{"file":"...","risk_type":"direct_identifier|search_fingerprint|'
    'numeric_fingerprint|raw_filing_leak|xbrl_garbage|document_quality|other",'
    '"severity":"low|medium|high","evidence":"short non-sensitive excerpt",'
    '"recommended_fix":"..."}],"summary":"..."}'
)


def nvidia_available() -> bool:
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
    artifact_root: Path, max_chars_per_file: int = 6000
) -> list[dict[str, str]]:
    """Collect sanitized public artifact text. Never includes originals/private maps."""
    files: list[dict[str, str]] = []
    public_dir = artifact_root / "anonymized"
    if not public_dir.is_dir():
        return files
    for path in sorted(public_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".md", ".json", ".csv", ".txt"}:
            continue
        rel = path.relative_to(artifact_root).as_posix()
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
    """Call NVIDIA chat completions. Returns parsed JSON or error dict."""
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
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(NVIDIA_ENDPOINT, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return {
            "status": "INCOMPLETE",
            "reason": f"provider request failed: {type(exc).__name__}",
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
    return parsed


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
    artifact_root: Path, max_chars_per_file: int = 6000
) -> dict[str, Any]:
    """Verify the public artifact with NVIDIA. Returns a report dict.

    Never sends originals or private maps. If the provider is unavailable or
    returns malformed output, the report status is INCOMPLETE and no exception
    is raised.
    """
    artifact_root = Path(artifact_root)
    file_chunks = _collect_public_files(artifact_root, max_chars_per_file)
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
        file_chunks = _collect_public_files(artifact_root, max_chars_per_file)
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
