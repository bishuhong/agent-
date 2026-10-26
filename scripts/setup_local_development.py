"""Explicitly provision the user-approved local development DB and account.

Never targets the test cluster or a pre-existing external database. Credentials
stay in ignored local files; rerunning preserves passwords and model settings.
"""

import json
import runpy
import secrets
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotenv import dotenv_values, set_key
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from etl_agent.adapters.identity.local_password import PasswordHasher
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.shared.canonical import encode_url


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != root:
        raise RuntimeError("Run from the project root")
    runtime = root / ".runtime"
    runtime.mkdir(exist_ok=True)
    data = (runtime / "pgdata-development").resolve()
    binary = (root / ".test-runtime/postgresql/pgsql/bin").resolve()
    config_path = runtime / "postgresql-development.json"
    credentials_path = runtime / "development-credentials.json"
    login_path = runtime / "local-login.json"
    expected = {"data": str(data), "bin": str(binary), "port": 53553}
    env_path = root / ".env"
    values = dotenv_values(env_path)
    existing_url = values.get("ETL_DATABASE_URL")
    if existing_url:
        parsed = make_url(existing_url)
        if (parsed.host, parsed.port, parsed.database, parsed.username) != (
            "127.0.0.1",
            53553,
            "etl_agent_development",
            "etl_app",
        ):
            raise RuntimeError("Existing database configuration is not this development DB")
    if config_path.exists():
        if json.loads(config_path.read_text(encoding="utf-8")) != expected:
            raise RuntimeError("Unexpected development cluster identity")
    else:
        if data.exists() or credentials_path.exists() or login_path.exists():
            raise RuntimeError("Unrecognized existing files; refusing initialization")
        if not (binary / "initdb.exe").is_file():
            raise RuntimeError("The previously installed PostgreSQL binaries are missing")
        credentials_path.write_text(
            json.dumps(
                {
                    "admin_password": secrets.token_urlsafe(40),
                    "application_password": secrets.token_urlsafe(40),
                }
            ),
            encoding="utf-8",
        )
        login_path.write_text(
            json.dumps(
                {
                    "username": "1111",
                    "password": "ETL-" + secrets.token_urlsafe(16),
                    "person_ref": str(uuid4()),
                    "project_code": "local_dev",
                }
            ),
            encoding="utf-8",
        )
        config_path.write_text(json.dumps(expected), encoding="utf-8")
    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    login = json.loads(login_path.read_text(encoding="utf-8"))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if not (data / "PG_VERSION").exists():
        if data.exists() and any(data.iterdir()):
            raise RuntimeError("Nonempty uninitialized directory; refusing to overwrite")
        password_file = runtime / "postgresql-development-init-password"
        try:
            with password_file.open("x", encoding="utf-8") as handle:
                handle.write(credentials["admin_password"] + "\n")
            with (runtime / "postgresql-development-init.log").open("w") as log:
                subprocess.run(
                    [
                        str(binary / "initdb.exe"),
                        "-D",
                        str(data),
                        "-U",
                        "etl_dev_admin",
                        "--encoding=UTF8",
                        "--locale=C",
                        "--auth-host=scram-sha-256",
                        "--auth-local=scram-sha-256",
                        "--pwfile=" + str(password_file),
                    ],
                    check=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                    timeout=60,
                )
        finally:
            password_file.unlink(missing_ok=True)
    subprocess.run(
        [sys.executable, str(root / "scripts/manage_development_postgres.py"), "start"],
        check=True,
        creationflags=flags,
    )
    with psycopg.connect(
        host="127.0.0.1",
        port=53553,
        dbname="postgres",
        user="etl_dev_admin",
        password=credentials["admin_password"],
        autocommit=True,
    ) as connection:
        actual = connection.execute("SHOW data_directory").fetchone()[0]
        if Path(actual).resolve() != data:
            raise RuntimeError("Connected server is not the owned development cluster")
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname='etl_app'").fetchone():
            connection.execute(
                sql.SQL(
                    "CREATE ROLE etl_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(sql.Literal(credentials["application_password"]))
            )
        if not connection.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", ("etl_agent_development",)
        ).fetchone():
            connection.execute("CREATE DATABASE etl_agent_development OWNER etl_app")
    app_url = URL.create(
        "postgresql+psycopg",
        username="etl_app",
        password=credentials["application_password"],
        host="127.0.0.1",
        port=53553,
        database="etl_agent_development",
    )
    engine = create_engine(app_url)
    try:
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()
    updates = {
        "ETL_DATABASE_URL": app_url.render_as_string(hide_password=False),
        "ETL_ALLOWED_ORIGINS": json.dumps(["http://127.0.0.1:5173", "http://localhost:5173"]),
        "ETL_COOKIE_SECURE": "false",
    }
    for name in ("ETL_SESSION_KEY", "ETL_CURSOR_KEY"):
        if not values.get(name):
            updates[name] = secrets.token_urlsafe(48)
    if not values.get("ETL_CAPABILITY_PRIVATE_KEY"):
        private = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        updates["ETL_CAPABILITY_PRIVATE_KEY"] = encode_url(private)
        updates["ETL_CAPABILITY_KEY_ID"] = "local-development-v1"
    for name, value in updates.items():
        set_key(env_path, name, value)
    settings = Settings()
    if settings.database_url.get_secret_value() != updates["ETL_DATABASE_URL"]:
        raise RuntimeError("Process database environment overrides the local configuration")
    engine = create_engine(app_url)
    try:
        with Session(engine) as session:
            existing = session.scalar(select(m.User).where(m.User.username == "1111"))
            if existing:
                encoded = session.scalar(
                    select(m.LocalCredential.password_hash).where(
                        m.LocalCredential.user_id == existing.id
                    )
                )
                if not encoded or not PasswordHasher().verify(login["password"], encoded):
                    raise RuntimeError("Existing account password differs; refusing reset")
        if not existing:
            args = [
                "provision_local.py",
                "--username",
                "1111",
                "--display-name",
                "本地开发用户",
                "--person-ref",
                login["person_ref"],
                "--project-code",
                "local_dev",
                "--project-name",
                "本地开发工作空间",
                "--role",
                "maker",
                "--role",
                "operator",
            ]
            with (
                patch.object(sys, "argv", args),
                patch("getpass.getpass", return_value=login["password"]),
            ):
                runpy.run_path(str(root / "scripts/provision_local.py"), run_name="__main__")
    finally:
        engine.dispose()
    print("Local development DB and account ready; model configuration preserved.")
    print("Login credentials saved only to ignored .runtime/local-login.json")


if __name__ == "__main__":
    main()
