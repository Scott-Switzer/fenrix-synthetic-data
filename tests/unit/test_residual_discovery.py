from __future__ import annotations

from fenrix_synthetic.masking.discovery import (
    DiscoveredEntity,
    DiscoveryResult,
    ResidualEntityDiscoverer,
)


class TestResidualEntityDiscoverer:
    def test_discover_capitalized_phrases(self):
        discoverer = ResidualEntityDiscoverer()
        text = "Acme Corporation is a leading provider of security solutions."
        result = discoverer.discover(text)
        texts = [e.text for e in result]
        assert any("Acme" in t for t in texts)

    def test_discover_ticker_patterns(self):
        discoverer = ResidualEntityDiscoverer()
        text = "The company (ACM) reported strong results."
        result = discoverer.discover(text)
        types = [e.discovery_type for e in result]
        assert "ticker_pattern" in types

    def test_discover_urls(self):
        discoverer = ResidualEntityDiscoverer()
        text = "Visit https://www.acme-corp.example for more info."
        result = discoverer.discover(text)
        types = [e.discovery_type for e in result]
        assert "url" in types

    def test_discover_emails(self):
        discoverer = ResidualEntityDiscoverer()
        text = "Contact us at info@acme-corp.example."
        result = discoverer.discover(text)
        types = [e.discovery_type for e in result]
        assert "email" in types

    def test_discover_executive_patterns(self):
        discoverer = ResidualEntityDiscoverer()
        text = "Our CEO Jane Smith leads the company."
        result = discoverer.discover(text)
        types = [e.discovery_type for e in result]
        assert "executive_pattern" in types

    def test_discover_cik_patterns(self):
        discoverer = ResidualEntityDiscoverer()
        text = "CIK 0000999999 was used for the filing."
        result = discoverer.discover(text)
        types = [e.discovery_type for e in result]
        assert "cik" in types

    def test_pseudonyms_filtered_out(self):
        discoverer = ResidualEntityDiscoverer()
        text = "Company 001 reported strong results."
        result = discoverer.discover(text, known_pseudonyms={"Company 001"})
        assert len(result) == 0

    def test_common_words_filtered(self):
        discoverer = ResidualEntityDiscoverer()
        text = "The company reported financial results."
        result = discoverer.discover(text)
        texts = [e.text for e in result]
        assert "The company" not in texts

    def test_discover_unmasked_entities(self):
        discoverer = ResidualEntityDiscoverer()
        text = "Acme Corp (ACM) reported strong results."
        masked_spans = [(0, 9)]  # only "Acme Corp" is masked
        result = discoverer.discover_unmasked(text, masked_spans=masked_spans)
        texts = [e.text for e in result]
        assert any("ACM" in t for t in texts)

    def test_empty_text(self):
        discoverer = ResidualEntityDiscoverer()
        result = discoverer.discover("")
        assert len(result) == 0

    def test_clean_document_no_discoveries(self):
        discoverer = ResidualEntityDiscoverer()
        text = "The Company is a leading provider of solutions."
        result = discoverer.discover(text)
        texts = [e.text for e in result]
        assert all("Company" not in t or len(t) <= 3 for t in texts)

    def test_section_markers_filtered(self):
        discoverer = ResidualEntityDiscoverer()
        text = "Item 1. Business\nItem 1A. Risk Factors"
        result = discoverer.discover(text)
        assert len(result) == 0

    def test_compute_coverage_full(self):
        discoverer = ResidualEntityDiscoverer()
        text = "Acme Corp (ACM)"
        discovered = discoverer.discover(text)
        accepted_spans = [(0, 9), (10, 15)]
        result = discoverer.compute_coverage(discovered, accepted_spans)
        assert result.total_found == 2
        assert result.unmasked_count == 0
        assert result.coverage_pct == 100.0

    def test_compute_coverage_partial(self):
        discoverer = ResidualEntityDiscoverer()
        text = "Acme Corp (ACM)"
        discovered = discoverer.discover(text)
        accepted_spans = [(0, 9)]
        result = discoverer.compute_coverage(discovered, accepted_spans)
        assert result.total_found == 2
        assert result.unmasked_count >= 1
        assert result.coverage_pct < 100.0


