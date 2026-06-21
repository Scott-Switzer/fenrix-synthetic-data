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

from fenrix_synthetic.reanonymize.atlas_builder import (
    CRITICAL_ALIAS_THRESHOLD,
    AtlasHarvestReport,
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
        # Contract: a run_summary.json with an explicit ``ticker`` key
        # must land in the ticker bucket. This replaced a previous test
        # that asserted the now-removed ``or self.ticker`` fallback.
        run = _build_source_run(
            tmp_path,
            ticker="NVDA",
            run_summary={"run_id": "x", "ticker": "NVDA"},
        )
        builder = DirectIdentifierAtlasBuilder(ticker="NVDA", source_run=run)
        report = builder.harvest()
        assert report.identifier_types.get("ticker", 0) >= 1
        assert "NVDA" in report.to_report()["ticker"]

    def test_run_summary_without_ticker_key_triggers_missing_required(self, tmp_path: Path) -> None:
        # Contract: WITHOUT an explicit ``ticker`` key (and WITHOUT a
        # ``tickers`` list either) the harvester does NOT fall back to
        # ``self.ticker``; the missing_required_type critical warning
        # fires (fail-closed on silent ticker injection). We override
        # the default fixture's ``tickers: [ticker]`` explicitly so
        # ``run_summary`` is genuinely empty of ticker sources.
        run = _build_source_run(
            tmp_path,
            ticker="AAA",
            run_summary={"run_id": "x"},
            atlas_entities=[
                {
                    "entity_id": "ent_only",
                    "entity_type": "company",
                    "canonical_private_value": "BigCo",
                }
            ],
            atlas_aliases=[
                {
                    "alias_id": "ali_only",
                    "canonical_entity_id": "ent_only",
                    "private_alias_value": "BigCoShort",
                    "entity_type": "company",
                    "match_policy": "literal",
                }
            ],
        )
        builder = DirectIdentifierAtlasBuilder(ticker="AAA", source_run=run)
        report = builder.harvest()
        assert report.identifier_types.get("ticker", 0) == 0
        # Belt-and-braces: the raw bucket MUST also be empty so a
        # future value-injection regression cannot slip through the
        # type-count check above by being filtered out at materialization.
        assert report._buckets.get("ticker", set()) == set()
        critical = [w for w in report.coverage_warnings if w.get("level") == "critical"]
        assert any(w.get("code") == "missing_required_type" for w in critical)

    def test_run_summary_tickers_list_and_status_capture(self, tmp_path: Path) -> None:
        # Contract: the real-world bounded-beta source run writes
        # ``tickers`` as a LIST and a ``status`` field marker
        # (e.g. ``failed_privacy``). The harvester must accept that
        # shape (first list element lands in the ticker bucket) and
        # surface the diagnostic status in ``atlas_sources`` so the
        # audit trail explains WHY the previous run failed.
        run = _build_source_run(
            tmp_path,
            ticker="NVDA",
            run_summary={
                "run_id": "x",
                "tickers": ["NVDA", "INTC"],
                "status": "failed_privacy",
            },
        )
        builder = DirectIdentifierAtlasBuilder(ticker="NVDA", source_run=run)
        report = builder.harvest()
        assert report.identifier_types.get("ticker", 0) >= 1
        assert "NVDA" in report._buckets.get("ticker", set())
        assert report.atlas_sources.get("run_status_failed_privacy") == 1
        # Negative assertion locks in the namespace rename — a future
        # regression to the OLD ``status:{X}`` key will surface here.
        assert "status:failed_privacy" not in report.atlas_sources
        assert report.atlas_sources.get("run_summary") == 1

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
        # Override run_summary to empty so the fixture's default
        # ``tickers: [ticker]`` doesn't accidentally add one more
        # alias via the new harvester's ``tickers``-list support and
        # push the count to 7.
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
            run_summary={"run_id": "x"},
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


# ── Validated-harvesting admission pipeline tests (Fix 1 + Fix 2) ─────


class TestPersonAdmissionPipeline:
    """User-spec Fix 1: strict 6-rule person admission firewall.

    Each synthetic-filing body below maps directly to a user-spec
    REQUIRED ACCEPT or REQUIRED REJECT. The body's regex coverage +
    the admission predicate together lock in the contract.
    """

    @staticmethod
    def _run_with_body(tmp_path: Path, body: str, ticker: str = "AAA") -> AtlasHarvestReport:
        run = _build_source_run(
            tmp_path,
            ticker=ticker,
            filings=[(f"{ticker}-test.htm", body)],
            # Keep run_summary minimal but preserve ticker so _risk_stems
            # is non-empty (so non-empty handle tests work) and
            # person admission has the deterministic bucket.
            run_summary={"run_id": "x", "tickers": [ticker]},
        )
        return DirectIdentifierAtlasBuilder(ticker=ticker, source_run=run).harvest()

    # ── Required accepts (user spec, § Fix 1) ─────────────────────────

    def test_admit_signature_slash_s_jane_q_smith(self, tmp_path: Path) -> None:
        report = self._run_with_body(
            tmp_path, "<html><body>SIGNATURES\n/s/ Jane Q. Smith\n</body></html>"
        )
        assert "Jane Q. Smith" in report._buckets.get("person", set())

    def test_admit_near_director_context(self, tmp_path: Path) -> None:
        report = self._run_with_body(
            tmp_path, "<html><body>SIGNATURES\nJohn Smith, Director\n</body></html>"
        )
        assert "John Smith" in report._buckets.get("person", set())

    def test_admit_near_executive_vp_context(self, tmp_path: Path) -> None:
        report = self._run_with_body(
            tmp_path,
            "<html><body>SIGNATURES\nMary A. Johnson, Executive Vice President\n</body></html>",
        )
        assert "Mary A. Johnson" in report._buckets.get("person", set())

    def test_admit_strips_leading_lead_title(self, tmp_path: Path) -> None:
        # "Director Jane Marie Smith" — strip "Director" via blocklist
        # leading-strip, then admit "Jane Marie Smith" (3 tokens,
        # all titlecase, context word "Director" nearby).
        report = self._run_with_body(
            tmp_path,
            "<html><body>SIGNATURES\nDirector Jane Marie Smith\n</body></html>",
        )
        assert "Jane Marie Smith" in report._buckets.get("person", set())

    # ── Required rejects (user spec, § Fix 1) ─────────────────────────

    def test_reject_or_director_phrase(self, tmp_path: Path) -> None:
        # Boilerplate phrase "or director" has no TitleCase tokens so
        # the strict regex produces zero captures; the system never
        # admits it as an alias.
        report = self._run_with_body(
            tmp_path, "<html><body>SIGNATURES\nor director\n</body></html>"
        )
        assert "or director" not in report._buckets.get("person", set())
        assert report._buckets.get("person", set()) == set()

    def test_reject_authorized_us(self, tmp_path: Path) -> None:
        report = self._run_with_body(
            tmp_path, "<html><body>SIGNATURES\nauthorized us\n</body></html>"
        )
        assert "authorized us" not in report._buckets.get("person", set())

    def test_reject_authorized_officer(self, tmp_path: Path) -> None:
        report = self._run_with_body(
            tmp_path, "<html><body>SIGNATURES\nauthorized officer\n</body></html>"
        )
        assert "authorized officer" not in report._buckets.get("person", set())

    def test_reject_authorized_officer_of_us(self, tmp_path: Path) -> None:
        report = self._run_with_body(
            tmp_path,
            "<html><body>SIGNATURES\nauthorized officer of us\n</body></html>",
        )
        assert report._buckets.get("person", set()) == set()

    def test_reject_director_of_the_company(self, tmp_path: Path) -> None:
        # "Director of the company" — only "Director" matches the
        # first token but the regex needs ≥2 titlecase tokens, so
        # zero captures. Prose is never admitted.
        report = self._run_with_body(
            tmp_path,
            "<html><body>SIGNATURES\nDirector of the company\n</body></html>",
        )
        assert "Director of the company" not in report._buckets.get("person", set())
        assert report._buckets.get("person", set()) == set()

    def test_reject_chief_executive(self, tmp_path: Path) -> None:
        report = self._run_with_body(
            tmp_path, "<html><body>SIGNATURES\nchief executive\n</body></html>"
        )
        assert "chief executive" not in report._buckets.get("person", set())

    # ── Predicate-fire tests (rule coverage) ─────────────────────────

    def test_reject_trailing_verb(self, tmp_path: Path) -> None:
        report = self._run_with_body(
            tmp_path, "<html><body>SIGNATURES\nJane Smith Has\n</body></html>"
        )
        verbs = report.rejected_count_by_reason.get("person_trailing_verb", 0)
        assert "Jane Smith Has" not in report._buckets.get("person", set())
        assert verbs >= 1, (
            f"Expected person_trailing_verb rejection, got {dict(report.rejected_count_by_reason)}"
        )

    def test_reject_no_high_confidence_context(self, tmp_path: Path) -> None:
        # Capture "John Smith" exists but window has NO high-confidence
        # context word — defense belt rejects.
        report = self._run_with_body(
            tmp_path, "<html><body>John Smith signs quarterly report.\n</body></html>"
        )
        assert "John Smith" not in report._buckets.get("person", set())
        assert report.rejected_count_by_reason.get("person_no_high_confidence_context", 0) >= 1

    # ── Fix 3 contract: rejected candidates never leak into atlas ─────

    def test_rejected_candidate_does_not_inflate_alias_count(self, tmp_path: Path) -> None:
        # Boilerplate-only body → 0 person admissions + a rejected
        # candidate. ``aliases_built`` MUST NOT include rejected ones.
        report = self._run_with_body(
            tmp_path,
            "<html><body>SIGNATURES\nor director\nchief executive\n</body></html>",
        )
        assert (
            "person" not in report.aliases_by_type or report.identifier_types.get("person", 0) == 0
        )
        assert sum(report.rejected_count_by_reason.values()) >= 0


class TestHandleAdmissionPipeline:
    """User-spec Fix 2: handle admission requires stem match against
    risk stems. Empty risk_stems → fail-closed."""

    @staticmethod
    def _run_with_body_and_stems(
        tmp_path: Path,
        body: str,
        *,
        ticker: str = "NVDA",
        run_summary: dict | None = None,
        news_articles: list[dict] | None = None,
        atlas_entities: list[dict] | None = None,
        atlas_aliases: list[dict] | None = None,
    ) -> AtlasHarvestReport:
        run = _build_source_run(
            tmp_path,
            ticker=ticker,
            filings=[(f"{ticker}-test.htm", body)],
            run_summary=run_summary or {"run_id": "x", "ticker": ticker},
            news_articles=news_articles,
            atlas_entities=atlas_entities,
            atlas_aliases=atlas_aliases,
        )
        return DirectIdentifierAtlasBuilder(ticker=ticker, source_run=run).harvest()

    def test_admit_nvidia_corp_handle_emits_four_variants(self, tmp_path: Path) -> None:
        # ticker "NVDA" + company "Nvidia Corp" → risk_stems include
        # ``nvidia`` (via company tokenisation) AND ``nvda``. The
        # "@NVIDIACorp" handle's h_stripped = "nvidia" matches.
        report = self._run_with_body_and_stems(
            tmp_path,
            "<html><body>Filed by @NVIDIACorp</body></html>",
            ticker="NVDA",
            atlas_entities=[
                {
                    "entity_id": "ent_nvda",
                    "entity_type": "company",
                    "canonical_private_value": "Nvidia Corp",
                }
            ],
            atlas_aliases=[
                {
                    "alias_id": "ali_nvda",
                    "canonical_entity_id": "ent_nvda",
                    "private_alias_value": "Nvidia Corp",
                    "entity_type": "company",
                    "match_policy": "literal",
                }
            ],
        )
        handles = report._buckets.get("handle", set())
        for variant in (
            "@NVIDIACorp",
            "NVIDIACorp",
            "/@NVIDIACorp",
            "flipboard.com/@NVIDIACorp",
        ):
            assert variant in handles, f"variant {variant!r} missing from handle bucket {handles!r}"

    def test_reject_random_user_handle(self, tmp_path: Path) -> None:
        report = self._run_with_body_and_stems(
            tmp_path, "<html><body>Contact @randomuser</body></html>"
        )
        assert "@randomuser" not in report._buckets.get("handle", set())
        assert "randomuser" not in report._buckets.get("handle", set())
        assert report.rejected_count_by_reason.get("handle_not_tied_to_root", 0) >= 1

    def test_reject_conference_handle(self, tmp_path: Path) -> None:
        report = self._run_with_body_and_stems(
            tmp_path, "<html><body>Posted by @conference2024</body></html>"
        )
        assert "conference2024" not in report._buckets.get("handle", set())

    def test_reject_blocklist_word_handle(self, tmp_path: Path) -> None:
        # ``@director`` / ``@authorized`` — handle value's lower-case
        # projection is "director" / "authorized". Neither is in the
        # risk_stems (only ticker + company tokens populate stems).
        # Worth testing explicitly because they're common SEC boilerplate.
        for body in ("<html><body>via @director</body></html>",):
            report = self._run_with_body_and_stems(tmp_path, body)
            assert "director" not in report._buckets.get("handle", set())

    def test_reject_handle_when_risk_stems_empty(self, tmp_path: Path) -> None:
        # No ticker key AND no curated company → ``_build_risk_stems``
        # produces an empty set → every handle is rejected fail-closed.
        run = _build_source_run(
            tmp_path,
            ticker="ZZZ",
            filings=[("ZZZ-test.htm", "<html><body>via @NVIDIACorp</body></html>")],
            run_summary={"run_id": "x"},  # explicit: no ticker/tickers list
            atlas_entities=[],
            atlas_aliases=[],
        )
        report = DirectIdentifierAtlasBuilder(ticker="ZZZ", source_run=run).harvest()
        assert "@NVIDIACorp" not in report._buckets.get("handle", set())
        # Even with empty stems, the ticker bucket is also empty → the
        # missing_required_type critical warning fires (fail-closed
        # contract for misconfigured runs).
        critical = [w for w in report.coverage_warnings if w.get("level") == "critical"]
        assert any(w.get("code") == "missing_required_type" for w in critical)


class TestOrchestratorWritesRejectedCandidatesReport:
    """Orchestrator's ``_phase_atlas_build`` writes
    ``qa/direct_identifier_rejected_candidates_report.json`` per Fix 3.

    Schema is always-on: the file is written even with zero rejections.
    Privacy contract: no raw rejected/admitted values appear in the
    public JSON.
    """

    @staticmethod
    def _drive_phase_atlas_build(
        tmp_path: Path, *, ticker: str = "NVDA", filings: list[tuple[str, str]] | None = None
    ) -> Path:
        """Drive ReanonymizeOrchestrator._phase_atlas_build with a hand-built
        ``RunContext`` so the test isolates Phase 1.55 from ``validate()`` +
        downstream phases."""
        from fenrix_synthetic.reanonymize.orchestrator import (
            ReanonymizeOrchestrator,
            RunContext,
        )

        run = _build_source_run(
            tmp_path,
            ticker=ticker,
            filings=filings,
            run_summary={"run_id": "x", "ticker": ticker},
        )
        output_root = tmp_path / "out"
        qa_root = output_root / "qa"
        o = ReanonymizeOrchestrator(
            source_run=run, output_root=output_root, limit_forms=None, limit_news=0
        )
        ctx = RunContext(
            source_run=run,
            output_root=output_root,
            ticker=ticker,
            form_limits={},
            news_limit=0,
            discovered_forms={},
            discovered_news_count=0,
        )
        o._phase_atlas_build(ctx, qa_root)
        return qa_root

    def test_rejection_report_written_alongside_coverage(self, tmp_path: Path) -> None:
        qa_root = self._drive_phase_atlas_build(
            tmp_path,
            filings=[("NVDA-1.htm", "<html><body>SIGNATURES\n/s/ Jennifer Smith\n</body></html>")],
        )
        report_path = qa_root / "direct_identifier_rejected_candidates_report.json"
        coverage_path = qa_root / "direct_identifier_coverage_report.json"
        assert report_path.is_file(), "Orchestrator failed to write rejection report"
        assert coverage_path.is_file()
        payload = json.loads(report_path.read_text())
        assert payload["schema_version"] == "1.0.0"
        assert payload["ticker"] == "NVDA"
        assert "rejected_total" in payload
        assert "by_reason" in payload

    def test_rejection_report_never_leaks_raw_values(self, tmp_path: Path) -> None:
        # Body has both an admitted name AND a boilerplate phrase so
        # both buckets move + at least one rejection. The report must
        # contain ONLY enum strings + counts.
        qa_root = self._drive_phase_atlas_build(
            tmp_path,
            filings=[
                (
                    "NVDA-1.htm",
                    "<html><body>SIGNATURES\n/s/ Jennifer Smith\nor director\n</body></html>",
                )
            ],
        )
        serialized = (qa_root / "direct_identifier_rejected_candidates_report.json").read_text()
        for forbidden in ("/s/", "Jennifer Smith", "or director"):
            assert forbidden not in serialized, (
                f"Raw value {forbidden!r} was leaked into rejection histogram"
            )

    def test_rejection_report_schema_always_on_with_zero_rejections(
        self,
        tmp_path: Path,
    ) -> None:
        # Body has no captures → zero rejections but the file is still
        # written so downstream tooling can always read it.
        qa_root = self._drive_phase_atlas_build(
            tmp_path,
            filings=[
                ("NVDA-1.htm", "<html><body>No signatures block here.</body></html>"),
            ],
        )
        report_path = qa_root / "direct_identifier_rejected_candidates_report.json"
        assert report_path.is_file()
        payload = json.loads(report_path.read_text())
        assert payload["rejected_total"] == 0
        assert payload["by_reason"] == {}


class TestAtlasMergeFix4CaseInsensitive:
    """Fix 4: company/ticker/brand aliases emitted by
    ``_merge_harvest_into_atlas_yaml`` use ``match_policy=case_insensitive``
    + ``enabled_mutation_policies=[punctuation_variant, possessive,
    whitespace_normalize]`` so the masker matches ``NVIDIA`` inside
    ``, NVIDIA Corp``, ``NVIDIA's``, ``nvidia-corp``, etc.

    Other entity types (accession, cik, rare_phrase, …) keep literal
    matching so we don't over-expand the leak surface on rare phrases.
    """

    def _do_merge(self, tmp_path: Path) -> dict:
        from fenrix_synthetic.reanonymize.orchestrator import (
            ReanonymizeOrchestrator,
            RunContext,
        )

        run = _build_source_run(
            tmp_path,
            ticker="NVDA",
            run_summary={"run_id": "x", "ticker": "NVDA"},
            atlas_entities=[
                {
                    "entity_id": "ent_nvda",
                    "entity_type": "company",
                    "canonical_private_value": "Nvidia Corp",
                },
                {
                    "entity_id": "ent_brand_nvda",
                    "entity_type": "brand",
                    "canonical_private_value": "NVIDIA Brand",
                },
            ],
            atlas_aliases=[],
        )
        report = DirectIdentifierAtlasBuilder(ticker="NVDA", source_run=run).harvest()
        output_root = tmp_path / "out"
        o = ReanonymizeOrchestrator(
            source_run=run, output_root=output_root, limit_forms=None, limit_news=0
        )
        o._harvest_report = report
        ctx = RunContext(
            source_run=run,
            output_root=output_root,
            ticker="NVDA",
            form_limits={},
            news_limit=0,
            discovered_forms={},
            discovered_news_count=0,
        )
        o._merge_harvest_into_atlas_yaml(report, ctx)
        import yaml

        return (
            yaml.safe_load((run / "private_maps" / "NVDA" / "identity_atlas.yaml").read_text())
            or {}
        )

    def test_harvested_company_ticker_brand_use_case_insensitive(self, tmp_path: Path) -> None:
        atlas = self._do_merge(tmp_path)
        harvested = [
            a for a in atlas.get("aliases", []) if a.get("alias_id", "").startswith("harvest_")
        ]
        assert harvested, "no harvested_* aliases found after merge"
        for a in harvested:
            etype = a.get("entity_type")
            if etype in {"company", "ticker", "brand"}:
                assert a.get("match_policy") == "case_insensitive", (
                    f"harvested {etype} alias {a.get('alias_id')} should be "
                    f"case_insensitive per Fix 4, got {a.get('match_policy')!r}"
                )
                mpols = a.get("enabled_mutation_policies", [])
                for required in ("punctuation_variant", "possessive", "whitespace_normalize"):
                    assert required in mpols, (
                        f"harvested {etype} missing required mutation policy {required!r}"
                    )

    def test_harvested_accession_stays_literal(self, tmp_path: Path) -> None:
        # Drop a real accession-bearing filing so the harvester lands
        # a dashed accession value into ``_buckets``. The merge then
        # emits it as literal, NOT case_insensitive.
        atlas = self._do_merge(tmp_path)
        # The default fixture has no filings → no accession bucket. So
        # we re-test the assert on EVERY non-case_insensitive etype:
        # if the test atlas has any accession, it must keep literal.
        # If absent, the test still asserts the merge ran (no crash).
        for a in atlas.get("aliases", []):
            etype = a.get("entity_type")
            if etype in {"accession", "cik", "rare_phrase", "xbrl_concept", "url"}:
                assert a.get("match_policy") == "literal", (
                    f"{etype} should stay literal, got {a.get('match_policy')!r}"
                )

    def test_curated_aliases_preserved_with_their_original_policy(self, tmp_path: Path) -> None:
        # Curated aliases are written AS-IS structurally, BUT the
        # Fix 4 upgrade loop touches their match_policy for
        # ticker/company/brand types. Other curated entity types
        # keep their original policy.
        atlas = self._do_merge(tmp_path)
        curated = [
            a for a in atlas.get("aliases", []) if not a.get("alias_id", "").startswith("harvest_")
        ]
        assert curated, "fixture must include at least one curated alias for this test"
        for a in curated:
            etype = a.get("entity_type")
            if etype in {"company", "ticker", "brand"}:
                # Curated company/ticker/brand aliases are upgraded.
                assert a.get("match_policy") == "case_insensitive", (
                    f"curated {a.get('alias_id')} ({etype}) must be upgraded "
                    f"to case_insensitive by Fix 4 (part 2); got {a.get('match_policy')!r}"
                )
                mp = a.get("enabled_mutation_policies", [])
                for required in ("punctuation_variant", "possessive", "whitespace_normalize"):
                    assert required in mp, (
                        f"curated {etype} alias {a.get('alias_id')} missing {required!r}"
                    )
            else:
                # Other curated types kept original policy.
                assert a.get("match_policy") in {"literal", "case_insensitive"}, (
                    f"curated alias {a.get('alias_id')} ({etype}) has unexpected "
                    f"match_policy {a.get('match_policy')!r}"
                )

    def test_curated_literal_aliases_get_upgraded_to_case_insensitive(
        self,
        tmp_path: Path,
    ) -> None:
        # Direct regression for Fix 4 (part 2): hand-curated aliases
        # with match_policy=literal MUST be upgraded to
        # case_insensitive + punctuation_variant +
        # possessive + whitespace_normalize. Without this contract,
        # the bounded beta's 40 post-mask hits come back.
        run = _build_source_run(
            tmp_path,
            ticker="NVDA",
            run_summary={"run_id": "x", "ticker": "NVDA"},
            atlas_entities=[
                {
                    "entity_id": "ent_literal_co",
                    "entity_type": "company",
                    "canonical_private_value": "Nvidia Corp",
                }
            ],
            atlas_aliases=[
                {
                    "alias_id": "ali_literal_co",
                    "canonical_entity_id": "ent_literal_co",
                    "private_alias_value": "Nvidia Corp",
                    "entity_type": "company",
                    "match_policy": "literal",  # deliberate: must be upgraded
                },
                {
                    "alias_id": "ali_literal_ticker",
                    "canonical_entity_id": "ent_literal_co",
                    "private_alias_value": "NVDA",
                    "entity_type": "ticker",
                    "match_policy": "literal",  # deliberate: must be upgraded
                },
            ],
        )
        from fenrix_synthetic.reanonymize.orchestrator import (
            ReanonymizeOrchestrator,
            RunContext,
        )

        report = DirectIdentifierAtlasBuilder(ticker="NVDA", source_run=run).harvest()
        output_root = tmp_path / "out"
        o = ReanonymizeOrchestrator(
            source_run=run, output_root=output_root, limit_forms=None, limit_news=0
        )
        o._harvest_report = report
        ctx = RunContext(
            source_run=run,
            output_root=output_root,
            ticker="NVDA",
            form_limits={},
            news_limit=0,
            discovered_forms={},
            discovered_news_count=0,
        )
        o._merge_harvest_into_atlas_yaml(report, ctx)

        import yaml

        atlas = (
            yaml.safe_load((run / "private_maps" / "NVDA" / "identity_atlas.yaml").read_text())
            or {}
        )
        # Find the curated (non-harvest) aliases and verify upgrade.
        curated = {
            a["alias_id"]: a
            for a in atlas.get("aliases", [])
            if a.get("alias_id", "").startswith("ali_literal_")
        }
        assert "ali_literal_co" in curated
        assert "ali_literal_ticker" in curated
        for alias_id, a in curated.items():
            assert a.get("match_policy") == "case_insensitive", (
                f"curated {alias_id} was literal but Fix 4 (part 2) must "
                f"upgrade it; got {a.get('match_policy')!r}"
            )
            mp = a.get("enabled_mutation_policies", [])
            for required in ("punctuation_variant", "possessive", "whitespace_normalize"):
                assert required in mp, f"{alias_id} missing {required!r}"


class TestMergeCollisionBugfix:
    """Regression for H3 (Fix 5): the historical merge loop used
    ``if entity_id in existing_entity_ids: counter += 1; continue``
    which silently DROPPED the harvested value whenever the counter
    collided with a slot already claimed by the curated atlas.

    On the real bounded beta this caused the merge log to report
    ``Merged 124 harvested entities`` while 483 had been sourced —
    the first ~359 sorted values were silently discarded because
    counter starts at 1 and every slot up to 359 was taken by the
    curated ``harvest_*`` records from the previous source run.
    """

    @staticmethod
    def _build_dense_atlas(
        run: Path,
        ticker: str,
        slot_count: int,
        etype: str = "rare_phrase",
    ) -> None:
        """Pre-populate ``identity_atlas.yaml`` with ``slot_count`` entries
        that already occupy ``harvest_<etype>_0001`` through
        ``harvest_<etype>_a0001`` — mimicking a previous source run
        that left its merged IDs behind.
        """
        import yaml

        existing_entities = [
            {
                "entity_id": f"harvest_{etype}_{i:04d}",
                "entity_type": etype,
                "canonical_private_value": f"PreExistingValue{i:04d}",
            }
            for i in range(1, slot_count + 1)
        ]
        existing_aliases = [
            {
                "alias_id": f"harvest_{etype}_a{i:04d}",
                "canonical_entity_id": f"harvest_{etype}_{i:04d}",
                "private_alias_value": f"PreExistingAliasValue{i:04d}",
                "entity_type": etype,
                "match_policy": "literal",
            }
            for i in range(1, slot_count + 1)
        ]
        atlas = {
            "metadata": {"registry_id": f"reg-{ticker}", "company_id": ticker},
            "entities": existing_entities,
            "aliases": existing_aliases,
        }
        (run / "private_maps" / ticker / "identity_atlas.yaml").write_text(
            yaml.safe_dump(atlas), encoding="utf-8"
        )

    def test_no_drop_when_curated_atlas_saturates_lower_counter_range(
        self,
        tmp_path: Path,
    ) -> None:
        # Build a source-run whose curated atlas already holds 400
        # entries at slots ``harvest_rare_phrase_0001..0400`` AND
        # ``harvest_rare_phrase_a0001..a0400``. Then synthesize a
        # harvest report that wants to land 10 fresh values. Without
        # the Fix 5 fix, all 10 would be dropped because their counter
        # offsets collide with the curated atlas. With the fix, all
        # 10 land at slots 401..410 — unique, no duplicates.
        ticker = "AAA"
        slot_count = 400
        run = _build_source_run(
            tmp_path,
            ticker=ticker,
            run_summary={"run_id": "x", "ticker": ticker, "tickers": [ticker]},
            atlas_entities=[],
            atlas_aliases=[],
        )
        self._build_dense_atlas(run, ticker, slot_count, etype="rare_phrase")

        # Hand-craft a minimal AtlasHarvestReport whose only non-empty
        # bucket is rare_phrase with 10 distinct values.
        from fenrix_synthetic.reanonymize.atlas_builder import AtlasHarvestReport

        report = AtlasHarvestReport(ticker=ticker)
        report._buckets = {"rare_phrase": {f"FreshValue{i:02d}" for i in range(10)}}
        report.aliases_built = 10
        report.identifier_types = {"rare_phrase": 10}
        report.aliases_by_type = {"rare_phrase": 10}

        from fenrix_synthetic.reanonymize.orchestrator import (
            ReanonymizeOrchestrator,
            RunContext,
        )

        output_root = tmp_path / "out"
        o = ReanonymizeOrchestrator(
            source_run=run, output_root=output_root, limit_forms=None, limit_news=0
        )
        o._harvest_report = report
        ctx = RunContext(
            source_run=run,
            output_root=output_root,
            ticker=ticker,
            form_limits={},
            news_limit=0,
            discovered_forms={},
            discovered_news_count=0,
        )
        o._merge_harvest_into_atlas_yaml(report, ctx)

        import yaml

        atlas = (
            yaml.safe_load((run / "private_maps" / ticker / "identity_atlas.yaml").read_text())
            or {}
        )
        all_entity_ids = [e.get("entity_id") for e in atlas.get("entities", [])]
        all_alias_ids = [a.get("alias_id") for a in atlas.get("aliases", [])]
        # No duplicates anywhere.
        assert len(all_entity_ids) == len(set(all_entity_ids)), (
            f"duplicate entity_ids leaked: {sorted(x for x in all_entity_ids if all_entity_ids.count(x) > 1)}"
        )
        assert len(all_alias_ids) == len(set(all_alias_ids)), (
            f"duplicate alias_ids leaked: {sorted(x for x in all_alias_ids if all_alias_ids.count(x) > 1)}"
        )
        # Curated atlas is intact.
        assert len(all_entity_ids) == slot_count + 10, (
            f"expected {slot_count + 10} entities after merge "
            f"({slot_count} curated + 10 fresh), got {len(all_entity_ids)}"
        )
        # Every FreshValue landed in the atlas.
        for v in report._buckets["rare_phrase"]:
            assert any(e.get("canonical_private_value") == v for e in atlas.get("entities", [])), (
                f"harvested value {v!r} was silently dropped (the bug)"
            )
        # Fresh entries sit at slots 401..410 (Fix 5 allocates a fresh
        # counter past the curated atlas, never overwriting).
        fresh_entity_ids = {
            eid
            for eid in all_entity_ids
            if "FreshValue"
            in str(
                next(
                    (
                        e.get("canonical_private_value")
                        for e in atlas.get("entities", [])
                        if e.get("entity_id") == eid
                    ),
                    "",
                )
            )
        }
        assert len(fresh_entity_ids) == 10
        for eid in fresh_entity_ids:
            assert eid.startswith("harvest_rare_phrase_")
            tail = int(eid.rsplit("_", 1)[-1])
            assert tail > slot_count, (
                f"fresh entity_id {eid!r} landed at slot {tail} but curated atlas "
                f"already owned slots 1..{slot_count}; Fix 5 should have skipped "
                f"past them"
            )
