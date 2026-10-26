from __future__ import annotations

import io
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from openpyxl import Workbook
from pydantic import SecretStr

from etl_agent.adapters.files.parsers import StructuredFileParser
from etl_agent.adapters.probes.postgresql import PostgreSQLProbe, approved_host
from etl_agent.adapters.secrets.vault import VaultSecretProvider
from etl_agent.api.schemas import contracts as c
from etl_agent.config import Settings
from etl_agent.modules.assets.ports import ProbeRegistry, StoredSecret
from etl_agent.modules.assets.service import AssetsService
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.shared.context import ActorContext
from etl_agent.shared.errors import ApplicationError


@pytest.fixture
def parser():
    return StructuredFileParser()


def test_csv_preserves_leading_zeroes_as_text(parser):
    parsed = parser.parse(b"account,email\n00123,a@example.com\n", "csv")
    assert [field["logical_type"] for field in parsed.fields] == ["string", "string"]
    assert parsed.statistics == {"row_count": "1", "is_approximate": False}
    assert parsed.fields[0]["sensitivity"] == "unclassified"


@pytest.mark.parametrize("content", [b"name,name\na,b\n", b"name,value\na,b,c\n", b"", b"\xff\xfe"])
def test_bad_csv_never_becomes_successful_empty_schema(parser, content):
    with pytest.raises(ApplicationError):
        parser.parse(content, "csv")


def test_json_decimal_precision_and_unknown_are_explicit(parser):
    parsed = parser.parse(
        b'[{"price":123.4500,"unknown":null},{"price":0.10,"unknown":null}]', "json"
    )
    assert parsed.fields[0]["logical_type"] == "decimal"
    assert parsed.fields[0]["precision"] == 7
    assert parsed.fields[0]["scale"] == 4
    assert parsed.fields[1]["logical_type"] == "unknown"


@pytest.mark.parametrize(
    "content",
    [
        b'[{"a":1,"a":2}]',
        b'{"data":[{"a":1}]}',
        b'[{"a":1},{"b":2}]',
        b'[{"a":1},{"a":"1"}]',
        b'[{"a":NaN}]',
    ],
)
def test_ambiguous_json_is_rejected(parser, content):
    with pytest.raises(ApplicationError):
        parser.parse(content, "json")


def xlsx_bytes(*, formula=False, second_sheet=False):
    workbook = Workbook()
    workbook.active.append(["name", "value"])
    workbook.active.append(["row", "=1+1" if formula else 2])
    if second_sheet:
        workbook.create_sheet("ambiguous")
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def test_xlsx_has_real_schema(parser):
    parsed = parser.parse(xlsx_bytes(), "xlsx")
    assert [field["logical_type"] for field in parsed.fields] == ["string", "integer"]
    assert parsed.statistics["row_count"] == "1"


@pytest.mark.parametrize("options", [{"formula": True}, {"second_sheet": True}])
def test_xlsx_does_not_guess_sheet_or_formula_result(parser, options):
    with pytest.raises(ApplicationError):
        parser.parse(xlsx_bytes(**options), "xlsx")


def test_parquet_exact_decimal_schema(parser):
    import pyarrow as pa
    import pyarrow.parquet as pq

    output = io.BytesIO()
    table = pa.table(
        {
            "id": pa.array([1], type=pa.int64()),
            "price": pa.array([Decimal("123.4500")], type=pa.decimal128(12, 4)),
        }
    )
    pq.write_table(table, output)
    parsed = parser.parse(output.getvalue(), "parquet")
    assert parsed.fields[1]["precision"] == 12
    assert parsed.fields[1]["scale"] == 4
    assert parsed.statistics["row_count"] == "1"


def test_file_limit_checks_actual_bytes_before_decode(parser, monkeypatch):
    monkeypatch.setattr("etl_agent.adapters.files.parsers.MAX_BYTES", 4)
    with pytest.raises(ApplicationError, match="100MiB"):
        parser.parse(b"a,b\n1,2\n", "csv")


def test_vault_is_unavailable_without_configuration():
    provider = VaultSecretProvider(Settings())
    with pytest.raises(ApplicationError) as failure:
        provider.store("safe", {"password": "do-not-log"})
    assert failure.value.status_code == 503
    assert "do-not-log" not in str(failure.value)


