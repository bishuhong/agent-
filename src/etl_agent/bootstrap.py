"""Composition root. Constructing the application never creates database tables."""

from typing import Any

from etl_agent.config import Settings
from etl_agent.infrastructure.persistence.database import Database
from etl_agent.modules.assets.service import AssetsService
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.evaluation.service import BenchmarkService
from etl_agent.modules.execution.service import ExecutionService, ManagedExecutor
from etl_agent.modules.generation.service import GenerationService
from etl_agent.modules.governance.service import GovernanceService
from etl_agent.modules.harness.service import CommandAuthorizer, CommitCoordinator, ReplayGuard
from etl_agent.modules.identity_access.service import LocalAuthService, ProjectService
from etl_agent.modules.pipelines.service import PipelineService
from etl_agent.modules.publication.service import PublicationService
from etl_agent.modules.quality.service import QualityService
from etl_agent.modules.reporting.service import HealthService, ReportingService
from etl_agent.modules.scheduling.service import ScheduleService
from etl_agent.modules.supervision.service import SupervisionService
from etl_agent.shared.context import utcnow
from etl_agent.shared.errors import unavailable
from etl_agent.shared.security import CapabilitySigner, CursorCodec


class UnconfiguredEngine:
    """Fail closed until credential injection, shadow rendering and engine version are verified."""

    def submit(self, spec: dict[str, Any]) -> Any:
        raise unavailable("SeaTunnel受管提交、Secret注入及影子资源适配器")

    def query(self, job_id: str) -> Any:
        raise unavailable("SeaTunnel版本及作业查询适配器")

    def cancel(self, job_id: str) -> None:
        raise unavailable("SeaTunnel取消适配器")

    def pause(self, job_id: str) -> None:
        raise unavailable("SeaTunnel暂停适配器")

    def resume(self, job_id: str) -> None:
        raise unavailable("SeaTunnel恢复适配器")


class BackendRuntime:
    @property
    def required_database(self) -> Database:
        if self.database is None:
            raise unavailable("PostgreSQL")
        return self.database

    @property
    def required_auth(self) -> LocalAuthService:
        if self.auth is None:
            raise unavailable("本地身份服务")
        return self.auth

    def __init__(
        self,
        settings: Settings,
        *,
        database: Database | None = None,
        engine: Any = None,
        target: Any = None,
    ) -> None:
        self.settings = settings
        url = settings.database_url.get_secret_value()
        self.database = database or (Database(url) if url else None)
        self.ledger = EvidenceLedger()
        self.signer = CapabilitySigner(
            settings.capability_private_key.get_secret_value(),
            settings.capability_public_keys,
            settings.capability_key_id,
        )
        self.cursors = CursorCodec(settings.cursor_key.get_secret_value())
        self.auth = LocalAuthService(self.database, settings) if self.database else None
        self.projects = ProjectService(self.ledger, settings)
        self.assets = AssetsService(settings, self.ledger)
        self.pipelines = PipelineService(settings, self.ledger)
        self.generation = GenerationService(settings, self.ledger)
        self.governance = GovernanceService(settings, self.ledger)
        self.authorizer = CommandAuthorizer(settings, self.signer, self.ledger)
        self.commits = CommitCoordinator(settings, self.governance, self.authorizer, self.ledger)
        self.replay = ReplayGuard(self.governance, self.authorizer, self.ledger)
        self.quality = QualityService(settings, self.ledger)
        self.supervision = SupervisionService(settings, self.ledger)
        self.execution = ExecutionService(
            settings, self.ledger, self.quality, self.supervision, self.cursors
        )
        self.publications = PublicationService(self.database, self.authorizer, self.ledger, target)
        self.executor = ManagedExecutor(
            self.database,
            self.replay,
            engine or UnconfiguredEngine(),
            self.execution,
            self.ledger,
            self.publications,
        )
        self.benchmarks = BenchmarkService(
            settings, self.ledger, l2_submit=self.commits.commit_benchmark
        )
        self.schedules = ScheduleService(settings, self.ledger)
        self.reporting = ReportingService(settings)
        self.health = HealthService(settings)

    def health_result(self, request: Any) -> dict[str, Any]:
        if self.database is None:
            return {"status": "not_ready", "checked_at": utcnow(), "components": []}
        with self.database.session_factory() as session:
            return self.health.check(session, detailed=False)
