"""Create an isolated loopback-only test cluster; never connects to an existing DB.

Uses the Windows binary archive linked by postgresql.org -> EnterpriseDB. This is
an explicit developer test helper, not part of application startup or migration.
"""

import hashlib
import json
import secrets
import socket
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".test-runtime"
URL = "https://get.enterprisedb.com/postgresql/postgresql-17.11-5-windows-x64-binaries.zip"


def run(args: list[str]) -> None:
    # Do not pipe pg_ctl output: the background postgres child can inherit that
    # pipe and keep communicate() waiting after pg_ctl has already exited.
    output_path = RUNTIME / "postgresql-command.log"
    with output_path.open("w", encoding="utf-8") as output:
        result = subprocess.run(
            args,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=90,
        )
    if result.returncode:
        raise RuntimeError(
            f"{Path(args[0]).name} failed: {output_path.read_text(encoding='utf-8', errors='replace')[-3000:]}"
        )


def main() -> None:
    RUNTIME.mkdir(exist_ok=True)
    archive = RUNTIME / "postgresql-binaries.zip"
    binary = RUNTIME / "postgresql"
    if not (binary / "pgsql/bin/initdb.exe").exists():
        if not archive.exists():
            print("Downloading official PostgreSQL test binaries", flush=True)
            temp = archive.with_suffix(".partial")
            with urllib.request.urlopen(URL, timeout=60) as response, temp.open("wb") as output:
                expected = int(response.headers.get("Content-Length", "0"))
                received = 0
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    received += len(chunk)
                if expected and received != expected:
                    raise RuntimeError("incomplete PostgreSQL archive")
            temp.replace(archive)
        archive_hash = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
        (RUNTIME / "postgresql-download.json").write_text(
            json.dumps({"url": URL, "sha256": archive_hash}), encoding="utf-8"
        )
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                if not member.filename.startswith(("pgsql/bin/", "pgsql/lib/", "pgsql/share/")):
                    continue
                target = (binary / member.filename).resolve()
                if not target.is_relative_to(binary.resolve()):
                    raise RuntimeError("unsafe archive path")
                source.extract(member, binary)
        print("PostgreSQL binaries extracted", flush=True)
    data = RUNTIME / "pgdata-v2"
    config = RUNTIME / "postgresql-test.json"
    if config.exists():
        print("Existing isolated cluster configuration retained; no data changed", flush=True)
        return
    if data.exists():
        raise RuntimeError(
            "Existing data directory without ownership config; refusing initialization"
        )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    password = secrets.token_urlsafe(32)
    pwfile = RUNTIME / "init-password"
    pwfile.write_text(password, encoding="utf-8")
    bin_dir = binary / "pgsql/bin"
    try:
        run(
            [
                str(bin_dir / "initdb.exe"),
                "-D",
                str(data),
                "-U",
                "etl_test",
                "--pwfile",
                str(pwfile),
                "--auth=scram-sha-256",
                "--encoding=UTF8",
                "--locale=C",
            ]
        )
    finally:
        pwfile.unlink(missing_ok=True)
    run(
        [
            str(bin_dir / "pg_ctl.exe"),
            "-D",
            str(data),
            "-l",
            str(RUNTIME / "postgresql.log"),
            "-o",
            f"-h 127.0.0.1 -p {port}",
            "-w",
            "start",
        ]
    )
    config.write_text(
        json.dumps(
            {
                "data": str(data),
                "bin": str(bin_dir),
                "port": port,
                "user": "etl_test",
                "password": password,
            }
        ),
        encoding="utf-8",
    )
    import psycopg

    with psycopg.connect(
        host="127.0.0.1",
        port=port,
        user="etl_test",
        password=password,
        dbname="postgres",
        autocommit=True,
    ) as connection:
        connection.execute("CREATE DATABASE etl_agent_test")
    url = f"postgresql+psycopg://etl_test:{password}@127.0.0.1:{port}/etl_agent_test"
    (RUNTIME / "test-database-url").write_text(url, encoding="utf-8")
    print(
        f"Isolated PostgreSQL test cluster ready on loopback port {port}; credentials remain in ignored runtime files",
        flush=True,
    )


if __name__ == "__main__":
    main()
