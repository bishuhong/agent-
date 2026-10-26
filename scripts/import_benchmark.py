"""Import an explicitly supplied, versioned benchmark through the same catalog rules."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.database import Database
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.evaluation.catalog import DatasetImport, EvaluationCatalog
from etl_agent.modules.identity_access.service import actor_from_user


def main():
    parser = argparse.ArgumentParser(description="导入评测集；同名同版本拒绝覆盖，不自动执行评测")
    parser.add_argument("file", type=Path)
    parser.add_argument("--user-id", type=UUID, required=True)
    args = parser.parse_args()
    payload = DatasetImport.model_validate_json(args.file.read_bytes())
    database = Database(Settings())
    try:
        with database.session_factory.begin() as session:
            user = session.get(m.User, args.user_id)
            if user is None or user.status != "enabled":
                parser.error("必须指定有效的已预置主体")
            result = EvaluationCatalog(EvidenceLedger()).import_dataset(
                session, actor_from_user(session, user), payload
            )
        print(json.dumps(result, ensure_ascii=False))
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
