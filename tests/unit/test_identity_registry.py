from __future__ import annotations

import pytest

from fenrix_synthetic.identity import (
    Alias,
    BoundaryPolicy,
    CanonicalEntity,
    CasePolicy,
    EntityRegistry,
    EntityType,
    IdentityRegistry,
    MatchPolicy,
    MutationPolicy,
    PseudonymGenerator,
    PseudonymPolicy,
    RegistryMetadata,
    RegistryStatus,
)


class TestEntityType:
    def test_has_required_types(self):
        required = [
            "company",
            "former_company_name",
            "ticker",
            "cik",
            "sec_accession_number",
            "executive",
            "board_member",
            "subsidiary",
            "product",
            "brand",
            "company_domain",
            "facility",
            "headquarters",
            "auditor",
            "customer",
        ]
        for r in required:
            assert r in EntityType._value2member_map_, f"missing entity type: {r}"


class TestRegistryMetadata:
    def test_default_timestamps(self):
        meta = RegistryMetadata(registry_id="test-001", company_id="C100")
        assert meta.status == RegistryStatus.ACTIVE
        assert meta.schema_version == "1.0.0"
        assert meta.private_classification == "private"

    def test_private_classification_enforced(self):
        meta = RegistryMetadata(registry_id="test-002", company_id="C100")
        assert meta.private_classification == "private"


class TestCanonicalEntity:
    def test_empty_entity_id_rejected(self):
        with pytest.raises(ValueError):
            CanonicalEntity(
                entity_id="",
                company_id="C001",
                entity_type=EntityType.COMPANY,
                canonical_private_value="Test Corp",
            )

    def test_empty_value_rejected(self):
        with pytest.raises(ValueError):
            CanonicalEntity(
                entity_id="ent-001",
                company_id="C001",
                entity_type=EntityType.COMPANY,
                canonical_private_value="  ",
            )

    def test_valid_entity(self):
        entity = CanonicalEntity(
            entity_id="ent-001",
            company_id="C001",
            entity_type=EntityType.EXECUTIVE,
            canonical_private_value="Jane Smith",
            assigned_pseudonym="Executive 001",
        )
        assert entity.entity_id == "ent-001"
        assert entity.assigned_pseudonym == "Executive 001"
        assert entity.active is True


class TestAlias:
    def test_empty_id_rejected(self):
        with pytest.raises(ValueError):
            Alias(
                alias_id="",
                canonical_entity_id="ent-001",
                private_alias_value="Test",
            )

    def test_empty_value_rejected(self):
        with pytest.raises(ValueError):
            Alias(
                alias_id="ali-001",
                canonical_entity_id="ent-001",
                private_alias_value="",
            )

    def test_default_policies(self):
        alias = Alias(
            alias_id="ali-001",
            canonical_entity_id="ent-001",
            private_alias_value="Test Corp",
        )
        assert alias.match_policy == MatchPolicy.LITERAL
        assert alias.case_policy == CasePolicy.CASE_INSENSITIVE
        assert alias.boundary_policy == BoundaryPolicy.WORD
        assert alias.priority == 100
        assert alias.active is True

    def test_mutation_policies(self):
        alias = Alias(
            alias_id="ali-002",
            canonical_entity_id="ent-001",
            private_alias_value="Test Corp",
            enabled_mutation_policies=[
                MutationPolicy.POSSESSIVE,
                MutationPolicy.DASH_VARIANT,
            ],
        )
        assert MutationPolicy.POSSESSIVE in alias.enabled_mutation_policies


