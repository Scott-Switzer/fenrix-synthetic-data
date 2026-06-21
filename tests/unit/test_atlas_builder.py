"""Tests for the ``DirectIdentifierAtlasBuilder`` and harvest coverage.

These tests cover the user-spec contract:

- BL-3: builder must harvest from run_summary + atlas_yaml + filings + news.
- BL-4: ``qa/direct_identifier_coverage_report.json`` schema with
  per-type counts; ``aliases_built <= 6`` → ``critical`` warning.
- BL-4: ``to_report()`` MUST NOT include raw identifiers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fenrix_synthetic.reanonymize.atlas_builder import (
    AtlasHarvestReport,
    CRITICAL_ALIAS_THRESHOLD,
    DirectIdentifierAtlasBuilder,
)


def _build_source_run(
    tmp_path: Path,
    *,
    ticker: str = "AAA",
    filings: list[tuple[str, str]] | None = None,
    atlas_entities: list[dict] | None = None,
    atlas_aliases: list[dict] | None = None,
    run_summary: dict | None = None,
    news_articles: list[dict] | None = None,
) -> Path:
    """Materialise a minimal valid ``source_run`` for the builder."""
    run = tmp_path / "src"
    originals = run / "originals" / ticker
    run.mkdir(parents=True)
    (originals / "sec" / "filings").mkdir(parents=True)
    (originals / "news").mkdir(parents=True)
    (run / "private_maps" / ticker).mkdir(parents=True)
    (run / "config").mkdir(parents=True)
    (run / "manifests").mkdir(parents=True)
    (run / "qa").mkdir(parents=True)
    (run / "anonymized" / ticker).mkdir(parents=True)

    # run_summary.json — omit explicit ``ticker`` key so the harvester
    # does NOT bias the bucket count upward. The validator still has
    # access to ``tickers: [ticker]`` for routing.
    summary = run_summary or {"run_id": "x", "tickers": [ticker]}
    (run / "run_summary.json").write_text(json.dumps(summary), encoding="utf-8")

    # SEC filings
    filings = filings or []
    for name, body in filings:
        (originals / "sec" / "filings" / name).write_text(body, encoding="utf-8")

    # Atlas YAML
    import yaml

    atlas = {
        "metadata": {"registry_id": f"reg-{ticker}", "company_id": ticker},
        "entities": atlas_entities
        or [
            {
                "entity_id": "e1",
                "entity_type": "company",
                "canonical_private_value": "Atlas Co Holdings",
            }
        ],
        "aliases": atlas_aliases
        or [
            {
                "alias_id": "a1",
                "canonical_entity_id": "e1",
                "private_alias_value": "Atlas Co",
                "entity_type": "company",
                "match_policy": "literal",
            }
        ],
    }
    (run / "private_maps" / ticker / "identity_atlas.yaml").write_text(
        yaml.safe_dump(atlas), encoding="utf-8"
    )

    # News articles
    if news_articles is not None:
        (originals / "news" / "articles.json").write_text(
            json.dumps(news_articles), encoding="utf-8"
        )
    return run


class TestAtlasBuilderHarvest:
    def test_run_summary_ticker_lands_in_ticker_bucket(self, tmp_path: Path) -> None:
        run = _build_source_run(tmp_path, ticker="NVDA")
        builder = DirectIdentifierAtlasBuilder(ticker="NVDA", source_run=run)
        report = builder.harvest()
        assert (
            "NVDA" in report.to_report()["ticker"]
            or len(report.identifier_types.get("ticker", 0)) >= 1
        )
        # Ticker bucket must have NVDA.
        assert report.identifier_types.get("ticker", 0) >= 1

    def test_existing_atlas_yaml_entries_propagate(self, tmp_path: Path) -> None:
        run = _build_source_run(
            tmp_path,
            ticker="AAA",
            atlas_entities=[
                {
                    "entity_id": "ent_co",
                    "entity_type": "company",
                    "canonical_private_value": "BigCo Holdings",
                },
                {
                    "entity_id": "ent_brand",
                    "entity_type": "brand",
                    "canonical_private_value": "BigBrand",
                },
            ],
            atlas_aliases=[
                {
                    "alias_id": "a_co",
                    "canonical_entity_id": "ent_co",
                    "private_alias_value": "BigCo",
                    "entity_type": "company",
                    "match_policy": "literal",
                },
            ],
        )
        builder = DirectIdentifierAtlasBuilder(ticker="AAA", source_run=run)
        report = builder.harvest()
        # ``aliases_built`` includes both entities + aliases + run_summary ticker.
        assert report.aliases_built >= 2

    def test_filing_header_accession_regex_populates_accession_bucket(self, tmp_path: Path) -> None:
        run = _build_source_run(
            tmp_path,
            ticker="AAA",
            filings=[
                (
                    "AAA-2024-10K.htm",
                    "<html><body>Acc: 0001045810-24-000029 filing for AAA.</body></html>",
                ),
            ],
        )
        builder = DirectIdentifierAtlasBuilder(ticker="AAA", source_run=run)
        report = builder.harvest()
        # Both formatted + bare accession should appear.
        assert "accession" in report.identifier_types
        assert report.identifier_types["accession"] >= 2

    def test_news_metadata_publisher_and_url(self, tmp_path: Path) -> None:
        news = [
            {
                "headline": "AAA posts record revenue, beats estimates again this quarter",
                "publisher": "Reuters",
                "canonical_url": "https://reuters.example/aaa-q1",
            }
        ]
        run = _build_source_run(tmp_path, ticker="AAA", news_articles=news)
        builder = DirectIdentifierAtlasBuilder(ticker="AAA", source_run=run)
        report = builder.harvest()
        assert report.identifier_types.get("brand", 0) >= 1
        assert report.identifier_types.get("url", 0) >= 1

    def test_to_report_redacts_raw_identifiers(self, tmp_path: Path) -> None:
        run = _build_source_run(
            tmp_path,
            ticker="AAA",
            atlas_entities=[
                {
                    "entity_id": "ent_secret",
                    "entity_type": "company",
                    "canonical_private_value": "TopSecretCompanyName",
                }
            ],
        )
        builder = DirectIdentifierAtlasBuilder(ticker="AAA", source_run=run)
        report = builder.harvest()
        joined = json.dumps(report.to_report())
        # The secret value MUST NOT leak into the public report.
        assert "TopSecretCompanyName" not in joined
        # At most a count summary is emitted.
        assert "identifier_types" in joined


class TestAtlasBuilderCoverageWarnings:
    def test_zero_aliases_is_critical(self, tmp_path: Path) -> None:
        # Build source-run with NO atlas entities/aliases and no filing data.
        run = tmp_path / "src"
        (run / "private_maps" / "AAA").mkdir(parents=True)
        (run / "private_maps" / "AAA" / "identity_atlas.yaml").write_text(
            json.dumps(
                {
                    "metadata": {"registry_id": "reg-AAA", "company_id": "AAA"},
                    "entities": [],
                    "aliases": [],
                }
            ),
            encoding="utf-8",
        )
        (run / "config").mkdir(parents=True)
        (run / "manifests").mkdir(parents=True)
        (run / "qa").mkdir(parents=True)
        (run / "anonymized" / "AAA").mkdir(parents=True)
        (run / "originals" / "AAA" / "sec" / "filings").mkdir(parents=True)
        (run / "originals" / "AAA" / "news").mkdir(parents=True)
        (run / "run_summary.json").write_text(
            json.dumps({"ticker": "ZZZ", "tickers": ["ZZZ"]}), encoding="utf-8"
        )

        builder = DirectIdentifierAtlasBuilder(ticker="ZZZ", source_run=run)
        report = builder.harvest()
        # ticker bucket should be missing → critical warning for missing required type.
        critical = [w for w in report.coverage_warnings if w.get("level") == "critical"]
        assert len(critical) >= 1

    def test_six_aliases_is_critical_for_nvda_like(self, tmp_path: Path) -> None:
        # 6 aliases (matches the previous bounded-beta real NVDA outcome).
        small_entities = [
            {
                "entity_id": f"e{i}",
                "entity_type": "company",
                "canonical_private_value": f"Val{i:02d}",
            }
            for i in range(3)
        ]
        small_aliases = [
            {
                "alias_id": f"a{i}",
                "canonical_entity_id": f"e{i % 3}",
                "private_alias_value": f"AliasVal{i:02d}",
                "entity_type": "company",
                "match_policy": "literal",
            }
            for i in range(3)
        ]
        run = _build_source_run(
            tmp_path,
            ticker="AAA",
            atlas_entities=small_entities,
            atlas_aliases=small_aliases,
        )
        builder = DirectIdentifierAtlasBuilder(ticker="AAA", source_run=run)
        report = builder.harvest()
        # 3 entities + 3 aliases = 6 → at or below critical threshold.
        assert report.aliases_built <= CRITICAL_ALIAS_THRESHOLD
        critical = [w for w in report.coverage_warnings if w.get("level") == "critical"]
        assert any(w.get("code") == "below_critical_threshold" for w in critical)


class TestAtlasHarvestReportShape:
    def test_to_report_required_keys(self) -> None:
        r = AtlasHarvestReport(ticker="AAA")
        rep = r.to_report()
        assert rep["ticker"] == "AAA"
        for key in (
            "atlas_sources",
            "identifier_types",
            "aliases_built",
            "aliases_by_type",
            "coverage_warnings",
            "critical_warnings_count",
        ):
            assert key in rep


class TestOrchestratorMergeHarvestIntoYaml:
    """Verifies ``ReanonymizeOrchestrator._merge_harvest_into_atlas_yaml``.

    Code-reviewer #3 contract: harvested entities + aliases MUST
    merge into identity_atlas.yaml WITHOUT overwriting the existing
    human-curated entries, so the subsequent ``load_atlas`` call sees
    the merged atlas on the SAME run (not just on a follow-up).
    """

    def test_merge_preserves_curated_and_adds_harvested(self, tmp_path: Path) -> None:
        # Build a source-run with one human-curated entity+alias AND
        # an accession-bearing filing header so the harvester picks
        # up a deterministic accession value.
        run = _build_source_run(
            tmp_path,
            ticker="AAA",
            filings=[
                (
                    "AAA-2024-10K.htm",
                    "<html><body>Accession 0001045810-24-000029 for AAA.</body></html>",
                ),
            ],
            atlas_entities=[
                {
                    "entity_id": "ent_curated",
                    "entity_type": "company",
                    "canonical_private_value": "BigCo Holdings",
                },
            ],
            atlas_aliases=[
                {
                    "alias_id": "ali_curated",
                    "canonical_entity_id": "ent_curated",
                    "private_alias_value": "BigCo",
                    "entity_type": "company",
                    "match_policy": "literal",
                },
            ],
        )
        from fenrix_synthetic.reanonymize.atlas_builder import DirectIdentifierAtlasBuilder
        from fenrix_synthetic.reanonymize.orchestrator import (
            ReanonymizeOrchestrator,
            RunContext,
        )

        builder = DirectIdentifierAtlasBuilder(ticker="AAA", source_run=run)
        report = builder.harvest()
        # Sanity: at least ticker + accession + curated entity in the harvest.
        assert report.aliases_built >= 2

        o = ReanonymizeOrchestrator(
            source_run=run,
            output_root=tmp_path / "out",
            limit_forms=None,
            limit_news=0,
        )
        o._harvest_report = report
        ctx = RunContext(
            source_run=run,
            output_root=tmp_path / "out",
            ticker="AAA",
            form_limits={},
            news_limit=0,
            discovered_forms={},
            discovered_news_count=0,
        )
        o._merge_harvest_into_atlas_yaml(report, ctx)

        # Re-read atlas YAML to verify merge semantics.
        import yaml

        atlas_path = run / "private_maps" / "AAA" / "identity_atlas.yaml"
        atlas = yaml.safe_load(atlas_path.read_text()) or {}
        entity_ids = {e.get("entity_id") for e in atlas.get("entities", [])}
        alias_ids = {a.get("alias_id") for a in atlas.get("aliases", [])}

        # Curated entry MUST be preserved unchanged.
        assert "ent_curated" in entity_ids
        # New harvested entries MUST be present.
        harvested_entities = {eid for eid in entity_ids if eid.startswith("harvest_")}
        assert len(harvested_entities) >= 1
        # Original alias preserved.
        assert "ali_curated" in alias_ids
        # New harvested aliases present.
        harvested_aliases = {aid for aid in alias_ids if aid.startswith("harvest_")}
        assert len(harvested_aliases) >= 1
        # YAML still parses cleanly.
        assert isinstance(atlas, dict)

    def test_merge_is_idempotent_on_re_run(self, tmp_path: Path) -> None:
        # Build + merge, then merge again with the SAME report. The
        # second merge must not duplicate the harvest_* aliases (the
        # existing_alias_ids check should skip).
        run = _build_source_run(
            tmp_path,
            ticker="AAA",
            filings=[
                (
                    "AAA-2024-10K.htm",
                    "<html><body>0001045810-24-000029.</body></html>",
                ),
            ],
        )
        from fenrix_synthetic.reanonymize.atlas_builder import DirectIdentifierAtlasBuilder
        from fenrix_synthetic.reanonymize.orchestrator import (
            ReanonymizeOrchestrator,
            RunContext,
        )

        builder = DirectIdentifierAtlasBuilder(ticker="AAA", source_run=run)
        report_first = builder.harvest()
        o = ReanonymizeOrchestrator(
            source_run=run,
            output_root=tmp_path / "out",
            limit_forms=None,
            limit_news=0,
        )
        o._harvest_report = report_first
        ctx = RunContext(
            source_run=run,
            output_root=tmp_path / "out",
            ticker="AAA",
            form_limits={},
            news_limit=0,
            discovered_forms={},
            discovered_news_count=0,
        )
        o._merge_harvest_into_atlas_yaml(report_first, ctx)

        # Re-run merge with a fresh harvest report; should NOT double-count.
        builder2 = DirectIdentifierAtlasBuilder(ticker="AAA", source_run=run)
        report_second = builder2.harvest()
        o._merge_harvest_into_atlas_yaml(report_second, ctx)

        import yaml

        atlas = (
            yaml.safe_load((run / "private_maps" / "AAA" / "identity_atlas.yaml").read_text()) or {}
        )
        # Each ``harvest_*`` entity_id appears at most once.
        eids = [e.get("entity_id") for e in atlas.get("entities", [])]
        assert len(eids) == len(set(eids)), f"duplicate entity_ids after re-merge: {eids}"
        aids = [a.get("alias_id") for a in atlas.get("aliases", [])]
        assert len(aids) == len(set(aids)), f"duplicate alias_ids after re-merge: {aids}"
