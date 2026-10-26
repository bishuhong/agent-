from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from etl_agent.shared.canonical import artifact_package, canonical_bytes, digest, encode_url
from etl_agent.shared.errors import ApplicationError
from etl_agent.shared.security import (
    CapabilitySigner,
    CursorCodec,
    entity_etag,
    match_etag,
    session_csrf,
)


def test_canonical_golden_vector_and_no_float():
    assert (
        canonical_bytes({"中文": [None, "9007199254740993"], "a": True})
        == b'{"a":true,"\xe4\xb8\xad\xe6\x96\x87":[null,"9007199254740993"]}'
    )
    assert digest({"b": "2", "a": "1"}) == digest({"a": "1", "b": "2"})
    assert digest(["1", "2"]) != digest(["2", "1"])
    with pytest.raises(ApplicationError):
        canonical_bytes({"amount": 0.1})


def test_fingerprint_and_artifact_fixed_sha256_vectors():
    assert (
        digest(
            {
                "purpose": "isolated_trial",
                "note": "中文\n",
                "count": "9007199254740993",
                "value": None,
            }
        )
        == "0832ada0f8da9327880cfe86d3fb5d99b37748cfba127d78e53f9953f11ba4d1"
    )
    artifacts = {
        "etl_plan": {"name": "中文", "source": ["a", "b"]},
        "hocon": "env { parallelism=1 }\n",
        "dag": {"nodes": [], "edges": []},
        "quality_contract": {"allow_empty_input": False, "max_rejection_ratio": "0.05"},
        "supervision_contract": {"max_input_records": "9007199254740993"},
    }
    root, _ = artifact_package(artifacts)
    assert root == "bf88b0f79ba00d4ee3f990643162c8ab173b2d8bfc1da874286e45848ca53416"


def test_csrf_stable_per_session_and_etag_exact_entity():
    key = "k" * 32
    assert session_csrf("session-a", key) == session_csrf("session-a", key)
    assert session_csrf("session-a", key) != session_csrf("session-b", key)
    tag = entity_etag({"id": "one", "revision": 2})
    match_etag(tag, tag)
    with pytest.raises(ApplicationError) as error:
        match_etag(tag, entity_etag({"id": "two", "revision": 2}))
    assert error.value.status_code == 412


def test_cursor_tamper_and_scope_rejected():
    codec = CursorCodec("c" * 32)
    binding = {"project_id": "one", "query": "abc"}
    value = codec.encode({"id": str(uuid4())}, binding)
    assert "id" in codec.decode(value, binding)
    for changed, scope in [(value + "x", binding), (value, {"project_id": "two"})]:
        with pytest.raises(ApplicationError) as error:
            codec.decode(changed, scope)
        assert error.value.code == "INVALID_CURSOR"


def test_capability_exact_expiry_and_tamper():
    key = Ed25519PrivateKey.generate()
    signer = CapabilitySigner(encode_url(key.private_bytes_raw()), {}, "test")
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    claims = signer.sign(
        {"command_id": str(uuid4()), "issued_at": now, "expires_at": now + timedelta(minutes=5)}
    )
    # The persisted JSON representation is what is later verified.
    import json

    signed = json.loads(canonical_bytes(claims))
    signer.verify(signed, now)
    with pytest.raises(ApplicationError) as error:
        signer.verify(signed, now + timedelta(minutes=5))
    assert error.value.code == "CAPABILITY_EXPIRED"
    signed["command_id"] = str(uuid4())
    with pytest.raises(ApplicationError):
        signer.verify(signed, now)
