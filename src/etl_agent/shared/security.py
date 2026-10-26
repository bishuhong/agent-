"""Session CSRF, opaque entity preconditions, signed cursors and capabilities."""

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from etl_agent.shared.canonical import canonical_bytes, decode_url, digest, encode_url
from etl_agent.shared.errors import ApplicationError, invalid, unavailable


def require_key(key: str, name: str) -> bytes:
    data = key.encode("utf-8")
    if len(data) < 32:
        raise unavailable(name)
    return data


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_csrf(raw_session: str, key: str) -> str:
    return encode_url(
        hmac.digest(require_key(key, "会话密钥"), b"csrf-v1:" + raw_session.encode(), "sha256")
    )


def entity_etag(value: Any) -> str:
    return '"' + digest(value) + '"'


def match_etag(supplied: str | None, current: str) -> None:
    if supplied is None:
        raise invalid("必须提供服务器返回的If-Match", "If-Match")
    if not hmac.compare_digest(supplied, current):
        raise ApplicationError("REVISION_MISMATCH", "对象已变化，请读取最新内容后重新审查", 412)


class CursorCodec:
    def __init__(self, key: str):
        self.key = key

    def encode(self, position: dict[str, Any], binding: dict[str, Any]) -> str:
        raw = canonical_bytes({"v": 1, "position": position, "binding": binding})
        signature = hmac.digest(require_key(self.key, "游标密钥"), raw, "sha256")
        return encode_url(raw) + "." + encode_url(signature)

    def decode(self, value: str, binding: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded, signed = value.split(".")
            raw = decode_url(encoded)
            expected = hmac.digest(require_key(self.key, "游标密钥"), raw, "sha256")
            if not hmac.compare_digest(expected, decode_url(signed)):
                raise ValueError("signature mismatch")
            data = json.loads(raw)
            if data["v"] != 1 or data["binding"] != binding:
                raise ValueError("scope mismatch")
            if not isinstance(data["position"], dict):
                raise ValueError("invalid position")
            return data["position"]
        except (ValueError, KeyError, TypeError, ApplicationError) as exc:
            if isinstance(exc, ApplicationError) and exc.status_code == 503:
                raise
            raise ApplicationError("INVALID_CURSOR", "游标无效或不属于当前查询", 400) from exc


class CapabilitySigner:
    def __init__(self, private_key: str, public_keys: dict[str, str], key_id: str):
        self.key_id = key_id
        self._private: Ed25519PrivateKey | None = None
        self._public: dict[str, Ed25519PublicKey] = {}
        if private_key:
            if "BEGIN" in private_key:
                candidate = serialization.load_pem_private_key(private_key.encode(), password=None)
            else:
                candidate = Ed25519PrivateKey.from_private_bytes(decode_url(private_key))
            if not isinstance(candidate, Ed25519PrivateKey):
                raise ValueError("只允许Ed25519签名密钥")
            self._private = candidate
            self._public[key_id] = candidate.public_key()
        for kid, value in public_keys.items():
            if "BEGIN" in value:
                pub = serialization.load_pem_public_key(value.encode())
            else:
                pub = Ed25519PublicKey.from_public_bytes(decode_url(value))
            if not isinstance(pub, Ed25519PublicKey):
                raise ValueError("只允许Ed25519验签公钥")
            self._public[kid] = pub

    @property
    def can_sign(self) -> bool:
        return self._private is not None

    def sign(self, claims: dict[str, Any]) -> dict[str, Any]:
        if self._private is None:
            raise unavailable("Capability签名密钥")
        unsigned = {key: value for key, value in claims.items() if key != "signature"}
        unsigned["key_id"] = self.key_id
        return {**unsigned, "signature": encode_url(self._private.sign(canonical_bytes(unsigned)))}

    def verify(self, signed: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
        unsigned = self.verify_signature(signed)
        try:
            issued = datetime.fromisoformat(unsigned["issued_at"].replace("Z", "+00:00"))
            expires = datetime.fromisoformat(unsigned["expires_at"].replace("Z", "+00:00"))
            current = now or datetime.now(timezone.utc)
            if (
                issued.tzinfo is None
                or expires.tzinfo is None
                or (expires - issued).total_seconds() != 300
            ):
                raise ValueError("invalid validity")
            if current < issued or current >= expires:
                raise ApplicationError("CAPABILITY_EXPIRED", "授权不在首次消费有效窗口内", 409)
            return unsigned
        except (KeyError, ValueError, TypeError) as exc:
            raise ApplicationError("FORBIDDEN", "能力时间绑定无效", 403) from exc

    def verify_signature(self, signed: dict[str, Any]) -> dict[str, Any]:
        unsigned = {key: value for key, value in signed.items() if key != "signature"}
        try:
            self._public[unsigned["key_id"]].verify(
                decode_url(signed["signature"]), canonical_bytes(unsigned)
            )
            return unsigned
        except (KeyError, ValueError, TypeError, InvalidSignature) as exc:
            raise ApplicationError("FORBIDDEN", "能力签名或绑定无效", 403) from exc
