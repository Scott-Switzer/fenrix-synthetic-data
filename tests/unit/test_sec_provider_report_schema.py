"""Tests for SEC provider report schema."""

from __future__ import annotations

import json

from fenrix_synthetic.professor.sec_providers import (
    FixtureSecProvider,
    OfficialSecApiProvider,
)


class TestSecProviderReportSchema:
    """Validate the qa/sec_provider_report.json schema."""

    REQUIRED_FIELDS = (
        "provider_name",
        "provider_kind",
        "request_count",
        "cache_hits",
        "cache_misses",
        "rate_limit_setting",
        "user_agent_configured",
        "filings_discovered",
        "filings_selected",
        "parse_success_count",
        "parse_failure_count",
        "semantic_validation_result",
        "private_cache_location",
        "public_provenance_keys",
    )

    def test_fixture_provider_report_has_required_fields(self) -> None:
        """FixtureSecProvider report must have all required fields."""
        provider = FixtureSecProvider()
        report = provider.get_provider_report()
        for field in self.REQUIRED_FIELDS:
            assert field in report, f"Missing required field: {field}"

    def test_official_provider_report_has_required_fields(self) -> None:
        """OfficialSecApiProvider report must have all required fields."""
        # Need to bypass live network check - use minimal valid config
        # with live_network=False to create the provider without live calls
        provider = OfficialSecApiProvider(
            user_agent="TestAgent/1.0 test@test.com",
            live_network=False,
        )
        report = provider.get_provider_report()
        for field in self.REQUIRED_FIELDS:
            assert field in report, f"Missing required field: {field}"

    def test_provider_name_exists(self) -> None:
        """Provider name must exist in report."""
        fixture_report = FixtureSecProvider().get_provider_report()
        assert fixture_report["provider_name"] == "FixtureSecProvider"

        official_report = OfficialSecApiProvider(
            user_agent="TestAgent/1.0 test@test.com",
            live_network=False,
        ).get_provider_report()
        assert official_report["provider_name"] == "OfficialSecApiProvider"

    def test_provider_kind_exists(self) -> None:
        """Provider kind must exist in report."""
        fixture_report = FixtureSecProvider().get_provider_report()
        assert fixture_report["provider_kind"] == "fixture"

        official_report = OfficialSecApiProvider(
            user_agent="TestAgent/1.0 test@test.com",
            live_network=False,
        ).get_provider_report()
        assert official_report["provider_kind"] == "real"

    def test_request_count_exists(self) -> None:
        """Request count must exist."""
        report = FixtureSecProvider().get_provider_report()
        assert "request_count" in report
        assert isinstance(report["request_count"], int)

    def test_cache_hit_miss_counts_exist(self) -> None:
        """Cache hit/miss counts must exist."""
        report = FixtureSecProvider().get_provider_report()
        assert "cache_hits" in report
        assert "cache_misses" in report
        assert isinstance(report["cache_hits"], int)
        assert isinstance(report["cache_misses"], int)

    def test_rate_limit_setting_exists(self) -> None:
        """Rate limit setting must exist."""
        report = FixtureSecProvider().get_provider_report()
        assert "rate_limit_setting" in report

    def test_user_agent_configured_boolean_exists(self) -> None:
        """User-agent configured boolean must exist."""
        report = FixtureSecProvider().get_provider_report()
        assert "user_agent_configured" in report
        assert isinstance(report["user_agent_configured"], bool)

    def test_filings_discovered_by_form_exists(self) -> None:
        """Filings discovered by form must exist."""
        report = FixtureSecProvider().get_provider_report()
        assert "filings_discovered" in report
        assert isinstance(report["filings_discovered"], dict)

    def test_filings_selected_by_form_exists(self) -> None:
        """Filings selected by form must exist."""
        report = FixtureSecProvider().get_provider_report()
        assert "filings_selected" in report
        assert isinstance(report["filings_selected"], dict)

    def test_parse_success_failure_count_exist(self) -> None:
        """Parse success/failure counts must exist."""
        report = FixtureSecProvider().get_provider_report()
        assert "parse_success_count" in report
        assert "parse_failure_count" in report
        assert isinstance(report["parse_success_count"], int)
        assert isinstance(report["parse_failure_count"], int)

    def test_semantic_validation_result_exists(self) -> None:
        """Semantic validation result must exist."""
        report = FixtureSecProvider().get_provider_report()
        assert "semantic_validation_result" in report

    def test_private_cache_location_not_serialized_to_public_bundle(self) -> None:
        """Private cache location must not leak into public bundle.

        The private_cache_location field exists in the report, but the
        report itself is under qa/ which is part of the output tree.
        The cache location should be empty or a relative path, not an
        absolute system path that reveals user structure.
        """
        report = FixtureSecProvider().get_provider_report()
        cache_loc = report["private_cache_location"]
        # Should not leak absolute home paths
        if cache_loc:
            assert "/Users/" not in cache_loc, f"Cache location leaks user path: {cache_loc}"

    def test_public_provenance_keys_exist(self) -> None:
        """Public provenance keys must exist."""
        report = FixtureSecProvider().get_provider_report()
        assert "public_provenance_keys" in report
        assert isinstance(report["public_provenance_keys"], list)

    def test_report_serializes_to_json(self) -> None:
        """Report must be JSON-serializable."""
        report = FixtureSecProvider().get_provider_report()
        serialized = json.dumps(report)
        assert isinstance(serialized, str)
        deserialized = json.loads(serialized)
        assert deserialized["provider_name"] == "FixtureSecProvider"
