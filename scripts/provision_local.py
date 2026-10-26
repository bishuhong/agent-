"""Explicit, out-of-band account provisioning. Never creates/migrates database tables."""

import argparse
import re
from getpass import getpass
from uuid import UUID, uuid4

from sqlalchemy import select

from etl_agent.adapters.identity.local_password import PasswordHasher
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.database import Database
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.identity_access.domain import ROLE_ACTIONS


def main() -> None:
    parser = argparse.ArgumentParser(description="预置本地账号和明确的项目授权；不自动迁移")
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument(
        "--person-ref", type=UUID, required=True, help="同一自然人的所有账号必须使用同一个稳定UUID"
    )
    parser.add_argument("--project-code", required=True)
    parser.add_argument("--project-name", help="只有显式提供名称时才允许创建项目")
    parser.add_argument("--role", action="append", choices=sorted(ROLE_ACTIONS), required=True)
    parser.add_argument(
        "--approval-slot", action="append", choices=["data", "security"], default=[]
    )
    args = parser.parse_args()
    settings = Settings()
    if not settings.database_url.get_secret_value():
        parser.error("必须配置 ETL_DATABASE_URL；不会创建默认数据库")
    name = args.username.lower()
    if re.fullmatch(r"[a-z0-9_-]{3,64}", name) is None:
        parser.error("账号名应为3—64位字母、数字、下划线或连字符")
    for slot in args.approval_slot:
        if {"data": "data_checker", "security": "security_checker"}[slot] not in args.role:
            parser.error("职责槽必须同时具有对应角色")
    password = getpass("初始密码（12—128字符，不回显）: ")
    if password != getpass("再次输入密码: "):
        parser.error("两次密码不一致")
    hasher = PasswordHasher(
        settings.scrypt_n, settings.scrypt_r, settings.scrypt_p, settings.password_parallelism
    )
    encoded = hasher.hash(password)
    database = Database(settings)
    try:
        with database.session_factory.begin() as session:
            if session.scalar(select(m.User.id).where(m.User.username == name)):
                parser.error("账号已存在；不会覆盖密码或身份关联")
            project = session.scalar(select(m.Project).where(m.Project.code == args.project_code))
            if project is None:
                if not args.project_name:
                    parser.error("项目不存在；创建时必须明确提供 --project-name")
                project = m.Project(code=args.project_code, name=args.project_name)
                session.add(project)
                session.flush([project])
            uid = uuid4()
            user = m.User(
                id=uid,
                subject_ref=f"local:{uid}",
                person_ref=args.person_ref,
                username=name,
                display_name=args.display_name,
                status="enabled",
            )
            session.add(user)
            session.flush([user])
            session.add(m.LocalCredential(user_id=uid, password_hash=encoded))
            for role in set(args.role):
                session.add(m.ProjectMembership(project_id=project.id, user_id=uid, role=role))
            session.flush()
            for slot in set(args.approval_slot):
                session.add(m.ProjectRoleGrant(project_id=project.id, user_id=uid, role_slot=slot))
            session.flush()
            EvidenceLedger().append(
                session,
                project.id,
                None,
                "local_account_provisioned",
                "user",
                uid,
                {"roles": sorted(set(args.role)), "approval_slots": args.approval_slot},
            )
            project_id = project.id
        print(f"预置完成。user_id={uid} project_id={project_id}")
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
