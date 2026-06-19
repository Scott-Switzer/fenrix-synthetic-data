from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import DiscoveryChunk, EntityDiscoveryResponse


class DiscoveryError(Exception):
    pass


class ProviderUnavailableError(DiscoveryError):
    pass


class ProviderTimeoutError(DiscoveryError):
    pass


class ProviderResponseError(DiscoveryError):
    pass


class ProviderConfigurationError(DiscoveryError):
    pass


class EntityDiscoveryProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def model_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def model_version(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def discover(
        self,
        chunk: DiscoveryChunk,
        labels: list[str],
        context: dict | None = None,
    ) -> EntityDiscoveryResponse:
        raise NotImplementedError

    def supports_label(self, label: str) -> bool:
        return True

    def dispose(self) -> None:  # noqa: B027
        """Clean up provider resources. Override to implement cleanup."""
        pass
