"""HTTP/session/permission contracts against a migrated, isolated PostgreSQL schema."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from etl_agent.bootstrap import BackendRuntime
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.database import Database
from etl_agent.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def http_backend():
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("TEST_DATABASE_URL absent")
    url = make_url(raw).set(drivername="postgresql+psycopg")
    schema = "http_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, connect_args={"options": f"-c search_path={schema} -c timezone=UTC"}
    )
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        database = object.__new__(Database)
        database.engine = engine
        database.session_factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
        database.sessionmaker = database.session_factory
        settings = Settings(
            environment="test",
            session_key="a" * 40,
            cursor_key="b" * 40,
            cookie_secure=False,
            allowed_origins=["http://testserver"],
        )
        runtime = BackendRuntime(settings, database=database)
        with database.session_factory.begin() as session:
            user = m.User(
                username="httpmaker",
                subject_ref="local:httpmaker",
                person_ref=uuid4(),
                display_name="Maker",
                status="enabled",
            )
            project = m.Project(name="Visible", code=uuid4().hex)
            hidden = m.Project(name="Hidden", code=uuid4().hex)
            session.add_all([user, project, hidden])
            session.flush()
            session.add_all(
                [
                    m.LocalCredential(
                        user_id=user.id,
                        password_hash=runtime.auth.hasher.hash("Test-password-123!"),
                    ),
                    m.ProjectMembership(project_id=project.id, user_id=user.id, role="maker"),
                ]
            )
            ids = project.id, hidden.id, user.id
        yield runtime, create_app(runtime=runtime), ids
    finally:
        engine.dispose()
        assert schema.startswith("http_test_") and schema.removeprefix("http_test_").isalnum()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture()
def logged_in(http_backend):
    runtime, app, ids = http_backend
    with TestClient(app) as client:
        result = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://testserver"},
            json={"username": "httpmaker", "password": "Test-password-123!"},
        )
        assert result.status_code == 200, result.text
        csrf = result.json()["data"]["csrf_token"]
        yield client, {"Origin": "http://testserver", "X-CSRF-Token": csrf}, runtime, ids


def test_session_csrf_stability_and_revocation(logged_in):
    client, headers, runtime, ids = logged_in
    first = client.get("/api/v1/auth/me")
    second = client.get("/api/v1/auth/me")
    assert first.status_code == second.status_code == 200
    assert (
        first.json()["data"]["csrf_token"]
        == second.json()["data"]["csrf_token"]
        == headers["X-CSRF-Token"]
    )
    assert "person_ref" not in first.text and "password" not in first.text
    assert (
        client.post("/api/v1/auth/logout", headers={"Origin": "http://testserver"}).status_code
        == 403
    )
    cookie = client.cookies.get(runtime.settings.cookie_name)
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    client.cookies.set(runtime.settings.cookie_name, cookie)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_project_isolation_and_etag(logged_in):
    client, headers, runtime, (project, hidden, user) = logged_in
    response = client.get(f"/api/v1/projects/{project}")
    assert response.status_code == 200
    assert response.headers["etag"].startswith('"')
    assert client.get(f"/api/v1/projects/{hidden}").status_code in {403, 404}
    result = client.get("/api/v1/projects")
    assert result.status_code == 200, result.text
    assert [row["id"] for row in result.json()["items"]] == [str(project)]


def test_origin_is_required_even_for_login(http_backend):
    _, app, _ = http_backend
    with TestClient(app) as client:
        for headers in ({}, {"Origin": "https://attacker.invalid"}):
            response = client.post(
                "/api/v1/auth/login",
                headers=headers,
                json={"username": "httpmaker", "password": "secret-value"},
            )
            assert response.status_code == 403
            assert "secret-value" not in response.text


def test_validation_redacts_password(http_backend):
    _, app, _ = http_backend
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://testserver"},
            json={"username": [], "password": {"sensitive": "never-echo"}},
        )
        assert response.status_code == 422
        assert "never-echo" not in response.text
        assert response.json()["error"]["field_errors"]


def test_login_failure_is_committed_outside_http_transaction(http_backend):
    runtime, app, _ = http_backend
    with TestClient(app) as client:
        for _ in range(5):
            assert (
                client.post(
                    "/api/v1/auth/login",
                    headers={"Origin": "http://testserver"},
                    json={"username": "missing-user", "password": "wrong-password"},
                ).status_code
                == 401
            )
        assert (
            client.post(
                "/api/v1/auth/login",
                headers={"Origin": "http://testserver"},
                json={"username": "missing-user", "password": "wrong-password"},
            ).status_code
            == 429
        )
    with runtime.database.session_factory() as session:
        assert (
            session.scalar(select(m.LoginThrottle.failures).where(m.LoginThrottle.failures == 5))
            == 5
        )


def test_pipeline_write_is_persisted_before_success(logged_in):
    client, headers, runtime, (project, _, user) = logged_in
    payload = {
        "project_id": str(project),
        "name": "pipeline",
        "code": "p_" + uuid4().hex,
        "description": None,
    }
    response = client.post("/api/v1/pipelines", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    body = response.json()["data"]
    result = client.get(f"/api/v1/versions/{body['candidate_version_id']}/design")
    assert result.status_code == 200, result.text
    assert result.headers["etag"]
    conflict = client.post(
        f"/api/v1/versions/{body['candidate_version_id']}/generation",
        headers={**headers, "If-Match": '"stale-entity"'},
        json={
            "prompt": "copy",
            "source_profile_ids": [str(uuid4())],
            "target_profile_id": str(uuid4()),
        },
    )
    assert conflict.status_code == 412
    assert conflict.json()["error"]["code"] == "REVISION_MISMATCH"
    with runtime.database.session_factory() as session:
        assert session.get(m.Pipeline, body["pipeline"]["id"]) is not None


def test_http_cursor_is_bound_to_query_and_preserves_exact_page(logged_in):
    client, headers, runtime, (project, _, user) = logged_in
    for _ in range(2):
        assert (
            client.post(
                "/api/v1/pipelines",
                headers=headers,
                json={
                    "project_id": str(project),
                    "name": "cursor-test",
                    "code": "cursor_" + uuid4().hex,
                },
            ).status_code
            == 201
        )
    path = f"/api/v1/projects/{project}/pipelines"
    first = client.get(path, params={"limit": 1, "query": "cursor-test"})
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["has_more"] and len(page["items"]) == 1
    second = client.get(
        path, params={"limit": 1, "query": "cursor-test", "cursor": page["next_cursor"]}
    )
    assert second.status_code == 200, second.text
    assert len(second.json()["items"]) == 1
    assert second.json()["items"][0]["id"] != page["items"][0]["id"]
    mismatch = client.get(
        path, params={"limit": 1, "query": "different", "cursor": page["next_cursor"]}
    )
    assert mismatch.status_code == 400 and mismatch.json()["error"]["code"] == "INVALID_CURSOR"


def test_missing_database_has_safe_health():
    app = create_app(Settings(environment="test", database_url=""))
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["data"]["status"] == "not_ready"


def test_secret_metadata_read_has_etag_and_never_exposes_locator(logged_in):
    client, headers, runtime, (project, hidden, user) = logged_in
    with runtime.database.session_factory.begin() as session:
        owned = m.SecretReference(
            project_id=project,
            provider="vault",
            public_alias="test-reference",
            provider_locator="private-locator-do-not-expose",
            secret_version="1",
        )
        foreign = m.SecretReference(
            project_id=hidden,
            provider="vault",
            public_alias="hidden-reference",
            provider_locator="hidden-private-locator",
            secret_version="1",
        )
        session.add_all([owned, foreign])
        session.flush()
        owned_id, foreign_id = owned.id, foreign.id
    response = client.get(f"/api/v1/secret-references/{owned_id}")
    assert response.status_code == 200
    assert response.headers["etag"]
    assert response.json()["data"]["revision"] == 1
    assert (
        "provider_locator" not in response.text
        and "private-locator-do-not-expose" not in response.text
    )
    assert client.get(f"/api/v1/secret-references/{foreign_id}").status_code == 404
