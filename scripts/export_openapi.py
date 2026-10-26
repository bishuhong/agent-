"""Export the registered contract, without database access or secret values."""

import json
from pathlib import Path

from etl_agent.config import Settings
from etl_agent.main import create_app


def main() -> None:
    app = create_app(Settings(_env_file=None, database_url="", capability_private_key=""))
    output = Path(__file__).resolve().parents[1] / "docs" / "backend-openapi.json"
    output.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已导出 {len(app.openapi()['paths'])} 个路径：{output}")


if __name__ == "__main__":
    main()