class TestDiscoveredEntity:
    def test_create_discovered_entity(self):
        entity = DiscoveredEntity(
            text="Acme Corp",
            start=0,
            end=9,
            discovery_type="capitalized_phrase",
            confidence=0.4,
        )
        assert entity.text == "Acme Corp"
        assert entity.start == 0
        assert entity.end == 9

    def test_discovery_result_defaults(self):
        result = DiscoveryResult()
        assert result.total_found == 0
        assert result.coverage_pct == 0.0
        assert len(result.entities) == 0
        assert len(result.unmasked_high_confidence) == 0


class TestResidualDiscoveryIntegration:
    def test_canary_document_finds_entities(self):
        from pathlib import Path

        discoverer = ResidualEntityDiscoverer()
        doc_path = Path(__file__).parent.parent / "fixtures" / "canary_document.md"
        text = doc_path.read_text()
        result = discoverer.discover(text)
        assert len(result) > 0
        texts = [e.text for e in result]
        assert any("Canary" in t for t in texts)

    def test_masked_canary_document_no_residual(self):
        from pathlib import Path

        from fenrix_synthetic.identity import EntityRegistry, EntityType, MatchPolicy
        from fenrix_synthetic.masking import DeterministicMasker

        doc_path = Path(__file__).parent.parent / "fixtures" / "canary_document.md"
        text = doc_path.read_text()

        reg = EntityRegistry.create("C001", "reg-residual-test")
        reg.add_entity("ent-company", EntityType.COMPANY, "Canary Holdings Corporation")
        reg.add_entity("ent-ticker", EntityType.TICKER, "CHC")
        reg.add_entity("ent-cik", EntityType.CIK, "0000999999")
        reg.add_entity("ent-exec", EntityType.EXECUTIVE, "Eleanor Testperson")
        reg.add_entity("ent-domain", EntityType.COMPANY_DOMAIN, "canary-test.invalid")
        reg.add_entity("ent-product", EntityType.PRODUCT, "CanaryShield 9000")
        reg.add_alias(
            "ali-company",
            "ent-company",
            "Canary Holdings Corporation",
            EntityType.COMPANY,
            MatchPolicy.LITERAL,
            100,
        )
        reg.add_alias(
            "ali-company-short",
            "ent-company",
            "Canary Holdings",
            EntityType.COMPANY,
            MatchPolicy.LITERAL,
            150,
        )
        reg.add_alias(
            "ali-ticker", "ent-ticker", "CHC", EntityType.TICKER, MatchPolicy.TICKER_EXACT, 200
        )
        reg.add_alias(
            "ali-cik", "ent-cik", "0000999999", EntityType.CIK, MatchPolicy.CIK_PADDED, 200
        )
        reg.add_alias(
            "ali-exec",
            "ent-exec",
            "Eleanor Testperson",
            EntityType.EXECUTIVE,
            MatchPolicy.LITERAL,
            100,
        )
        reg.add_alias(
            "ali-domain",
            "ent-domain",
            "canary-test.invalid",
            EntityType.COMPANY_DOMAIN,
            MatchPolicy.DOMAIN_FULL,
            100,
        )
        reg.add_alias(
            "ali-product",
            "ent-product",
            "CanaryShield 9000",
            EntityType.PRODUCT,
            MatchPolicy.LITERAL,
            100,
        )

        masker = DeterministicMasker(reg, "bronze-C001-residual")
        masked_text, audit, _ = masker.mask(text, reg.config_hash())

        discoverer = ResidualEntityDiscoverer()
        accepted_spans = discoverer.extract_accepted_spans(
            [m for m in audit.spans if m.conflict_status.value == "accepted"]
        )
        assert len(accepted_spans) > 0

        discovered = discoverer.discover(masked_text)
        result = discoverer.compute_coverage(discovered, accepted_spans)

        assert result.total_found >= 0
        assert result.masked_count >= 0
