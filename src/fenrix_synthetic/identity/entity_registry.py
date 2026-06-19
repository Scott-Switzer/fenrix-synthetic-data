from __future__ import annotations

from .pseudonyms import PseudonymGenerator
from .schemas import (
    Alias,
    CanonicalEntity,
    EntityType,
    IdentityRegistry,
    MatchPolicy,
    PseudonymPolicy,
    RegistryMetadata,
)


class EntityRegistry:
    def __init__(
        self,
        metadata: RegistryMetadata,
        generator: PseudonymGenerator | None = None,
    ):
        self.metadata = metadata
        self.entities: dict[str, CanonicalEntity] = {}
        self.aliases: dict[str, Alias] = {}
        self._alias_by_value: dict[str, list[Alias]] = {}
        self._pseudonym_generator = generator or PseudonymGenerator(PseudonymPolicy())
        self._next_counters: dict[str, int] = {}

    @classmethod
    def create(
        cls,
        company_id: str,
        registry_id: str,
        config_hash: str = "",
        pseudonym_policy: PseudonymPolicy | None = None,
    ) -> EntityRegistry:
        metadata = RegistryMetadata(
            registry_id=registry_id,
            company_id=company_id,
            registry_config_hash=config_hash,
            pseudonym_policy_version=(
                pseudonym_policy.policy_version if pseudonym_policy else "1.0.0"
            ),
        )
        generator = PseudonymGenerator(pseudonym_policy or PseudonymPolicy())
        return cls(metadata=metadata, generator=generator)

    def add_entity(
        self,
        entity_id: str,
        entity_type: EntityType,
        canonical_value: str,
        source_refs: list[str] | None = None,
    ) -> CanonicalEntity:
        if entity_id in self.entities:
            raise ValueError(f"entity_id '{entity_id}' already exists")
        counter = self._next_counters.get(entity_type.value, 1)
        pseudonym = self._pseudonym_generator.generate(entity_type, counter)
        self._next_counters[entity_type.value] = counter + 1
        entity = CanonicalEntity(
            entity_id=entity_id,
            company_id=self.metadata.company_id,
            entity_type=entity_type,
            canonical_private_value=canonical_value,
            assigned_pseudonym=pseudonym,
            source_references=source_refs or [],
        )
        self.entities[entity_id] = entity
        return entity

    def add_alias(
        self,
        alias_id: str,
        entity_id: str,
        alias_value: str,
        entity_type: EntityType = EntityType.COMPANY,
        match_policy: MatchPolicy = MatchPolicy.LITERAL,
        priority: int = 100,
    ) -> Alias:
        if alias_id in self.aliases:
            raise ValueError(f"alias_id '{alias_id}' already exists")
        if entity_id not in self.entities:
            raise ValueError(f"entity_id '{entity_id}' not found")
        alias = Alias(
            alias_id=alias_id,
            canonical_entity_id=entity_id,
            private_alias_value=alias_value,
            entity_type=entity_type,
            match_policy=match_policy,
            priority=priority,
        )
        self.aliases[alias_id] = alias
        norm = alias_value.lower().strip()
        self._alias_by_value.setdefault(norm, []).append(alias)
        return alias

    def get_entity(self, entity_id: str) -> CanonicalEntity | None:
        return self.entities.get(entity_id)

    def get_alias(self, alias_id: str) -> Alias | None:
        return self.aliases.get(alias_id)

    def get_aliases_by_value(self, value: str) -> list[Alias]:
        return self._alias_by_value.get(value.lower().strip(), [])

    def get_pseudonym(self, entity_id: str) -> str:
        entity = self.entities.get(entity_id)
        if entity is None:
            raise KeyError(f"entity_id '{entity_id}' not found")
        return entity.assigned_pseudonym

    def remove_entity(self, entity_id: str) -> None:
        if entity_id not in self.entities:
            raise KeyError(f"entity_id '{entity_id}' not found")
        alias_ids = [
            a.alias_id for a in self.aliases.values() if a.canonical_entity_id == entity_id
        ]
        for aid in alias_ids:
            self.remove_alias(aid)
        del self.entities[entity_id]

    def remove_alias(self, alias_id: str) -> None:
        if alias_id not in self.aliases:
            raise KeyError(f"alias_id '{alias_id}' not found")
        alias = self.aliases[alias_id]
        norm = alias.private_alias_value.lower().strip()
        if norm in self._alias_by_value:
            self._alias_by_value[norm] = [
                a for a in self._alias_by_value[norm] if a.alias_id != alias_id
            ]
            if not self._alias_by_value[norm]:
                del self._alias_by_value[norm]
        del self.aliases[alias_id]

    def all_entities(self) -> list[CanonicalEntity]:
        return list(self.entities.values())

    def all_aliases(self) -> list[Alias]:
        return list(self.aliases.values())

    def to_registry(self) -> IdentityRegistry:
        return IdentityRegistry(
            metadata=self.metadata,
            entities=self.all_entities(),
            aliases=self.all_aliases(),
        )

    @classmethod
    def from_registry(cls, registry: IdentityRegistry) -> EntityRegistry:
        inst = cls(metadata=registry.metadata)
        for entity in registry.entities:
            inst.entities[entity.entity_id] = entity
            etype = entity.entity_type.value
            c = 1
            if etype in inst._next_counters:
                c = inst._next_counters[etype] + 1
            inst._next_counters[etype] = c
        for alias in registry.aliases:
            inst.aliases[alias.alias_id] = alias
            norm = alias.private_alias_value.lower().strip()
            inst._alias_by_value.setdefault(norm, []).append(alias)
        return inst

    def config_hash(self) -> str:
        import hashlib

        import orjson

        sorted_entities = sorted(self.entities.values(), key=lambda e: e.entity_id)
        sorted_aliases = sorted(self.aliases.values(), key=lambda a: a.alias_id)
        data = {
            "company_id": self.metadata.company_id,
            "entities": [
                {
                    "entity_id": e.entity_id,
                    "entity_type": e.entity_type.value,
                    "canonical_private_value": e.canonical_private_value,
                    "assigned_pseudonym": e.assigned_pseudonym,
                }
                for e in sorted_entities
            ],
            "aliases": [
                {
                    "alias_id": a.alias_id,
                    "canonical_entity_id": a.canonical_entity_id,
                    "private_alias_value": a.private_alias_value,
                    "match_policy": a.match_policy.value,
                    "priority": a.priority,
                }
                for a in sorted_aliases
            ],
        }
        raw = orjson.dumps(data, option=orjson.OPT_SORT_KEYS)
        return hashlib.sha256(raw).hexdigest()
