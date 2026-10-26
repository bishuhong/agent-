"""Vault KV v2 adapter; plaintext credentials never enter a control-plane record."""

from __future__ import annotations

from urllib.parse import quote, urlsplit

import httpx

from etl_agent.config import Settings
from etl_agent.modules.assets.ports import StoredSecret
from etl_agent.shared.errors import ApplicationError, unavailable


class VaultSecretProvider:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> None:
        self.url = settings.secret_provider_url
        self.token = settings.secret_provider_token.get_secret_value()
        self.transport = transport
        if self.url:
            parsed = urlsplit(self.url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Vault requires a configured HTTPS URL without inline credentials")

    def _request(self, method: str, locator: str, **kwargs) -> dict:
        if not self.url or not self.token:
            raise unavailable("Vault SecretProvider")
        try:
            with httpx.Client(
                timeout=10, follow_redirects=False, transport=self.transport
            ) as client:
                response = client.request(
                    method,
                    self.url.rstrip("/") + "/v1/secret/data/" + locator,
                    headers={"X-Vault-Token": self.token},
                    **kwargs,
                )
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("invalid Vault payload")
                return result
        except Exception as error:
            raise unavailable("Vault SecretProvider") from error

    def store(self, alias: str, values: dict[str, str]) -> StoredSecret:
        locator = quote(alias, safe="")
        result = self._request("POST", locator, json={"data": values})
        try:
            version = str(int(result["data"]["version"]))
            if int(version) < 1:
                raise ValueError("invalid version")
        except Exception as error:
            raise unavailable("Vault版本回执") from error
        return StoredSecret("vault_kv_v2", locator, version)

    def resolve(self, secret: StoredSecret) -> dict[str, str]:
        if secret.provider != "vault_kv_v2" or "/" in secret.locator or ".." in secret.locator:
            raise ApplicationError("STATE_CONFLICT", "Secret引用不属于此受管提供方", 409)
        result = self._request("GET", secret.locator, params={"version": secret.version})
        try:
            values = result["data"]["data"]
            actual_version = str(result["data"]["metadata"]["version"])
            if (
                actual_version != secret.version
                or not isinstance(values, dict)
                or not all(
                    isinstance(key, str) and isinstance(value, str) for key, value in values.items()
                )
            ):
                raise ValueError("invalid secret data")
            return values
        except Exception as error:
            raise unavailable("Vault精确版本读取") from error