class TestEntityRegistry:
    def test_create_registry(self):
        reg = EntityRegistry.create(
            company_id="C001",
            registry_id="reg-test-001",
        )
        assert reg.metadata.company_id == "C001"
        assert reg.metadata.registry_id == "reg-test-001"
        assert reg.metadata.status == RegistryStatus.ACTIVE

    def test_add_entity(self):
        reg = EntityRegistry.create("C001", "reg-test-002")
        entity = reg.add_entity(
            entity_id="ent-001",
            entity_type=EntityType.COMPANY,
            canonical_value="Test Corp",
            source_refs=["sec-10k-2024"],
        )
        assert entity.entity_id == "ent-001"
        assert entity.company_id == "C001"
        assert entity.assigned_pseudonym == "Company 001"
        assert "sec-10k-2024" in entity.source_references

    def test_duplicate_entity_rejected(self):
        reg = EntityRegistry.create("C001", "reg-test-003")
        reg.add_entity("ent-001", EntityType.COMPANY, "Test Corp")
        with pytest.raises(ValueError, match="already exists"):
            reg.add_entity("ent-001", EntityType.COMPANY, "Test Corp Again")

    def test_add_alias(self):
        reg = EntityRegistry.create("C001", "reg-test-004")
        reg.add_entity("ent-001", EntityType.COMPANY, "Test Corp")
        alias = reg.add_alias(
            alias_id="ali-001",
            entity_id="ent-001",
            alias_value="Test Corp (TC)",
            entity_type=EntityType.COMPANY,
            match_policy=MatchPolicy.LITERAL,
            priority=150,
        )
        assert alias.alias_id == "ali-001"
        assert alias.canonical_entity_id == "ent-001"
        assert alias.priority == 150

    def test_duplicate_alias_rejected(self):
        reg = EntityRegistry.create("C001", "reg-test-005")
        reg.add_entity("ent-001", EntityType.COMPANY, "Test Corp")
        reg.add_alias("ali-001", "ent-001", "Test Corp")
        with pytest.raises(ValueError, match="already exists"):
            reg.add_alias("ali-001", "ent-001", "Test Corp Again")

    def test_alias_unknown_entity_rejected(self):
        reg = EntityRegistry.create("C001", "reg-test-006")
        with pytest.raises(ValueError, match="not found"):
            reg.add_alias("ali-001", "ent-999", "Ghost Corp")

    def test_get_pseudonym(self):
        reg = EntityRegistry.create("C001", "reg-test-007")
        reg.add_entity("ent-001", EntityType.EXECUTIVE, "Jane Smith")
        p = reg.get_pseudonym("ent-001")
        assert p == "Executive 001"

    def test_get_pseudonym_missing_raises(self):
        reg = EntityRegistry.create("C001", "reg-test-008")
        with pytest.raises(KeyError):
            reg.get_pseudonym("ent-999")

    def test_remove_entity(self):
        reg = EntityRegistry.create("C001", "reg-test-009")
        reg.add_entity("ent-001", EntityType.COMPANY, "Test Corp")
        reg.add_alias("ali-001", "ent-001", "Test Corp")
        reg.remove_entity("ent-001")
        assert reg.get_entity("ent-001") is None
        assert reg.get_alias("ali-001") is None

    def test_remove_alias(self):
        reg = EntityRegistry.create("C001", "reg-test-010")
        reg.add_entity("ent-001", EntityType.COMPANY, "Test Corp")
        reg.add_alias("ali-001", "ent-001", "Test Corp")
        reg.remove_alias("ali-001")
        assert reg.get_alias("ali-001") is None

    def test_uniqueness_across_entity_types(self):
        reg = EntityRegistry.create("C001", "reg-test-011")
        e1 = reg.add_entity("ent-001", EntityType.COMPANY, "Test Corp")
        e2 = reg.add_entity("ent-002", EntityType.TICKER, "TC")
        e3 = reg.add_entity("ent-003", EntityType.CIK, "0000123456")
        assert e1.assigned_pseudonym == "Company 001"
        assert e2.assigned_pseudonym == "Ticker 001"
        assert e3.assigned_pseudonym == "Cik 001"

    def test_company_id_mismatch(self):
        reg = EntityRegistry.create("C001", "reg-test-012")
        reg.add_entity("ent-001", EntityType.COMPANY, "Test Corp")
        registry = reg.to_registry()
        registry.entities[0].company_id = "WRONG"
        assert registry.validate_company_id_consistency() is False

    def test_to_from_registry_roundtrip(self):
        reg = EntityRegistry.create("C001", "reg-test-013")
        reg.add_entity("ent-001", EntityType.COMPANY, "Test Corp")
        reg.add_alias("ali-001", "ent-001", "Test Corp")
        reg.add_alias("ali-002", "ent-001", "TC", match_policy=MatchPolicy.TICKER_EXACT)

        registry = reg.to_registry()
        assert isinstance(registry, IdentityRegistry)
        assert len(registry.entities) == 1
        assert len(registry.aliases) == 2

        reg2 = EntityRegistry.from_registry(registry)
        assert reg2.get_entity("ent-001") is not None
        assert reg2.get_alias("ali-001") is not None
        assert reg2.get_alias("ali-002") is not None

    def test_config_hash_stable(self):
        reg1 = EntityRegistry.create("C001", "reg-test-014")
        reg1.add_entity("ent-001", EntityType.COMPANY, "Test Corp")
        reg1.add_alias("ali-001", "ent-001", "Test Corp")
        hash1 = reg1.config_hash()

        reg2 = EntityRegistry.create("C001", "reg-test-014")
        reg2.add_entity("ent-001", EntityType.COMPANY, "Test Corp")
        reg2.add_alias("ali-001", "ent-001", "Test Corp")
        hash2 = reg2.config_hash()

        assert hash1 == hash2

    def test_config_hash_changes_on_new_entity(self):
        reg1 = EntityRegistry.create("C001", "reg-test-015")
        reg1.add_entity("ent-001", EntityType.COMPANY, "Test Corp")
        hash1 = reg1.config_hash()

        reg2 = EntityRegistry.create("C001", "reg-test-015")
        reg2.add_entity("ent-001", EntityType.COMPANY, "Test Corp")
        reg2.add_entity("ent-002", EntityType.EXECUTIVE, "Jane Smith")
        hash2 = reg2.config_hash()

        assert hash1 != hash2

    def test_empty_and_whitespace_aliases(self):
        reg = EntityRegistry.create("C001", "reg-test-016")
        reg.add_entity("ent-001", EntityType.COMPANY, "Test Corp")
        with pytest.raises(ValueError):
            Alias(alias_id="", canonical_entity_id="ent-001", private_alias_value=" ")
        with pytest.raises(ValueError):
            Alias(alias_id="ali-001", canonical_entity_id="ent-001", private_alias_value="")

    def test_private_classification_enforcement(self):
        reg = EntityRegistry.create("C001", "reg-test-017")
        reg.add_entity("ent-001", EntityType.COMPANY, "Private Corp")
        registry = reg.to_registry()
        assert registry.metadata.private_classification == "private"

    def test_source_reference_validation(self):
        reg = EntityRegistry.create("C001", "reg-test-018")
        entity = reg.add_entity(
            "ent-001",
            EntityType.COMPANY,
            "Test Corp",
            source_refs=["sec-Edgar-10k-2024"],
        )
        assert len(entity.source_references) == 1
        assert "sec-Edgar-10k-2024" in entity.source_references


