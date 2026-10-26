"""Validated environment configuration. No secrets have usable default values."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ETL_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr = SecretStr("")
    redis_url: SecretStr = SecretStr("")
    allowed_origins: list[str] = Field(default_factory=list)
    cookie_name: str = "etl_session"
    cookie_secure: bool = True
    session_key: SecretStr = SecretStr("")
    cursor_key: SecretStr = SecretStr("")
    capability_private_key: SecretStr = SecretStr("")
    capability_public_keys: dict[str, str] = Field(default_factory=dict)
    capability_key_id: str = "default"
    storage_root: Path = Path(".runtime/assets")
    checkpoint_anchor_root: Path | None = None
    project_managers: dict[str, list[str]] = Field(default_factory=dict)
    permitted_environments: list[str] = Field(default_factory=lambda: ["development", "test"])
    isolated_environments: list[str] = Field(default_factory=lambda: ["test"])
    policy_version: str = "1"
    classification_risks: dict[str, Literal["P0", "P1", "P2", "P3"]] = Field(default_factory=dict)
    endpoint_allowlist: list[str] = Field(default_factory=list)
    model_providers: dict[str, dict[str, str]] = Field(default_factory=dict)
    secret_provider_url: str | None = None
    secret_provider_token: SecretStr = SecretStr("")
    seatunnel_url: str | None = None
    seatunnel_capabilities: frozenset[str] = frozenset()
    probe_connectors: frozenset[str] = frozenset()
    supervision_poll_seconds: int = Field(default=10, ge=1, le=300)
    max_upload_bytes: int = Field(default=104857600, ge=1, le=104857600)
    login_failures: int = Field(default=5, ge=1, le=20)
    login_window_seconds: int = Field(default=900, ge=60, le=3600)
    scrypt_n: int = Field(default=131072, ge=131072)
    scrypt_r: int = Field(default=8, ge=8)
    scrypt_p: int = Field(default=1, ge=1)
    password_parallelism: int = Field(default=2, ge=1, le=8)

    @model_validator(mode="after")
    def production_requirements(self) -> "Settings":
        if self.environment == "production":
            if not self.cookie_secure:
                raise ValueError("生产会话Cookie必须启用Secure")
            if not self.allowed_origins or any(
                not value.startswith("https://") for value in self.allowed_origins
            ):
                raise ValueError("生产Origin必须明确配置为HTTPS地址")
            for key in (self.session_key, self.cursor_key):
                if len(key.get_secret_value().encode()) < 32:
                    raise ValueError("生产会话/游标密钥必须至少32字节")
        return self
