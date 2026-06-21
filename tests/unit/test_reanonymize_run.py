"""Tests for ``fenrix-synthetic.reanonymize`` (orchestrator + limits parser).

All tests are offline and deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fenrix_synthetic.reanonymize import (
    InvalidSourceRunError,
    ReanonymizeOrchestrator,
    apply_form_limits,
    parse_form_limits,
)
from fenrix_synthetic.reanonymize.limits import infer_form

# ── Fixtures / helpers ────────────────────────────────────────────────


@pytest.fixture
def source_run(tmp_path: Path) -> Path:
    """Materialise a minimal valid ``--source-run`` directory.

    Layout:
        <tmp>/run_summary.json
        <tmp>/originals/AAA/sec/filings/<form>.html
        <tmp>/originals/AAA/news/articles.json
        <tmp>/private_maps/AAA/identity_atlas.yaml
    """
    run = tmp_path / "src_run"
    originals = run / "originals" / "AAA"
    sec_dir = originals / "sec" / "filings"
    news_dir = originals / "news"
    private = run / "private_maps" / "AAA"
    sec_dir.mkdir(parents=True)
    news_dir.mkdir(parents=True)
    private.mkdir(parents=True)

    # SEC filings (HTML files for Three forms)
    (sec_dir / "AAA-2024-10-K-2024-12-31.htm").write_text(
        "<html><body>10-K annual report content for AAA.</body></html>",
        encoding="utf-8",
    )
    (sec_dir / "AAA-2024-10-Q-2024-09-30.htm").write_text(
        "<html><body>10-Q quarterly report content for AAA.</body></html>",
        encoding="utf-8",
    )
    (sec_dir / "AAA-2024-08-K-2024-07-20.htm").write_text(
        "<html><body>8-K current report content for AAA.</body></html>",
        encoding="utf-8",
    )
    (sec_dir / "AAA-2024-extra-form.htm").write_text(
        "<html><body>Unknown-form content.</body></html>",
        encoding="utf-8",
    )

    # News articles
    articles = [
        {
            "headline": f"AAA headline #{i}",
            "summary": f"AAA summary #{i}",
            "body": f"AAA body content for article {i}. The CEO of AAA said revenue grew.",
            "publisher": "Reuters",
            "canonical_url": f"https://example.com/news/{i}",
            "published_timestamp": "2025-01-01T00:00:00Z",
        }
        for i in range(7)
    ]
    (news_dir / "articles.json").write_text(json.dumps(articles), encoding="utf-8")

    # Identity atlas (kept tiny so we exercise the loader's tolerance)
    atlas = {
        "metadata": {
            "registry_id": "reg-AAA",
            "company_id": "AAA",
        },
        "entities": [
            {
                "entity_id": "ent_company",
                "entity_type": "company",
                "canonical_private_value": "Acme Co Holdings",
            }
        ],
        "aliases": [
            {
                "alias_id": "ali_co",
                "canonical_entity_id": "ent_company",
                "private_alias_value": "Acme",
                "entity_type": "company",
                "match_policy": "literal",
            }
        ],
    }
    import yaml

    (private / "identity_atlas.yaml").write_text(yaml.safe_dump(atlas), encoding="utf-8")

    # run_summary.json (required by Phase 1)
    (run / "run_summary.json").write_text(
        json.dumps(
            {
                "run_id": "test_run",
                "tickers": ["AAA"],
                "start_time": "2025-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    return run


# ── Limits parser ──────────────────────────────────────────────────────


class TestParseFormLimits:
    def test_parses_multiple_forms(self) -> None:
        assert parse_form_limits("10-K:1,10-Q:1,8-K:1") == {
            "10-K": 1,
            "10-Q": 1,
            "8-K": 1,
        }

    def test_default_count_when_omitted(self) -> None:
        assert parse_form_limits("10-K") == {"10-K": 1}
        assert parse_form_limits("10-Q:3") == {"10-Q": 3}

    def test_tolerates_whitespace(self) -> None:
        assert parse_form_limits(" 10-K: 2 , 10-Q : 1 ") == {"10-K": 2, "10-Q": 1}

    def test_empty_or_none_returns_empty(self) -> None:
        assert parse_form_limits(None) == {}
        assert parse_form_limits("") == {}
        assert parse_form_limits("   ") == {}

    def test_rejects_invalid_token(self) -> None:
        with pytest.raises(ValueError):
            parse_form_limits("10-K:abc")
        with pytest.raises(ValueError):
            parse_form_limits("not_a_form!:1")
        with pytest.raises(ValueError):
            parse_form_limits("10-K:0")

    def test_larger_count_wins_on_repeat(self) -> None:
        assert parse_form_limits("10-K:1,10-K:5") == {"10-K": 5}


class TestInferForm:
    def test_recognises_common_forms(self) -> None:
        assert infer_form("AAA-2024-10K.htm") == "10-K"
        assert infer_form("AAA-2024-10_Q.htm") == "10-Q"
        assert infer_form("AAA-2024-8-K.htm") == "8-K"

    def test_returns_none_when_unknown(self) -> None:
        assert infer_form("AAA-notice.htm") is None


class TestApplyFormLimits:
    def test_no_limits_returns_pairs_with_none_forms(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"f{i}.htm" for i in range(3)]
        result = apply_form_limits(paths, {})
        # Each path is returned as (None, path) since none of the dummy
        # filenames match a known SEC form.
        assert result == [(None, sorted(paths)[i]) for i in range(3)]

    def test_inferred_form_appears_in_pairs(self, tmp_path: Path) -> None:
        paths = [
            tmp_path / "AAA-2024-10K.htm",
            tmp_path / "AAA-2024-10Q.htm",
        ]
        result = apply_form_limits(paths, {"10-K": 1, "10-Q": 1})
        assert all(isinstance(item, tuple) and len(item) == 2 for item in result)
        assert ("10-K", tmp_path / "AAA-2024-10K.htm") in result
        assert ("10-Q", tmp_path / "AAA-2024-10Q.htm") in result


# ── Orchestrator: validation ──────────────────────────────────────────


class TestValidation:
    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        o = ReanonymizeOrchestrator(
            source_run=tmp_path / "does_not_exist",
            output_root=tmp_path / "out",
            limit_forms=None,
            limit_news=5,
        )
        with pytest.raises(InvalidSourceRunError):
            o.validate()

    def test_missing_run_summary_raises(self, tmp_path: Path) -> None:
        (tmp_path / "x" / "originals").mkdir(parents=True)
        o = ReanonymizeOrchestrator(
            source_run=tmp_path / "x",
            output_root=tmp_path / "out",
            limit_forms=None,
            limit_news=5,
        )
        with pytest.raises(InvalidSourceRunError):
            o.validate()

    def test_multiple_tickers_raises(self, tmp_path: Path) -> None:
        run = tmp_path / "r"
        for t in ("AAA", "BBB"):
            (run / "originals" / t / "sec" / "filings").mkdir(parents=True)
        (run / "run_summary.json").write_text("{}", encoding="utf-8")
        o = ReanonymizeOrchestrator(
            source_run=run,
            output_root=tmp_path / "out",
            limit_forms=None,
            limit_news=5,
        )
        with pytest.raises(InvalidSourceRunError, match="multiple tickers"):
            o.validate()


# ── Orchestrator: end-to-end happy path ───────────────────────────────


class TestRunHappyPath:
    def test_writes_required_outputs(self, source_run: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        o = ReanonymizeOrchestrator(
            source_run=source_run,
            output_root=out,
            limit_forms="10-K:1,10-Q:1,8-K:1",
            limit_news=5,
        )
        o.run()

        # Required outputs exist
        public_root = out / "public"
        qa_root = out / "qa"
        sec_dir = public_root / "surrogates" / "sec"
        news_dir = public_root / "surrogates" / "news"
        numeric_dir = public_root / "numeric" / "classroom_safe"
        assert sec_dir.is_dir()
        assert news_dir.is_dir()
        for f in (
            "annual_statements.json",
            "quarterly_statements.json",
            "weekly_features.json",
            "ratio_and_regime_index.json",
        ):
            assert (numeric_dir / f).is_file(), f"missing numeric file {f}"
        for f in (
            "direct_privacy_report.json",
            "semantic_privacy_report.json",
            "nvidia_attack_report.json",
            "utility_report.json",
            "release_gate.json",
        ):
            assert (qa_root / f).is_file(), f"missing qa file {f}"

        # release_gate.json has the mandated keys
        gate = json.loads((qa_root / "release_gate.json").read_text())
        assert gate["beta_status"] == "INCOMPLETE"
        assert gate["release_safe"] is False
        assert (
            "stubs_enforced" in gate
        )  # release_gate.json has the mandated keys. ``semantic`` is no
        # longer in ``stubs_enforced`` because Phase 8 runs the real
        # 4-attack suite; only NVIDIA review remains stubbed.
        gate = json.loads((qa_root / "release_gate.json").read_text())
        assert gate["beta_status"] == "INCOMPLETE"
        assert gate["release_safe"] is False
        assert "stubs_enforced" in gate
        assert "nvidia" in gate["stubs_enforced"]
        assert "semantic" not in gate["stubs_enforced"]
        # Four-decision naming fields are present and consistent.
        assert gate["direct_privacy_decision"] == "PASS"
        assert gate["nvidia_decision"] == "NOT_RUN"
        assert gate["overall_release_decision"] in ("PASS", "FAIL", "INCOMPLETE")

        # Limits were honoured — news surrogates slice to <= limit_news
        news_files = list(news_dir.glob("*_surrogate.md"))
        assert 0 < len(news_files) <= 5

    def test_gate_never_passes_when_stubs_present(self, source_run: Path, tmp_path: Path) -> None:
        out = tmp_path / "out2"
        o = ReanonymizeOrchestrator(
            source_run=source_run,
            output_root=out,
            limit_forms=None,
            limit_news=2,
        )
        o.run()
        gate = json.loads((out / "qa" / "release_gate.json").read_text())
        assert gate["release_safe"] is False
        assert gate["beta_status"] == "INCOMPLETE"


# ── CLI smoke ─────────────────────────────────────────────────────────


class TestCLISmoke:
    def test_result_contains_ticker_and_limits(self, source_run: Path, tmp_path: Path) -> None:
        o = ReanonymizeOrchestrator(
            source_run=source_run,
            output_root=tmp_path / "out3",
            limit_forms=None,
            limit_news=5,
        )
        run_result = o.run()
        assert run_result["ticker"] == "AAA"
        # Limits surfaced in orchestrator result.
        assert "limits" in run_result
        assert run_result["limits"]["news"] == 5
        # release_gate dict is propagated.
        assert run_result["release_gate"]["beta_status"] == "INCOMPLETE"


# ── Orchestrator: limit enforcement regression ────────────────────────


class TestLimitEnforcement:
    """``--limit-forms`` must actually restrict the SEC surrogate count.

    These tests guard the contract: if the cap for a form is ``N`` and
    more than ``N`` filings of that form exist in ``--source-run``,
    the orchestrator must produce AT MOST ``N`` surrogate ``.md`` files
    for that form — not all of them.
    """

    def _build_source_run(
        self,
        tmp_path: Path,
        filings: dict[str, list[str]],
        ticker: str = "AAA",
    ) -> Path:
        """Return a minimal valid ``--source-run`` with explicit filings.

        ``filings`` maps ``{form_label: [filename, ...]}``. Each filename
        is written under ``originals/<TICKER>/sec/filings/``.
        """
        import yaml

        run = tmp_path / "src_run"
        originals = run / "originals" / ticker
        sec_dir = originals / "sec" / "filings"
        private = run / "private_maps" / ticker
        sec_dir.mkdir(parents=True)
        private.mkdir(parents=True)

        for _form, names in filings.items():
            for name in names:
                (sec_dir / name).write_text(
                    f"<html><body>{name} running over long-term trends.</body></html>",
                    encoding="utf-8",
                )

        # Atlas MUST contain at least one real entity + alias so the
        # orchestrator's fail-closed ``aliases_loaded == 0`` contract
        # does NOT block limit-enforcement tests. These tests intentionally
        # focus on form-cap semantics, not on registry loading, so the
        # placeholder atlas is small but spec-conformant.
        atlas = {
            "metadata": {"registry_id": f"reg-{ticker}", "company_id": ticker},
            "entities": [
                {
                    "entity_id": f"ent_{ticker}_co",
                    "entity_type": "company",
                    "canonical_private_value": "TestCo",
                }
            ],
            "aliases": [
                {
                    "alias_id": f"a_{ticker}_co",
                    "canonical_entity_id": f"ent_{ticker}_co",
                    "private_alias_value": "TestCo",
                    "entity_type": "company",
                    "match_policy": "literal",
                }
            ],
        }
        (private / "identity_atlas.yaml").write_text(yaml.safe_dump(atlas), encoding="utf-8")
        (run / "run_summary.json").write_text(
            json.dumps({"run_id": "limit_test", "tickers": [ticker]}),
            encoding="utf-8",
        )
        return run

    def test_cap_excludes_extra_filings_of_same_form(self, tmp_path: Path) -> None:
        # Two 10-K filings, one 10-Q, one 8-K. Limit only the 10-Ks.
        source_run = self._build_source_run(
            tmp_path,
            filings={
                "10-K": ["AAA-2023-10K.htm", "AAA-2024-10K.htm"],
                "10-Q": ["AAA-2024-10Q.htm"],
                "8-K": ["AAA-2024-8K.htm"],
            },
        )
        out = tmp_path / "out"
        o = ReanonymizeOrchestrator(
            source_run=source_run,
            output_root=out,
            limit_forms="10-K:1",
            limit_news=0,
        )
        result = o.run()

        sec_dir = out / "public" / "surrogates" / "sec"
        sec_files = list(sec_dir.glob("*.md"))
        # Exactly 3 surrogates: 1 10-K (limit) + 1 10-Q + 1 8-K (uncapped).
        # Without enforcement the orchestrator would emit 4 surrogates.
        assert len(sec_files) == 3, (
            f"--limit-forms 10-K:1 not enforced: got {len(sec_files)} surrogates, "
            f"expected 3. Forms: {sec_files}"
        )

        # And the gate should report the form coverage honouring the cap.
        gate = json.loads((out / "qa" / "release_gate.json").read_text())
        surrogate_counts = gate["surrogate_output_counts"]
        assert surrogate_counts["sec_md_files"] == 3
        assert surrogate_counts["sec_forms"].get("10-K") == 1
        assert surrogate_counts["sec_forms"].get("10-Q") == 1
        assert surrogate_counts["sec_forms"].get("8-K") == 1

        # The orchestrator's ``written`` payload should reflect the cap.
        assert result["written"]["sec_surrogates"] and len(result["written"]["sec_surrogates"]) == 3

    def test_empty_selection_produces_no_sec_surrogates(self, tmp_path: Path) -> None:
        # No filings at all — still a valid source-run shape.
        source_run = self._build_source_run(tmp_path, filings={})
        out = tmp_path / "out"
        o = ReanonymizeOrchestrator(
            source_run=source_run,
            output_root=out,
            limit_forms="10-K:5",
            limit_news=3,
        )
        o.run()
        sec_dir = out / "public" / "surrogates" / "sec"
        assert list(sec_dir.glob("*.md")) == []


# ── TextAnonymizer: selected_paths argument ──────────────────────────


class TestTextAnonymizerSelectedPaths:
    """Ensure the optional ``selected_paths`` argument actually restricts work."""

    def test_selected_paths_filters_iteration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Processing only the explicit paths avoids work done for the rest."""
        from fenrix_synthetic.anonymization.text_anonymizer import TextAnonymizer

        # Build a tiny ``originals/sec/filings`` tree with two HTML files.
        originals = tmp_path / "originals"
        filings_dir = originals / "sec" / "filings"
        filings_dir.mkdir(parents=True)
        (filings_dir / "AAA-2024-10K.htm").write_text(
            "<html><body>kept 10-K content</body></html>", encoding="utf-8"
        )
        (filings_dir / "AAA-2024-10Q.htm").write_text(
            "<html><body>kept 10-Q content</body></html>", encoding="utf-8"
        )
        (filings_dir / "AAA-2024-8K.htm").write_text(
            "<html><body>should be SKIPPED</body></html>", encoding="utf-8"
        )

        # Minimal valid atlas so masking has a registry. The
        # anonymizer reads ``private_maps_dir / identity_atlas.yaml``,
        # so the test wires ``private_maps_dir = private / AAA`` (the
        # same convention used by the orchestrator + older fixtures).
        import yaml

        private = tmp_path / "private"
        atlas = {
            "metadata": {"registry_id": "reg-AAA", "company_id": "AAA"},
            "entities": [],
            "aliases": [],
        }
        atlas_dir = private / "AAA"
        atlas_dir.mkdir(parents=True, exist_ok=True)
        (atlas_dir / "identity_atlas.yaml").write_text(yaml.safe_dump(atlas), encoding="utf-8")

        out_dir = tmp_path / "out"
        anonymizer = TextAnonymizer(
            ticker="AAA",
            originals_dir=originals,
            anonymized_dir=out_dir,
            # The anonymizer appends /identity_atlas.yaml to this
            # directory, so it MUST point at the directory that
            # actually contains the atlas file (private / AAA).
            private_maps_dir=atlas_dir,
        )

        # Only the first two HTMLs should be processed.
        manifest = anonymizer.anonymize_all(
            selected_paths=[
                filings_dir / "AAA-2024-10K.htm",
                filings_dir / "AAA-2024-10Q.htm",
            ]
        )
        md_files = sorted(out_dir.glob("sec/*.md"))
        # Exactly 2 surrogates; the 3rd HTML was passed by.
        assert len(md_files) == 2, (
            f"Selected paths not honoured: produced {[p.name for p in md_files]}"
        )
        # The skipped HTML's distinctive phrase must not leak.
        leaked = "".join(p.read_text(encoding="utf-8") for p in md_files)
        assert "should be SKIPPED" not in leaked
        # And the manifest reflects 2 artifacts.
        assert len(manifest) == 2
