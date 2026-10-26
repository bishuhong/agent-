# ETL-Agent 后端运行与联调说明

更新日期：2026-10-23。工作目录：`D:\python\ETL_Agent`。

本说明依据现有 PRD、概要设计、数据对象、总 Spec 和本轮已批准的契约增量。当前交付的是后端；完整真实数据搬运、原子替换和前端业务演示仍受外部接入条件限制。

## 1. 文件入口

| 路径 | 职责 |
|---|---|
| `src/etl_agent/main.py` | FastAPI、Origin、统一安全错误、请求追踪、Cookie HTTP 边界 |
| `src/etl_agent/bootstrap.py` | 模块装配；缺少配置不自动建库、不冒充依赖就绪 |
| `src/etl_agent/api/routes/control.py` | 既有 18 个业务接口、3 个认证接口及批准补齐的查询和动作 |
| `src/etl_agent/api/schemas/contracts.py` | 前后端共用的请求、响应、枚举、空值、日期及精确数值契约 |
| `src/etl_agent/infrastructure/persistence/` | 48 张 PostgreSQL 表、项目隔离及 DTO 白名单映射 |
| `migrations/versions/` | 初始结构、LangGraph 检查点、生成恢复上下文、密码格式修复 |
| `src/etl_agent/workers/tasks.py` | Celery、Outbox、持久化任务扫描、Worker 消费、调度与评测 |
| `src/etl_agent/modules/` | 身份、资产、流水线、生成、门禁、审批、执行、发布、质量、监督、审计、评测等 |
| `src/etl_agent/adapters/` | 本地密码、Vault、PostgreSQL 只读探查、四种文件解析、模型及 LangGraph |
| `tests/unit/`、`tests/contract/`、`tests/integration/` | 领域与契约测试、真实隔离 PostgreSQL 和 HTTP 测试 |
| `docs/backend-openapi.json` | 从实际注册应用导出的 API 契约，不是手写的接口清单 |

设计稿中的文件职责合并到上述共享文件，未照搬每个 Spec 的重复模板目录。该后端交付阶段未生成前端业务代码；后续前端实现、模型配置和本轮验证见《ETL-Agent_前端实施与启动说明_v0.1.md》。

## 2. 安装与启动

需要 Python 3.12、PostgreSQL、Redis；Celery Worker 的正式运行环境采用 Linux。Windows 下已执行同步服务和隔离数据库测试，尚未完成 Windows Celery 多进程生产运行验证。

```powershell
Set-Location 'D:\python\ETL_Agent'
uv sync --group dev --link-mode copy
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

复制配置只在 `.env` 尚不存在时执行，已有部署配置请自行编辑保留。填写数据库、Redis、浏览器 Origin，以及彼此独立的会话、游标、Ed25519 密钥。开发密钥可以通过下列脚本生成到已忽略的本地文件，再配置到 `.env` 或进程环境；脚本拒绝覆盖已有密钥且不回显密钥内容。

```powershell
.\.venv\Scripts\python.exe scripts/create_development_keys.py
```

生产模式必须使用 HTTPS Origin、Secure Cookie 和至少 32 字节的会话/游标密钥。生产密钥由部署环境管理，不使用开发密钥文件。

本轮只在隔离测试 schema 执行过迁移，没有迁移现有业务库。部署前应由部署负责人选择明确的新建数据库并审查迁移，然后执行：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn etl_agent.main:app --host 127.0.0.1 --port 8000
```

未配置数据库时，应用仍可加载契约，`/health` 返回 `not_ready` 和 503；配置了数据库也不代表 Redis、引擎和 SecretProvider 已就绪。当前健康公开接口只提供总体状态。

## 3. 预置账号

不提供公开注册和密码重置接口。使用运维 CLI 预置账号，密码交互输入且不回显。脚本不会自动迁移或覆盖既有密码。

```powershell
.\.venv\Scripts\python.exe scripts/provision_local.py --username maker01 --display-name '设计人员' --person-ref '<该自然人的稳定UUID>' --project-code demo --project-name '演示项目' --role maker
```

`--person-ref` 必须是实际 UUID；同一自然人的不同账号使用同一个值。再分别预置操作员、数据审批人和安全审批人；两类审批人的自然人必须不同，并分别指定对应角色和 `--approval-slot data` / `--approval-slot security`。不存在隐式管理员角色。项目成员管理权通过 `ETL_PROJECT_MANAGERS` 明确配置。

## 4. 认证与并发契约

- 登录成功设置 HttpOnly、SameSite=Lax 的服务端会话 Cookie；实际部署按 HTTPS 使用 Secure。
- 所有写请求都必须带允许的 `Origin`；登录也校验 Origin。登录之外的写请求还必须有 Cookie 和 `X-CSRF-Token`。
- `GET /api/v1/auth/me` 返回本会话稳定 CSRF；刷新页面不使其他标签页的值失效。
- 实体 GET 返回服务器生成的 `ETag`。要求条件写的接口将该值原样放入 `If-Match`；不能从 revision 猜造。
- Commit、取消、暂停、恢复和回滚等幂等动作按契约携带 `Idempotency-Key`；相同键不同请求返回 409。
- 计数、代次、毫秒时长与精确小数使用契约中的十进制字符串；ID 是 UUID 字符串；日期为含时区的 ISO 时间，响应统一 UTC `Z`。
- 错误统一为 `{error:{code,message,field_errors,retryable},request_id}`，不回传密码、SQL 参数或堆栈。

