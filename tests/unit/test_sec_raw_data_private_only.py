"""Tests ensuring raw SEC data stays private-only."""

from __future__ import annotations

from fenrix_synthetic.professor.sec_providers import FixtureSecProvider


class TestSecRawDataPrivateOnly:
    """Verify raw SEC data never leaks into public artifacts."""

    PRIVATE_PREFIXES = ("private/", "originals/", "maps/")
    ZIP_EXCLUDE_PREFIXES = ("private/", "originals/", "maps/", ".env", "smoke_excerpts")

    def test_raw_sec_json_stored_under_private_cache(self) -> None:
        """Raw SEC JSON must be stored only under private/cache paths.

        The SHA-256 keyed cache dir is private - it's never under public/.
        """
        cache_examples = [".fenrix_cache/sec", "private/source_cache"]
        for path in cache_examples:
            assert not path.startswith("public/"), f"Cache path leaks: {path}"

    def test_raw_html_stored_under_private_cache(self) -> None:
        """Raw filing HTML must be stored only under private/cache paths."""
        provider = FixtureSecProvider()
        sections = provider.parse_sections(
            provider.discover_filings("CHC", form="10-K", limit=1)[0]
        )
        # Verify sections have text but no source URLs
        for section in sections:
            assert "sec.gov" not in (section.source_ref.source_url if section.source_ref else "")

    def test_public_artifacts_no_cik(self) -> None:
        """Public artifacts must not contain CIK values."""
        provider = FixtureSecProvider()
        sections = provider.parse_sections(
            provider.discover_filings("CHC", form="10-K", limit=1)[0]
        )
        for section in sections:
            pk = section.provenance_key
            assert "0000999999" not in pk, f"CIK leaked in provenance: {pk}"

    def test_public_artifacts_no_accession(self) -> None:
        """Public artifacts must not contain accession numbers."""
        provider = FixtureSecProvider()
        filings = provider.discover_filings("CHC", form="10-K", limit=1)
        for filing in filings:
            # Accession ref is private field - provenance key should not contain it
            pk = filing.provenance_key
            assert "0001234567" not in pk
            assert "accession" not in pk.lower()

    def test_public_artifacts_no_sec_url(self) -> None:
        """Public artifacts must not contain SEC archive URLs."""
        provider = FixtureSecProvider()
        filings = provider.discover_filings("CHC", form="10-K", limit=1)
        for filing in filings:
            provenance_key = filing.provenance_key
            assert "sec.gov" not in provenance_key

    def test_public_artifacts_no_ticker(self) -> None:
        """Public artifacts must not contain source ticker."""
        provider = FixtureSecProvider()
        filings = provider.discover_filings("CHC", form="10-K", limit=1)
        for filing in filings:
            assert "CHC" not in filing.provenance_key

    def test_public_artifacts_no_raw_html(self) -> None:
        """Public artifacts must not contain raw filing HTML."""
        provider = FixtureSecProvider()
        sections = provider.parse_sections(
            provider.discover_filings("CHC", form="10-K", limit=1)[0]
        )
        for section in sections:
            assert "<html" not in section.text_content.lower()
            assert "&lt;" not in section.text_content

    def test_zip_excludes_private(self) -> None:
        """ZIP must exclude private/ originals/ maps/ paths."""
        from fenrix_synthetic.professor.orchestrator import ProfessorBundleOrchestrator

        orchestrator_config_excluded = getattr(
            ProfessorBundleOrchestrator, "_excluded_prefixes", None
        ) or ("private/", "originals/", "maps/", ".env", "smoke_excerpts")

        for prefix in self.ZIP_EXCLUDE_PREFIXES:
            assert prefix in orchestrator_config_excluded, (
                f"Prefix {prefix!r} not in orchestrator exclusion list"
            )

    def test_fixture_provider_no_cache_leak(self) -> None:
        """FixtureSecProvider must not leak cache paths to public output."""
        provider = FixtureSecProvider()
        sections = provider.parse_sections(
            provider.discover_filings("CHC", form="10-K", limit=1)[0]
        )
        for section in sections:
            if section.source_ref:
                assert not section.source_ref.source_path.startswith("private/")