class TestPseudonymPolicy:
    def test_default_policy(self):
        policy = PseudonymPolicy()
        result = policy.format_pseudonym(EntityType.COMPANY, 1)
        assert result == "Company 001"

    def test_all_entity_types_format(self):
        policy = PseudonymPolicy()
        for etype in EntityType:
            result = policy.format_pseudonym(etype, 1)
            assert "001" in result
            assert etype.value.replace("_", " ").title().replace(" ", "") in result

    def test_counter_zero_padded(self):
        policy = PseudonymPolicy()
        r1 = policy.format_pseudonym(EntityType.PRODUCT, 1)
        r2 = policy.format_pseudonym(EntityType.PRODUCT, 50)
        r3 = policy.format_pseudonym(EntityType.PRODUCT, 999)
        assert r1 == "Product 001"
        assert r2 == "Product 050"
        assert r3 == "Product 999"

    def test_policy_version(self):
        policy = PseudonymPolicy(policy_version="2.0.0")
        assert policy.policy_version == "2.0.0"


class TestPseudonymGenerator:
    def test_generate_stable(self):
        gen = PseudonymGenerator()
        p1 = gen.generate(EntityType.COMPANY, 1)
        p2 = gen.generate(EntityType.COMPANY, 1)
        p3 = gen.generate(EntityType.COMPANY, 2)
        assert p1 == p2
        assert p1 != p3

    def test_auto_counter(self):
        gen = PseudonymGenerator()
        p1 = gen.generate(EntityType.COMPANY)
        p2 = gen.generate(EntityType.COMPANY)
        p3 = gen.generate(EntityType.EXECUTIVE)
        assert p1 == "Company 001"
        assert p2 == "Company 002"
        assert p3 == "Executive 001"

    def test_reset_counters(self):
        gen = PseudonymGenerator()
        gen.generate(EntityType.COMPANY)
        gen.generate(EntityType.COMPANY)
        gen.reset_counters()
        p = gen.generate(EntityType.COMPANY)
        assert p == "Company 001"

    def test_different_types_independent_counters(self):
        gen = PseudonymGenerator()
        gen.generate(EntityType.COMPANY, 5)
        gen.generate(EntityType.TICKER, 1)
        gen.generate(EntityType.TICKER, 2)
        assert gen.generate(EntityType.EXECUTIVE) == "Executive 001"