def test_vault_uses_exact_kv_version_and_does_not_follow_redirects():
    calls = []

    def handle(request):
        calls.append(request)
        assert request.headers["X-Vault-Token"] == "test-sensitive-token"
        if request.method == "POST":
            return httpx.Response(200, json={"data": {"version": 3}})
        assert request.url.params["version"] == "3"
        return httpx.Response(
            200,
            json={
                "data": {
                    "data": {"username": "etl", "password": "secret"},
                    "metadata": {"version": 3},
                }
            },
        )

    provider = VaultSecretProvider(
        Settings(
            secret_provider_url="https://vault.example",
            secret_provider_token=SecretStr("test-sensitive-token"),
        ),
        transport=httpx.MockTransport(handle),
    )
    reference = provider.store("safe_alias", {"username": "etl", "password": "secret"})
    assert reference.version == "3"
    assert provider.resolve(reference)["username"] == "etl"
    assert len(calls) == 2


def test_vault_redirect_failure_never_leaks_body():
    provider = VaultSecretProvider(
        Settings(
            secret_provider_url="https://vault.example",
            secret_provider_token=SecretStr("test-token"),
        ),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                302, headers={"location": "http://169.254.169.254/"}, text="private-token"
            )
        ),
    )
    with pytest.raises(ApplicationError) as failure:
        provider.resolve(StoredSecret("vault_kv_v2", "safe_alias", "1"))
    assert "private-token" not in str(failure.value)


def test_probe_capability_is_not_faked_for_unconfigured_connectors():
    with pytest.raises(ApplicationError) as failure:
        ProbeRegistry().require("oracle")
    assert failure.value.status_code == 503


def test_network_policy_rejects_unapproved_or_metadata_hosts(monkeypatch):
    with pytest.raises(ApplicationError) as failure:
        approved_host("unapproved.example", [])
    assert failure.value.status_code == 403
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a, **k: [(None, None, None, None, ("169.254.169.254", 0))]
    )
    with pytest.raises(ApplicationError):
        approved_host("metadata.example", ["metadata.example"])


def test_probe_uses_bound_parameters_and_read_only_configuration(monkeypatch):
    captured = {}

    def connect(**kwargs):
        captured.update(kwargs)
        return "connection"

    monkeypatch.setattr("psycopg.connect", connect)
    monkeypatch.setattr(
        "etl_agent.adapters.probes.postgresql.approved_host", lambda *a: ["127.0.0.1"]
    )
    probe = PostgreSQLProbe(["localhost"])
    assert (
        probe._connect(
            {"kind": "database", "host": "localhost", "port": 5432, "database": "test"},
            {"username": "test", "password": "sensitive"},
        )
        == "connection"
    )
    assert "default_transaction_read_only=on" in captured["options"]
    assert "statement_timeout=10000" in captured["options"]
    assert captured["hostaddr"] == "127.0.0.1"


def test_endpoint_environment_and_host_are_authoritative():
    service = AssetsService(Settings(endpoint_allowlist=["allowed.example"]), EvidenceLedger())
    with pytest.raises(ApplicationError):
        service._endpoint({"kind": "database", "host": "allowed.example"}, "production")
    with pytest.raises(ApplicationError):
        service._endpoint({"kind": "database", "host": "evil.example"}, "test")


def test_external_secret_call_never_discards_pending_writes():
    dirty_session = SimpleNamespace(new={"pending"}, dirty=set(), deleted=set())
    with pytest.raises(RuntimeError, match="clean request"):
        AssetsService._external(dirty_session, lambda: pytest.fail("must not call Vault"))


def test_managed_secret_ref_must_belong_to_target_project(monkeypatch):
    other = uuid4()
    monkeypatch.setattr(
        "etl_agent.modules.assets.service.project_entity",
        lambda *a, **k: SimpleNamespace(project_id=other),
    )
    actor = ActorContext(uuid4(), uuid4())
    service = AssetsService(Settings(), EvidenceLedger())
    with pytest.raises(ApplicationError) as failure:
        service._credential(
            None,
            actor,
            uuid4(),
            c.ManagedCredentialInput(mode="managed_ref", secret_ref_id=uuid4()),
        )
    assert failure.value.status_code == 404


def test_accept_test_rejects_unconfigured_adapter_before_any_write(monkeypatch):
    monkeypatch.setattr(
        "etl_agent.modules.assets.service.project_entity",
        lambda *a, **k: SimpleNamespace(connector_type="mysql"),
    )
    service = AssetsService(Settings(), EvidenceLedger())
    with pytest.raises(ApplicationError) as failure:
        service.start_test(None, ActorContext(uuid4(), uuid4()), uuid4())
    assert failure.value.status_code == 503


def test_safe_worker_failure_hides_unexpected_driver_exception():
    output = AssetsService._safe_failure(RuntimeError("password=do-not-leak host=internal"))
    assert output["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "do-not-leak" not in str(output)
