"""Control only this project's explicitly provisioned persistent development cluster."""

import argparse
import json
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status"])
    action = parser.parse_args().action
    root = Path(__file__).resolve().parents[1]
    runtime = root / ".runtime"
    config = json.loads((runtime / "postgresql-development.json").read_text(encoding="utf-8"))
    data, binary = Path(config["data"]).resolve(), Path(config["bin"]).resolve()
    if (
        data != (runtime / "pgdata-development").resolve()
        or binary != (root / ".test-runtime/postgresql/pgsql/bin").resolve()
        or config["port"] != 53553
    ):
        raise RuntimeError("Unexpected cluster identity; refusing to control another database")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    args = [str(binary / "pg_ctl.exe"), "-D", str(data)]
    status = subprocess.run(
        [*args, "status"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        timeout=10,
    ).returncode
    if status not in (0, 3):
        raise RuntimeError("Unable to verify development cluster state")
    if action == "status":
        print("Development PostgreSQL running" if status == 0 else "Development PostgreSQL stopped")
        return
    if (action == "start" and status == 0) or (action == "stop" and status == 3):
        print("Development PostgreSQL already in requested state")
        return
    options = (
        ["-l", str(runtime / "postgresql-development.log"), "-o", "-h 127.0.0.1 -p 53553"]
        if action == "start"
        else ["-m", "fast"]
    )
    with (runtime / "postgresql-development-control.log").open("a", encoding="utf-8") as log:
        subprocess.run(
            [*args, *options, "-w", action],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            creationflags=flags,
            timeout=45,
        )
    print(f"Development PostgreSQL {action} completed")


if __name__ == "__main__":
    main()
