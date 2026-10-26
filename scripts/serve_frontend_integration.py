"""Disposable real HTTP backend for browser tests. Only the owned test DB is allowed."""

import os
from pathlib import Path
from uuid import uuid4

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from etl_agent.bootstrap import BackendRuntime
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.database import Database
from etl_agent.main import create_app


def main():
    root = Path(__file__).resolve().parents[1]
    raw = os.environ.get("TEST_DATABASE_URL", "")
    if not raw:
        raise RuntimeError("TEST_DATABASE_URL must be explicitly supplied")
    url = make_url(raw.strip()).set(drivername="postgresql+psycopg")
    if url.host != "127.0.0.1" or url.database != "etl_agent_test":
        raise RuntimeError("Refusing to migrate anything except the owned loopback test database")
    schema = "frontend_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, connect_args={"options": f"-c search_path={schema} -c timezone=UTC"}
    )
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
            _env_file=None,
            environment="test",
            session_key="frontend-test-session-key-" + uuid4().hex,
            cursor_key="frontend-test-cursor-key-" + uuid4().hex,
            cookie_secure=False,
            allowed_origins=["http://127.0.0.1:5175"],
            storage_root=root / ".test-runtime" / "frontend-assets" / schema,
        )
        runtime = BackendRuntime(settings, database=database)
        with database.session_factory.begin() as session:
            user = m.User(
                username="frontend_test",
                subject_ref="local:" + uuid4().hex,
                person_ref=uuid4(),
                display_name="浏览器联调账户",
                status="enabled",
            )
            project = m.Project(name="真实接口隔离验证项目", code=uuid4().hex)
            session.add_all([user, project])
            session.flush()
            session.add(
                m.LocalCredential(
                    user_id=user.id,
                    password_hash=runtime.auth.hasher.hash("Browser-test-password-123!"),
                )
            )
            for role in ("maker", "operator"):
                session.add(m.ProjectMembership(project_id=project.id, user_id=user.id, role=role))
        print("Disposable frontend integration backend ready on 127.0.0.1:8001", flush=True)
        uvicorn.run(create_app(runtime=runtime), host="127.0.0.1", port=8001, log_level="warning")
    finally:
        engine.dispose()
        assert (
            schema.startswith("frontend_test_") and schema.removeprefix("frontend_test_").isalnum()
        )
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


if __name__ == "__main__":
    main()
