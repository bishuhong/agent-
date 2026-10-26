"""Start/stop only the portable test cluster created in this repository."""

import argparse
import json
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status"])
    action = parser.parse_args().action
    runtime = Path(__file__).resolve().parents[1] / ".test-runtime"
    config = json.loads((runtime / "postgresql-test.json").read_text(encoding="utf-8"))
    data = Path(config["data"]).resolve()
    binary = Path(config["bin"]).resolve()
    if (
        data != (runtime / "pgdata-v2").resolve()
        or binary != (runtime / "postgresql/pgsql/bin").resolve()
    ):
        raise RuntimeError("Refusing to control a cluster outside the known isolated test paths")
    port = int(config["port"])
    if not 1024 <= port <= 65535:
        raise RuntimeError("Unexpected test cluster port")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    args = [str(binary / "pg_ctl.exe"), "-D", str(data)]
    status = subprocess.run(
        [*args, "status"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        timeout=10,
    ).returncode
    if action == "status":
        print("isolated test cluster running" if status == 0 else "isolated test cluster stopped")
        return
    if (action == "start" and status == 0) or (action == "stop" and status == 3):
        print("Isolated test cluster already in requested state")
        return
    if status not in {0, 3}:
        raise RuntimeError("Test cluster status could not be verified")
    options = (
        ["-l", str(runtime / "postgresql.log"), "-o", f"-h 127.0.0.1 -p {port}"]
        if action == "start"
        else ["-m", "fast"]
    )
    with (runtime / "postgresql-control.log").open("w", encoding="utf-8") as log:
        subprocess.run(
            [*args, *options, "-w", action],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            timeout=45,
        )
    print(f"Isolated test cluster {action} completed")


if __name__ == "__main__":
    main()
