"""Narrow asset adapter contracts. No Celery, ORM, or frontend state lives here."""

from dataclasses import dataclass
from typing import Any, Protocol

from etl_agent.shared.errors import unavailable


@dataclass(frozen=True)
class StoredSecret:
    provider: str
    locator: str
    version: str


class SecretProvider(Protocol):
    def store(self, alias: str, values: dict[str, str]) -> StoredSecret: ...
    def resolve(self, secret: StoredSecret) -> dict[str, str]: ...


@dataclass(frozen=True)
class ParsedFile:
    fields: list[dict[str, Any]]
    statistics: dict[str, Any]


class FileParser(Protocol):
    def parse(self, content: bytes, file_format: str) -> ParsedFile: ...


@dataclass(frozen=True)
class ProbeProfile:
    resource_name: str
    fields: list[dict[str, Any]]
    statistics: dict[str, Any]
    masked_samples: list[dict[str, Any]]


class ProbeAdapter(Protocol):
    def test(self, endpoint: dict[str, Any], credentials: dict[str, str]) -> None: ...
    def profile(
        self,
        endpoint: dict[str, Any],
        credentials: dict[str, str],
        resource_names: list[str],
        sample_limit: int,
    ) -> list[ProbeProfile]: ...


class ProbeRegistry:
    def __init__(self, adapters: dict[str, ProbeAdapter] | None = None) -> None:
        self.adapters = dict(adapters or {})

    def require(self, connector: str) -> ProbeAdapter:
        adapter = self.adapters.get(connector)
        if adapter is None:
            raise unavailable(f"{connector}只读探查适配器")
        return adapter
