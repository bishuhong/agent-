"""Versioned canonical bytes for artifacts, fingerprints and signed claims."""

import base64
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from etl_agent.shared.errors import invalid

SCHEMA_VERSION = 1
ARTIFACT_ORDER = ("etl_plan", "hocon", "dag", "quality_contract", "supervision_contract")


def json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise invalid("时间必须包含时区")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise invalid("规范JSON的对象键必须是字符串")
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    # Decimal contract values must be explicit strings, never an imprecise float.
    raise invalid("规范JSON不接受浮点数或未知类型")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(value: Any) -> str:
    return sha256(canonical_bytes(value))


def artifact_package(artifacts: dict[str, Any]) -> tuple[str, dict[str, str]]:
    if set(artifacts) != set(ARTIFACT_ORDER):
        raise invalid("冻结制品必须完整且类型唯一")
    digests = {
        kind: sha256(artifacts[kind].encode("utf-8"))
        if kind == "hocon" and isinstance(artifacts[kind], str)
        else digest(artifacts[kind])
        for kind in ARTIFACT_ORDER
    }
    root = {
        "schema_version": SCHEMA_VERSION,
        "artifacts": [{"type": kind, "digest": digests[kind]} for kind in ARTIFACT_ORDER],
    }
    return digest(root), digests


def encode_url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_url(value: str) -> bytes:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise invalid("编码值无效") from exc
