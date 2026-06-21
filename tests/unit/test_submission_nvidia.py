"""Focused tests for the minimal NVIDIA artifact verifier.

Covers:
* missing NVIDIA_API_KEY returns INCOMPLETE and does not fail the build
* verifier never includes originals/private_maps in the payload file list
* parser handles mocked PASS JSON
* parser handles malformed provider response safely
* build still writes nvidia_artifact_review.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fenrix_synthetic.submission_nvidia import (
    _collect_public_files,
    _parse_review_json,
    nvidia_available,
    verify_public_artifact_with_nvidia,
    write_nvidia_artifact_report,
)


def _make_public_artifact(root: Path, company_id: str = "COMPANY_001") -> None:
    public = root / "anonymized" / company_id / "sec"
    public.mkdir(parents=True)
    (public / "recent_event_summary.md").write_text(
        "# Recent Event Summary\n\nSafe sanitized content.\n", encoding="utf-8"
    )
    (public / "business_summary.md").write_text(
        "# Business Summary\n\nSafe content.\n", encoding="utf-8"
    )


def _make_private_artifact(root: Path) -> None:
    priv = root / "private_maps" / "CL"
    priv.mkdir(parents=True)
    (priv / "identity_map.json").write_text(
        '{"private": "SECRET_VALUE_NVAPI-LEAK"}\n', encoding="utf-8"
    )
    orig = root / "originals" / "CL"
    orig.mkdir(parents=True)
    (orig / "raw.txt").write_text("raw source content\n", encoding="utf-8")


def test_missing_nvidia_api_key_returns_incomplete(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    _make_public_artifact(tmp_path)
    report = verify_public_artifact_with_nvidia(tmp_path)
    assert report["status"] == "INCOMPLETE"
    assert "credential" in report["reason"]
    assert report["files_reviewed"] >= 1
    assert report["repair_passes"] == 0


def test_nvidia_available_reflects_env(monkeypatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    assert nvidia_available() is False
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-secret-12345")
    assert nvidia_available() is True


def test_verifier_never_includes_originals_or_private_maps(tmp_path: Path) -> None:
    _make_public_artifact(tmp_path)
    _make_private_artifact(tmp_path)
    files = _collect_public_files(tmp_path)
    rels = [f["file"] for f in files]
    assert any("anonymized/COMPANY_001/sec" in r for r in rels)
    assert not any("private_maps/" in r for r in rels)
    assert not any("originals/" in r for r in rels)
    for f in files:
        assert "nvapi-" not in f["text"]
        assert "NVIDIA_API_KEY" not in f["text"]
        assert "/Users/" not in f["text"]
        assert "/content/" not in f["text"]


def test_parser_handles_mocked_pass_json() -> None:
    content = '{"status":"PASS","risks":[],"summary":"No residual identity risk found."}'
    parsed = _parse_review_json(content)
    assert parsed is not None
    assert parsed["status"] == "PASS"
    assert parsed["risks"] == []
    assert "No residual" in parsed["summary"]


def test_parser_handles_risks_with_fields() -> None:
    content = json.dumps(
        {
            "status": "REVIEW_REQUIRED",
            "risks": [
                {
                    "file": "anonymized/COMPANY_001/sec/recent_event_summary.md",
                    "risk_type": "raw_filing_leak",
                    "severity": "high",
                    "evidence": "some excerpt",
                    "recommended_fix": "regenerate from template",
                }
            ],
            "summary": "one risk",
        }
    )
    parsed = _parse_review_json(content)
    assert parsed is not None
    assert parsed["status"] == "REVIEW_REQUIRED"
    assert len(parsed["risks"]) == 1
    assert parsed["risks"][0]["risk_type"] == "raw_filing_leak"


def test_parser_handles_malformed_provider_response_safely() -> None:
    assert _parse_review_json("") is None
    assert _parse_review_json("not json at all") is None
    assert _parse_review_json("```json\n{not valid}\n```") is None
    assert _parse_review_json('{"status":"WEIRD"}') is None
    parsed = _parse_review_json('{"status":"PASS","risks":"notalist","summary":""}')
    assert parsed is not None
    assert parsed["risks"] == []


def test_write_nvidia_artifact_report_creates_files(tmp_path: Path) -> None:
    report = {
        "schema_version": "1.0",
        "status": "PASS",
        "company_id": "COMPANY_001",
        "files_reviewed": 2,
        "repair_passes": 0,
        "risks": [],
        "summary": "clean",
    }
    write_nvidia_artifact_report(report, tmp_path / "qa")
    review_path = tmp_path / "qa" / "nvidia_artifact_review.json"
    summary_path = tmp_path / "qa" / "nvidia_artifact_summary.md"
    assert review_path.is_file()
    assert summary_path.is_file()
    loaded = json.loads(review_path.read_text(encoding="utf-8"))
    assert loaded["status"] == "PASS"
    summary_text = summary_path.read_text(encoding="utf-8")
    assert "NVIDIA Artifact Review Summary" in summary_text
    assert "Status: PASS" in summary_text


def test_build_writes_nvidia_artifact_review_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    import fenrix_synthetic.submission_package as submission_package

    ctx = submission_package.CompanyContext("CL", 1, "0000021665")
    submission_package.seed_context(ctx)
    _make_public_artifact(tmp_path, "COMPANY_001")
    qa = submission_package.run_nvidia_qa("CL", ctx, ["sample"], "auto", tmp_path)
    assert qa["status"] == "INCOMPLETE"
    review_path = tmp_path / "qa" / "nvidia_artifact_review.json"
    summary_path = tmp_path / "qa" / "nvidia_artifact_summary.md"
    assert review_path.is_file()
    assert summary_path.is_file()
    text = review_path.read_text(encoding="utf-8")
    assert "nvapi-" not in text
    assert "NVIDIA_API_KEY" not in text


def test_repair_loop_regenerates_recent_event_summary(tmp_path: Path, monkeypatch) -> None:
    """When NVIDIA flags a raw_filing_leak, the repair loop regenerates the file."""
    leaky_path = tmp_path / "anonymized" / "COMPANY_007" / "sec" / "recent_event_summary.md"
    leaky_path.parent.mkdir(parents=True)
    leaky_path.write_text(
        "# Recent Event\n\nIRS Employer Identification No. 04-2207613\n", encoding="utf-8"
    )
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-fake-key")

    def fake_call(company_id: str, chunks: list[dict[str, str]]) -> dict[str, Any]:
        first = {
            "status": "REVIEW_REQUIRED",
            "risks": [
                {
                    "file": "anonymized/COMPANY_007/sec/recent_event_summary.md",
                    "risk_type": "raw_filing_leak",
                    "severity": "high",
                    "evidence": "IRS Employer",
                    "recommended_fix": "regenerate",
                }
            ],
            "summary": "leak found",
        }
        second = {"status": "PASS", "risks": [], "summary": "clean after repair"}
        fake_call.calls += 1
        return first if fake_call.calls == 1 else second

    fake_call.calls = 0
    monkeypatch.setattr("fenrix_synthetic.submission_nvidia._call_nvidia", fake_call)
    report = verify_public_artifact_with_nvidia(tmp_path)
    assert report["status"] == "PASS"
    assert report["repair_passes"] == 1
    repaired = leaky_path.read_text(encoding="utf-8")
    assert "IRS Employer" not in repaired
    assert "Recent Event Summary" in repaired
