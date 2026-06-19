from __future__ import annotations

from .schemas import EntityType, PseudonymPolicy


class PseudonymGenerator:
    def __init__(self, policy: PseudonymPolicy | None = None):
        self._policy = policy or PseudonymPolicy()
        self._counters: dict[str, int] = {}

    @property
    def policy(self) -> PseudonymPolicy:
        return self._policy

    def generate(self, entity_type: EntityType, counter: int | None = None) -> str:
        if counter is None:
            etype = entity_type.value
            self._counters.setdefault(etype, 1)
            counter = self._counters[etype]
            self._counters[etype] += 1
        return self._policy.format_pseudonym(entity_type, counter)

    def configure(self, policy: PseudonymPolicy) -> None:
        self._policy = policy

    def reset_counters(self) -> None:
        self._counters.clear()
