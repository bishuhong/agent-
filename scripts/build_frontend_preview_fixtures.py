"""Generate explicitly simulated UI fixtures, validated against actual API DTOs."""

import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from etl_agent.api.schemas import contracts as c
from etl_agent.modules.identity_access.domain import ROLE_ACTIONS
from etl_agent.shared.context import utcnow


def main():
    now = utcnow()

    def uid(n):
        return str(UUID(int=(4 << 76) | (2 << 62) | n))

    project, user, owner = uid(1), uid(2), uid(3)

    def check(name, data):
        return getattr(c, name).model_validate(data).model_dump(mode="json")

    def record(n, mutable=False):
        return {
            "id": uid(n),
            "project_id": project,
            "created_at": now.isoformat(),
            **({"updated_at": now.isoformat(), "revision": 1} if mutable else {}),
        }

    permissions = [
        {
            "action": a,
            "allowed": a in ROLE_ACTIONS["maker"] | ROLE_ACTIONS["operator"],
            "reason_code": None
            if a in ROLE_ACTIONS["maker"] | ROLE_ACTIONS["operator"]
            else "FORBIDDEN",
            "reason_message": None
            if a in ROLE_ACTIONS["maker"] | ROLE_ACTIONS["operator"]
            else "预览身份没有此职责",
        }
        for a in sorted(set().union(*ROLE_ACTIONS.values()) | {"manage_project_members"})
    ]
    fixtures = {}
    fixtures["project"] = check(
        "ProjectResponse",
        {
            "id": project,
            "name": "数据集成工作空间",
            "code": "preview",
            "description": "ETL-Agent 交互预览项目",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "revision": 1,
            "available_actions": permissions,
        },
    )
    person = check(
        "UserResponse",
        {
            "id": user,
            "username": "preview_user",
            "display_name": "预览用户",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "revision": 1,
            "email": None,
            "status": "enabled",
        },
    )
    fixtures["memberships"] = [
        check("ProjectMembershipResponse", {**record(5, True), "user_id": user, "role": "maker"}),
        check(
            "ProjectMembershipResponse", {**record(6, True), "user_id": user, "role": "operator"}
        ),
    ]
    fixtures["identity"] = check(
        "CurrentIdentityResponse",
        {
            "user": person,
            "project_memberships": fixtures["memberships"],
            "role_grants": [],
            "available_actions": [],
            "expires_at": (now + timedelta(hours=8)).isoformat(),
            "csrf_token": "explicit-preview-no-real-session",
        },
    )
    connections = []
    for i, (name, kind) in enumerate(
        [
            ("订单业务库", "postgresql"),
            ("用户中心", "mysql"),
            ("分析数据仓库", "doris"),
            ("客户服务库", "postgresql"),
            ("产品行为库", "clickhouse"),
            ("业务归档库", "mysql"),
        ]
    ):
        connections.append(
            check(
                "ConnectionResponse",
                {
                    **record(10 + i, True),
                    "name": name,
                    "connector_type": kind,
                    "endpoint": {
                        "kind": "database",
                        "host": "preview.example.test",
                        "port": 5432,
                        "database": "preview",
                        "schema": None,
                    },
                    "environment": "test",
                    "secret_ref_id": None,
                    "available_actions": permissions,
                },
            )
        )
    fixtures["connections"] = connections
    fields = [
        check(
            "SchemaField",
            {
                "field_name": name,
                "ordinal": i,
                "native_type": None,
                "logical_type": "string",
                "nullable": False,
                "is_primary_key": i == 0,
                "precision": None,
                "scale": None,
                "sensitivity": "public",
            },
        )
        for i, name in enumerate(["order_id", "customer_id", "amount", "created_at"])
    ]
    profiles = [
        check(
            "MetadataProfileResponse",
            {
                **record(20 + i),
                "connection_id": connections[j]["id"],
                "file_asset_id": None,
                "resource_name": name,
                "fields": fields,
                "statistics": {"row_count": "28640", "is_approximate": True},
                "masked_samples": [],
                "profile_digest": str(i + 1) * 64,
                "expires_at": (now + timedelta(minutes=15)).isoformat(),
            },
        )
        for i, (name, j) in enumerate([("orders", 0), ("orders_analytics", 2), ("customers", 3)])
    ]
    fixtures["profiles"] = profiles
    scope = {
        "connection_id": connections[2]["id"],
        "file_asset_id": None,
        "resource_name": "orders_errors",
        "operations": ["execute_etl"],
        "environment": "test",
    }
    quality = {
        "schema_version": 1,
        "rules": [
            {
                "rule_id": "not_null_order",
                "field_name": "order_id",
                "kind": "not_null",
                "stage": "validate",
                "error_code": "ORDER_ID_EMPTY",
            }
        ],
        "error_resource": scope,
        "max_rejection_ratio": "0.01",
        "allow_empty_input": False,
    }
    budget = {
        "schema_version": 1,
        "max_input_records": "1000000",
        "max_output_bytes": "1073741824",
        "max_duration_ms": "3600000",
        "max_amplification_ratio": "10",
        "max_rejection_ratio": "0.05",
        "on_exceed": "kill",
    }
    plan = check(
        "EtlPlan",
        {
            "source_profile_ids": [profiles[0]["id"]],
            "target_profile_id": profiles[1]["id"],
            "field_mappings": [
                {
                    "source_fields": [f["field_name"]],
                    "target_field": f["field_name"],
                    "expression": f"identity({f['field_name']})",
                    "target_type": "string",
                }
                for f in fields
            ],
            "nodes": [
                {"node_id": "orders_source", "kind": "source", "resource_ref": "orders"},
                {"node_id": "field_mapping", "kind": "transform", "resource_ref": None},
                {"node_id": "analytics_sink", "kind": "sink", "resource_ref": "orders_analytics"},
            ],
            "edges": [
                {"from_node_id": "orders_source", "to_node_id": "field_mapping"},
                {"from_node_id": "field_mapping", "to_node_id": "analytics_sink"},
            ],
            "quality_contract": quality,
            "supervision_contract": budget,
        },
    )
    fixtures["pipelines"], fixtures["versions"], fixtures["designs"] = [], [], {}
    for i, (name, code, status) in enumerate(
        [
            ("订单明细增量同步", "orders_incremental", "ready"),
            ("用户画像数据归集", "customer_profiles", "draft"),
            ("产品行为宽表构建", "product_events", "ready"),
            ("销售日报数据准备", "sales_daily", "draft"),
        ]
    ):
        pipeline = check(
            "PipelineResponse",
            {
                **record(30 + i, True),
                "name": name,
                "code": code,
                "description": None,
                "status": status,
                "available_actions": permissions,
            },
        )
        version = check(
            "PipelineVersionResponse",
            {
                **record(40 + i),
                "pipeline_id": pipeline["id"],
                "version_number": 1,
                "is_immutable": status == "ready",
                "artifact_digest": "a" * 64 if status == "ready" else None,
                "revision": 1,
            },
        )
        report = check(
            "ValidationReportResponse",
            {
                **record(50 + i),
                "version_id": version["id"],
                "content_digest": "a" * 64,
                "rule_version": "preview-v1",
                "passed": True,
                "issues": [],
            },
        )
        fixtures["pipelines"].append(pipeline)
        fixtures["versions"].append(version)
        fixtures["designs"][version["id"]] = check(
            "DesignResponse",
            {
                "version": version,
                "etl_plan": plan,
                "hocon_text": 'env { parallelism = 1 }\nsource { Managed { resource_ref = "orders" } }\ntransform { FieldMapper { order_id = "order_id" } }\nsink { Managed { resource_ref = "orders_analytics" } }',
                "validation_report": report,
                "available_actions": [
                    (
                        {
                            **a,
                            "allowed": False,
                            "reason_code": "STATE_CONFLICT",
                            "reason_message": "已冻结版本只读",
                        }
                        if version["is_immutable"] and a["action"] in {"generate", "freeze_version"}
                        else a
                    )
                    for a in permissions
                ],
            },
        )
    version = fixtures["versions"][0]
    prep = check(
        "PreparationResponseBody",
        {
            **record(60),
            "version_id": version["id"],
            "purpose": "execute_etl",
            "execution_run_id": None,
            "maker_id": owner,
            "input_fingerprint": "b" * 64,
            "input_bindings": [],
            "resource_scopes": [scope],
            "impact_summary": "读取冻结源，仅写批准的影子与错误资源；发布需独立授权。",
            "data_classification": "public",
            "environment": "test",
            "risk_level": "P3",
            "required_slots": ["data", "security"],
            "quality_contract": quality,
            "supervision_contract": budget,
            "rollback_plan": {
                "target": scope,
                "shadow_resource": "orders_analytics__preview_shadow",
                "retained_resource": "orders_analytics__preview_retained",
                "expected_generation": "0",
                "retention_ms": "86400000",
                "steps": ["核验目标代次", "按冻结范围恢复资源"],
            },
            "policy_version": "1",
            "expires_at": (now + timedelta(minutes=30)).isoformat(),
            "evaluation_binding": None,
        },
    )
    approvals = [
        check(
            "ApprovalResponse",
            {
                **record(61 + i, True),
                "preparation_id": prep["id"],
                "version_id": version["id"],
                "required_slot": slot,
                "status": "pending",
                "decision": None,
                "approver_id": None,
                "decided_at": None,
            },
        )
        for i, slot in enumerate(["data", "security"])
    ]
    fixtures["preparation"] = check(
        "PreparationResponse",
        {
            "preparation": prep,
            "approvals": approvals,
            "validity": "valid",
            "available_actions": [
                {
                    **a,
                    "allowed": False,
                    "reason_code": "STATE_CONFLICT",
                    "reason_message": "预览准备单尚未取得双槽审批",
                }
                for a in permissions
                if a["action"] in {"approve_data", "approve_security", "commit"}
            ],
        },
    )
    fixtures["preparationSummaries"] = [
        check(
            "PreparationSummary",
            {
                **record(60),
                "version_id": version["id"],
                "maker_id": owner,
                "expires_at": prep["expires_at"],
                "validity": "valid",
                "approval_status": "pending_approval",
            },
        )
    ]
    fixtures["runs"], fixtures["runSummaries"] = {}, []
    for i, status in enumerate(["succeeded", "running", "failed"]):
        run = check(
            "ExecutionRunResponseBody",
            {
                **record(70 + i, True),
                "version_id": version["id"],
                "preparation_id": prep["id"],
                "capability_token_digest": "c" * 64,
                "execution_status": status,
                "engine_job_id": "preview-job-" + str(i),
                "input_records": "28640",
                "output_records": "28612",
                "error_records": "28",
                "bytes_processed": "104857600",
                "started_at": (now - timedelta(minutes=20)).isoformat(),
                "finished_at": now.isoformat() if status != "running" else None,
                "original_run_id": None,
                "metrics": {
                    "input_records": "28640",
                    "output_records": "28612",
                    "error_records": "28",
                    "bytes_processed": "104857600",
                    "output_bytes": "104857600",
                    "duration_ms": "46000",
                    "observed_at": now.isoformat(),
                },
            },
        )
        fixtures["runs"][run["id"]] = check(
            "ExecutionRunResponse",
            {
                "run": run,
                "publication": None,
                "latest_rollback": None,
                "quality_report": None,
                "supervision": None,
                "available_actions": [
                    {
                        **a,
                        "allowed": False,
                        "reason_code": "DEPENDENCY_UNAVAILABLE",
                        "reason_message": "交互预览不执行真实运行操作",
                    }
                    for a in permissions
                    if a["action"] in {"cancel", "pause", "resume", "rollback", "rerun"}
                ],
            },
        )
        fixtures["runSummaries"].append(
            check(
                "ExecutionRunSummary",
                {
                    **record(70 + i),
                    "version_id": version["id"],
                    "execution_status": status,
                    "publication_status": "not_published",
                    "rollback_status": None,
                    "started_at": run["started_at"],
                    "finished_at": run["finished_at"],
                },
            )
        )
    fixtures["overview"] = check(
        "ProjectOverviewResponse",
        {
            "project_id": project,
            "connection_count": "6",
            "pipeline_count": "4",
            "pending_approval_count": "2",
            "period_start": (now - timedelta(days=7)).isoformat(),
            "period_end": now.isoformat(),
            "run_success_rate": "0.5",
            "resource_distribution": [
                {"connector_type": kind, "resource_count": count}
                for kind, count in [
                    ("postgresql", "2"),
                    ("mysql", "2"),
                    ("doris", "1"),
                    ("clickhouse", "1"),
                ]
            ],
        },
    )
    fixtures["audit"] = [
        check(
            "AuditEventResponse",
            {
                **record(80 + i),
                "actor_id": user,
                "actor_kind": "user",
                "event_type": event,
                "resource_type": "pipeline_version",
                "resource_id": version["id"],
                "payload_digest": "d" * 64,
                "prev_event_hash": "e" * 64 if i else None,
                "event_hash": "e" * 64,
                "sequence_no": str(i + 1),
            },
        )
        for i, event in enumerate(["pipeline_created", "version_frozen", "prepared"])
    ]
    fixtures["health"] = check(
        "HealthResult", {"status": "degraded", "checked_at": now.isoformat(), "components": []}
    )
    fixtures["datasets"] = [
        check(
            "BenchmarkDatasetResponse",
            {**record(90), "name": "基础集成门禁样例", "version": "v1", "content_digest": "f" * 64},
        )
    ]
    fixtures["agent"] = check(
        "AgentRunResponse",
        {
            **record(95, True),
            "version_id": version["id"],
            "conversation_id": uid(96),
            "thread_id": "preview-agent",
            "prompt": "预览设计需求",
            "provider_binding": {
                "provider": "deepseek",
                "model": "deepseek-v4.1-flash",
                "prompt_version": "v1",
                "policy_version": "1",
            },
            "status": "succeeded",
            "step_count": 3,
            "repair_count": 0,
            "error_message": None,
        },
    )
    output = Path(__file__).resolve().parents[1] / "frontend/src/mocks/fixtures.json"
    output.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Simulated frontend fixtures generated and validated against backend DTOs.")


if __name__ == "__main__":
    main()
