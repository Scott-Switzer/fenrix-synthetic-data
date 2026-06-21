from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType


def load_builder() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "build_submission_fast.py"
    spec = importlib.util.spec_from_file_location("build_submission_fast", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_submission_fast"] = module
    spec.loader.exec_module(module)
    return module


def write_public_shell(root: Path, builder: ModuleType) -> None:
    public = root / "anonymized" / "COMPANY_001" / "metrics"
    public.mkdir(parents=True)
    (root / "qa").mkdir()
    for name in builder.PUBLIC_TOP_LEVEL_FILES:
        (root / name).write_text("safe public content\n", encoding="utf-8")
    (public / "metrics_manifest.json").write_text(
        '{"company_id":"COMPANY_001"}\n', encoding="utf-8"
    )
    (root / "qa" / "release_gate.json").write_text('{"artifact_built":true}\n', encoding="utf-8")


def test_script_line_count_cap() -> None:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "build_submission_fast.py"
    assert len(script_path.read_text(encoding="utf-8").splitlines()) <= 350


def test_deterministic_pseudonyms_are_stable() -> None:
    builder = load_builder()
    first = builder.CompanyContext("CL", 1, "0000021665")
    second = builder.CompanyContext("CL", 1, "0000021665")

    assert first.assign("Colgate-Palmolive Company", "COMPANY") == "COMPANY_001"
    assert first.assign("CL", "TICKER") == "TICKER_001"
    assert first.assign("0000021665", "CIK") == "CIK_001"
    assert first.assign("colgate.com", "DOMAIN") == "DOMAIN_001"
    assert second.assign("colgate.com", "DOMAIN") == "DOMAIN_001"


def test_heading_only_sec_output_is_rejected() -> None:
    builder = load_builder()
    body, reason = builder.summarize(
        "Item 1. Business\nItem 1A. Risk Factors\nPage 3", {}, "Business"
    )

    assert "UNAVAILABLE" in body
    assert "meaningful characters" in reason or "heading-only" in reason


def test_xbrl_taxonomy_garbage_is_rejected() -> None:
    builder = load_builder()
    noisy = "\n".join(
        [
            "us-gaap:Revenue xbrli:contextRef iso4217:USD TICKER_FAKE:SegmentMember",
            "utr:shares srt:ProductOrServiceMember us-gaap:Assets xbrli:unitRef",
            "This line has enough narrative text to look tempting but should still be rejected.",
        ]
        * 15
    )
    body, reason = builder.summarize(noisy, {}, "Financial")

    assert "UNAVAILABLE" in body
    assert "taxonomy" in reason


def test_raw_8k_identity_fields_are_scrubbed() -> None:
    builder = load_builder()
    ctx = builder.CompanyContext("HBAN", 1, "0000049196")
    builder.seed_context(ctx)
    raw = """
    Exact name of registrant: Huntington Bancshares Incorporated
    41 South High Street, Columbus, Ohio 43287
    Phone: (614) 480-2265
    IRS Employer Identification No. 31-0724920
    Commission File Number 001-03482
    /s/ Stephen Steinour, Chief Executive Officer and Director
    """

    clean = builder.scrub_text(raw, ctx.private_map)

    assert "Huntington" not in clean
    assert "41 South High" not in clean
    assert "(614)" not in clean
    assert "31-0724920" not in clean
    assert "001-03482" not in clean
    assert "Stephen Steinour" not in clean


def test_raw_news_headline_url_and_ticker_prose_not_exported(tmp_path: Path, monkeypatch) -> None:
    builder = load_builder()
    import fenrix_synthetic.submission_sources as submission_sources

    class FakeTicker:
        news = [
            {
                "content": {
                    "title": "HBAN shares jump after Huntington update",
                    "summary": "Read more at https://example.com/HBAN for Huntington details.",
                }
            }
        ]

    class FakeYf:
        @staticmethod
        def Ticker(ticker: str) -> FakeTicker:
            return FakeTicker()

    monkeypatch.setattr(submission_sources, "load_yfinance", lambda: FakeYf)
    ctx = builder.CompanyContext("HBAN", 1, "0000049196")
    builder.seed_context(ctx)

    status, _, _, _ = builder.collect_news("HBAN", ctx, tmp_path, 5)
    payload = json.loads(
        (tmp_path / "anonymized" / "COMPANY_001" / "news" / "news_briefs.json").read_text()
    )
    text = json.dumps(payload)

    assert status == "OK"
    assert "HBAN shares jump" not in text
    assert "https://example.com" not in text
    assert "HBAN" not in text
    assert "Huntington" not in text
    assert "sanitized_summary" in text


def test_exact_raw_fundamentals_not_exported(tmp_path: Path) -> None:
    builder = load_builder()

    class FakeRow:
        iloc = [123456789012]

    class FakeFrame:
        empty = False

        @staticmethod
        def iterrows() -> list[tuple[str, FakeRow]]:
            return [("Total Revenue", FakeRow())]

    class FakeTicker:
        income_stmt = FakeFrame()
        balance_sheet = None
        cashflow = None

    ctx = builder.CompanyContext("CL", 1, "0000021665")
    builder.write_binned_fundamentals(tmp_path / "fundamentals_binned.csv", FakeTicker(), ctx)
    text = (tmp_path / "fundamentals_binned.csv").read_text(encoding="utf-8")

    assert "123456789012" not in text
    assert "mega" in text


def test_zip_excludes_private_original_and_raw_paths(tmp_path: Path) -> None:
    builder = load_builder()
    write_public_shell(tmp_path, builder)
    (tmp_path / "private_maps" / "CL").mkdir(parents=True)
    (tmp_path / "private_maps" / "CL" / "identity_map.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "originals" / "CL").mkdir(parents=True)
    (tmp_path / "originals" / "CL" / "source.txt").write_text("source\n", encoding="utf-8")
    (tmp_path / "smoke_excerpts").mkdir()
    (tmp_path / "smoke_excerpts" / "raw.txt").write_text("raw\n", encoding="utf-8")

    zip_path = builder.package_zip(tmp_path)
    validation = builder.validate_zip(zip_path)
    names = set(zipfile.ZipFile(zip_path).namelist())

    assert validation.ok
    assert not any(
        name.startswith(("private_maps/", "originals/", "smoke_excerpts/")) for name in names
    )


def test_zip_validation_rejects_api_key_and_local_path(tmp_path: Path) -> None:
    builder = load_builder()
    write_public_shell(tmp_path, builder)
    (tmp_path / "README.md").write_text(
        "safe text nvapi-testsecret123 /Users/example/project\n",
        encoding="utf-8",
    )

    validation = builder.validate_zip(builder.package_zip(tmp_path))

    assert not validation.ok
    assert validation.api_key_hits
    assert validation.local_path_hits
