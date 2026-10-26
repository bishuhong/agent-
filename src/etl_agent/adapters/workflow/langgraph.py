"""Real LangGraph interrupt/resume backed by explicitly migrated PostgreSQL tables."""

from __future__ import annotations

from typing import Any, TypedDict

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

from etl_agent.config import Settings
from etl_agent.modules.generation.ports import ModelContext, ModelProvider
from etl_agent.shared.errors import ApplicationError, unavailable


class WorkflowState(TypedDict, total=False):
    context: dict[str, Any]
    output: dict[str, Any]
    attempt: int


class LangGraphWorkflow:
    def __init__(self, settings: Settings, provider: ModelProvider):
        self.settings = settings
        self.provider = provider

    @property
    def conn_string(self) -> str:
        value = self.settings.database_url.get_secret_value()
        if not value:
            raise unavailable("PostgreSQL工作流检查点")
        url = make_url(value)
        values: dict[str, Any] = {
            "host": url.host,
            "port": url.port,
            "user": url.username,
            "password": url.password,
            "dbname": url.database,
            **dict(url.query),
        }
        return make_conninfo(**{key: item for key, item in values.items() if item is not None})

    def ensure_ready(self) -> None:
        try:
            with psycopg.connect(self.conn_string, connect_timeout=5, autocommit=True) as conn:
                names = conn.execute(
                    "SELECT to_regclass('checkpoints'),to_regclass('checkpoint_blobs'),to_regclass('checkpoint_writes')"
                ).fetchone()
                if not names or any(name is None for name in names):
                    raise unavailable("已迁移PostgreSQL工作流检查点表")
        except psycopg.Error as exc:
            raise unavailable("PostgreSQL工作流检查点") from exc

    def _graph(self, saver: PostgresSaver):
        def generate(state: WorkflowState) -> dict[str, Any]:
            context = ModelContext.model_validate(state["context"])
            output = self.provider.generate(context.provider_binding, context)
            return {"output": output.model_dump(mode="json")}

        def clarify(state: WorkflowState) -> dict[str, Any]:
            supplied = interrupt({"questions": state["output"]["questions"]})
            if not isinstance(supplied, dict) or any(
                not isinstance(value, str) for value in supplied.values()
            ):
                raise ApplicationError("VALIDATION_FAILED", "澄清恢复输入无效", 422)
            context = {
                **state["context"],
                "answers": {**state["context"].get("answers", {}), **supplied},
            }
            return {"context": context}

        graph = StateGraph(WorkflowState)
        graph.add_node("generate", generate)
        graph.add_node("clarify", clarify)
        graph.add_edge(START, "generate")
        graph.add_conditional_edges(
            "generate",
            lambda state: "clarify" if state["output"]["kind"] == "clarification" else END,
        )
        graph.add_edge("clarify", "generate")
        return graph.compile(checkpointer=saver)

    def run(
        self, thread_id: str, context: ModelContext, *, answers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self.ensure_ready()
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}
        try:
            with psycopg.connect(
                self.conn_string,
                autocommit=True,
                prepare_threshold=0,
                row_factory=dict_row,
                connect_timeout=5,
            ) as conn:
                graph = self._graph(PostgresSaver(conn))
                snapshot = graph.get_state(config)
                if snapshot.next:
                    paused = any(task.interrupts for task in snapshot.tasks)
                    if paused and answers is None:
                        return dict(snapshot.values["output"])
                    result = graph.invoke(Command(resume=answers) if paused else None, config)
                elif snapshot.values and snapshot.values.get("attempt") == context.repair_count:
                    return dict(snapshot.values["output"])
                else:
                    result = graph.invoke(
                        {
                            "context": context.model_dump(mode="json"),
                            "attempt": context.repair_count,
                        },
                        config,
                    )
                return dict(result["output"])
        except psycopg.Error as exc:
            raise unavailable("PostgreSQL工作流检查点") from exc
