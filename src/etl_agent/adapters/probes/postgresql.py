"""PostgreSQL-only, read-only metadata probe with DNS-pinned allowlisted endpoints."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

import psycopg
from psycopg import sql

from etl_agent.modules.assets.ports import ProbeProfile
from etl_agent.shared.errors import ApplicationError, invalid, unavailable


def approved_host(host: str, allowed: list[str]) -> list[str]:
    if host.casefold().rstrip(".") not in {item.casefold().rstrip(".") for item in allowed}:
        raise ApplicationError("FORBIDDEN", "目标主机不在受管允许清单中", 403)
    try:
        addresses = sorted(
            {str(item[4][0]) for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
        )
    except OSError as error:
        raise unavailable("目标主机解析") from error
    if not addresses:
        raise unavailable("目标主机解析")
    for address in addresses:
        value = ipaddress.ip_address(address)
        if value.is_link_local or value.is_multicast or value.is_unspecified:
            raise ApplicationError("FORBIDDEN", "目标地址属于禁止的网络范围", 403)
    return addresses


class PostgreSQLProbe:
    def __init__(self, allowed_hosts: list[str], *, require_tls: bool = True) -> None:
        self.allowed_hosts = allowed_hosts
        self.require_tls = require_tls

    def _connect(self, endpoint: dict[str, Any], credentials: dict[str, str]):
        if endpoint.get("kind") != "database":
            raise invalid("PostgreSQL必须使用数据库端点", "endpoint")
        addresses = approved_host(endpoint["host"], self.allowed_hosts)
        if "username" not in credentials or "password" not in credentials:
            raise invalid("PostgreSQL凭据需要username与password", "credentials")
        try:
            return psycopg.connect(
                host=endpoint["host"],
                hostaddr=addresses[0],
                port=endpoint["port"],
                dbname=endpoint["database"],
                user=credentials["username"],
                password=credentials["password"],
                connect_timeout=5,
                sslmode="verify-full" if self.require_tls else "prefer",
                options="-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=3000",
            )
        except Exception as error:
            raise unavailable("PostgreSQL只读连接") from error

    def test(self, endpoint: dict[str, Any], credentials: dict[str, str]) -> None:
        with self._connect(endpoint, credentials) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise unavailable("PostgreSQL只读连接")

    def profile(
        self,
        endpoint: dict[str, Any],
        credentials: dict[str, str],
        resource_names: list[str],
        sample_limit: int,
    ) -> list[ProbeProfile]:
        if not 0 <= sample_limit <= 100:
            raise invalid("样本上限必须为0—100", "sample_limit")
        results = []
        with self._connect(endpoint, credentials) as connection:
            with connection.cursor() as cursor:
                for resource in resource_names:
                    parts = resource.split(".")
                    if len(parts) == 1 and endpoint.get("schema"):
                        schema, table = endpoint["schema"], parts[0]
                    elif len(parts) == 2:
                        schema, table = parts
                    else:
                        raise invalid(
                            "资源必须为schema.table，或明确配置默认Schema", "resource_names"
                        )
                    if not schema or not table:
                        raise invalid("资源定位不能为空", "resource_names")
                    cursor.execute(
                        """SELECT c.column_name,c.ordinal_position,c.data_type,c.udt_name,
                        c.is_nullable,c.numeric_precision,c.numeric_scale,
                        EXISTS(SELECT 1 FROM information_schema.table_constraints t
                           JOIN information_schema.key_column_usage k USING(constraint_catalog,constraint_schema,constraint_name)
                           WHERE t.constraint_type='PRIMARY KEY' AND t.table_schema=c.table_schema
                             AND t.table_name=c.table_name AND k.column_name=c.column_name
                             AND k.table_schema=c.table_schema AND k.table_name=c.table_name)
                        FROM information_schema.columns c WHERE c.table_schema=%s AND c.table_name=%s
                        ORDER BY c.ordinal_position""",
                        (schema, table),
                    )
                    columns = cursor.fetchall()
                    if not columns:
                        raise ApplicationError(
                            "RESOURCE_NOT_FOUND", "受管资源不存在或探查账号不可见", 404
                        )
                    fields = []
                    for name, ordinal, native, udt, nullable, precision, scale, primary in columns:
                        logical = self._logical_type(native, udt)
                        if logical == "decimal" and (
                            precision is None or scale is None or scale < 0
                        ):
                            # Unbounded NUMERIC cannot be silently assigned fake precision.
                            logical = "unknown"
                        fields.append(
                            {
                                "field_name": name,
                                "ordinal": ordinal - 1,
                                "native_type": native,
                                "logical_type": logical,
                                "nullable": nullable == "YES",
                                "is_primary_key": primary,
                                "precision": precision if logical == "decimal" else None,
                                "scale": scale if logical == "decimal" else None,
                                "sensitivity": "unclassified",
                            }
                        )
                    cursor.execute(
                        "SELECT c.reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s",
                        (schema, table),
                    )
                    count = cursor.fetchone()
                    estimate = count[0] if count and count[0] >= 0 else None
                    samples = []
                    if sample_limit:
                        cursor.execute(
                            sql.SQL("SELECT {} FROM {}.{} LIMIT %s").format(
                                sql.SQL(",").join(sql.Identifier(item[0]) for item in columns),
                                sql.Identifier(schema),
                                sql.Identifier(table),
                            ),
                            (sample_limit,),
                        )
                        # No classification policy configured: mask every field, including numeric values.
                        for _ in cursor.fetchall():
                            samples.append(
                                {
                                    "values": {item[0]: "[REDACTED]" for item in columns},
                                    "masked_fields": [item[0] for item in columns],
                                }
                            )
                    results.append(
                        ProbeProfile(
                            resource,
                            fields,
                            {
                                "row_count": str(estimate) if estimate is not None else None,
                                "is_approximate": True,
                            },
                            samples,
                        )
                    )
        return results

    @staticmethod
    def _logical_type(native: str, udt: str) -> str:
        if native in {"smallint", "integer", "bigint"}:
            return "integer"
        if native == "numeric":
            return "decimal"
        if native == "boolean":
            return "boolean"
        if native == "date":
            return "date"
        if native.startswith("timestamp"):
            return "timestamp"
        if native == "bytea":
            return "binary"
        if native == "ARRAY":
            return "array"
        if udt in {"json", "jsonb"}:
            return "object"
        if native in {"text", "character varying", "character", "uuid"}:
            return "string"
        return "unknown"