API 路径、请求必填字段和响应字段以 `backend-openapi.json` 为准。修改后重新导出：

```powershell
.\.venv\Scripts\python.exe scripts/export_openapi.py
```

## 5. Worker 与调度

配置同一套数据库、安全密钥和 Redis 后，在 Linux 的独立进程运行：

```text
celery -A etl_agent.workers.tasks:celery_app worker --loglevel=INFO
celery -A etl_agent.workers.tasks:celery_app beat --loglevel=INFO
```

Beat 当前每 10 秒扫描，属于可工作的技术采样配置，不是已经承诺的实时 SLA。Outbox 支持至少一次投递；Worker 以数据库命令认领、签名和唯一 Consumption 为权威。数据库里未消费的命令也会被扫描，首次消费超过五分钟即失败，不自动续签。

模型生成、连接测试和探查从持久化任务恢复。调度使用冻结版本自身的监督预算及目标连接环境，每次只创建新 Prepare 和待审批记录，不复制旧审批、不直接执行。过期 Profile、失效角色和缺少分级策略会阻止准备；当前没有自动重探查并替换冻结 Profile 引用的流程。

## 6. 外部接入边界

| 能力 | 当前情况 |
|---|---|
| 本地认证、PostgreSQL 控制事务、LangGraph 持久化 | 有实现和实际数据库测试 |
| CSV、Excel、JSON、Parquet | 有文件解析和边界测试 |
| PostgreSQL 只读探查 | 有受管适配器；需 SecretProvider、目标允许名单及明确启用 connector；未在用户真实数据源联调 |
| 其他数据库、S3/MinIO、REST 数据连接器 | 需对应真实适配器；不会使用通用 SQL 假冒支持 |
| DeepSeek、Qwen、OpenAI-compatible 模型 | 有 HTTP 适配和安全校验；真实服务需要受管端点、模型版本及密钥配置 |
| Vault SecretProvider | 有适配器；没有真实 Vault 配置和联调证据 |
| SeaTunnel 数据面 | 提交、临时 Secret 注入、影子资源重写、质量算子与引擎指标映射仍需按实际版本接入；默认执行适配器明确阻塞，设置一个 URL 不会自动开启 |
| 目标原子发布与恢复 | 有收据授权、目标代次、保留窗口、未知结果对账协调；真实 Doris/其他目标适配器未接入 |
| 暂停、恢复、L2 多用例隔离和批次总预算 | 必须有经过验证的引擎能力；不靠配置字符串作为实际能力证明 |
| 审计锚 | 有签名和文件锚原语；独立外部可信锚及自动周期固化未完成部署接入 |
| 安全进化 | 有指标、证据约束和限界授权；候选生成与实际流量路由端口未接入，授权不代表已放量 |

这些阻塞会返回明确错误或失败任务，不产生假的数据搬运、发布、恢复或放量成功记录。只在测试中注入受控引擎/目标端，默认应用不启用这些测试适配器。

## 7. 评测与测试

版本化评测集可由已有主体经 CLI 导入；拒绝覆盖同名同版本，校验项目关联并记录审计。输入结构见 `modules/evaluation/catalog.py`，不新增未经约定的公开上传接口。

```powershell
.\.venv\Scripts\python.exe scripts/import_benchmark.py '<评测集JSON路径>' --user-id '<已有用户UUID>'
```

L0 可对指定冻结版本执行真实静态门禁；L1 仅声明为确定性故障仿真；L2 必须经过 Prepare、双槽审批和真实受管运行。没有真实模型生成、引擎运行或样本基准时，不将桩数据当作评测成绩。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit tests/contract -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts --no-cache
.\.venv\Scripts\python.exe -m mypy src/etl_agent
```

完整集成测试需要显式设置 `TEST_DATABASE_URL`，只在随机隔离 schema 迁移和清理；未设置时会明确跳过，不以 SQLite 替代。最终执行结果和仍未完成的验收见后端实施报告。

本轮测试用便携 PostgreSQL 集群位于已忽略的 `.test-runtime/pgdata-v2`，只绑定回环地址。可以显式启动、运行测试，再停止；管理脚本核验目录，不会操作其他 PostgreSQL 实例。

```powershell
.\.venv\Scripts\python.exe scripts/manage_test_postgres.py start
$env:TEST_DATABASE_URL = Get-Content -LiteralPath '.test-runtime/test-database-url' -Raw
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/manage_test_postgres.py stop
```

测试连接串和密码留在已忽略的本地运行目录，未写入文档或聊天。不要把这个测试实例当作生产数据库。
