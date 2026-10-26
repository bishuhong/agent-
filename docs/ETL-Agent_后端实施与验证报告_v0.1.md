# ETL-Agent 后端实施与验证报告

日期：2026-10-23。目录：`D:\python\ETL_Agent`。

## 1. 交付结论

本轮已实际编写后端代码、数据库迁移、接口和测试，并补充应用与 Worker 入口、预置账号、评测集导入、API 导出和运行说明。没有编写前端业务代码，没有迁移现有业务数据库。

**当前是控制面及受管执行协调的阶段性交付，不能标记“完整生产系统验收完成”。** 真实数据搬运、质量分流、原子替换、独立外部审计锚和安全进化实际放量仍有接入阻塞。测试适配器没有接入默认应用，也没有把测试结果冒充真实 SeaTunnel/Doris 的验收证据。

## 2. 依据与已确认变更

依据 `ETL-Agent_PRD_v0.1.md`、`ETL-Agent_概要设计总纲_v0.1.md`、`ETL-Agent_核心数据对象与API契约_v0.1.md`、`ETL-Agent_全模块功能Spec_v0.1.md`，并核对页面原型和前端 Spec。

| 决定 | 实现 |
|---|---|
| Project 为隔离单元 | 不新增 Tenant；项目范围查询、复合外键与每次操作鉴权 |
| 本地账号密码、预置账号、服务端会话 | 无公开注册；scrypt；Cookie、CSRF、登录限速和退出撤销 |
| Worker 消费单次 Capability | Commit 只创建授权与 Outbox；Consumption 在 Worker 短事务写入 |
| 补充两个用途 | `isolated_trial`、`run_l2_benchmark` 与执行、回滚用途分别校验 |
| 必要依赖与隔离迁移已获批准 | `pyproject.toml`、`uv.lock`；PostgreSQL 随机测试 schema |
| CSRF / ETag / G 路由补齐已获批准 | 稳定会话 CSRF、服务器 ETag、查询与既有动作路径 |
| I.3 规范化与固定向量已获批准 | 根清单版本为整数 1；五种制品固定顺序；结构制品保存规范 UTF-8 文本，HOCON 原文不 trim；固定 SHA256 测试 |

准备单冻结精确影子资源、保留资源与目标代次。相同准备单不能通过更换幂等键反复创建写入同一影子资源的运行；新运行需要新准备单。该规则是对冻结资源和唯一提交语义的一致性补齐，不增加业务按钮。

## 3. 需求追踪与实际覆盖

“有实现”表示已具备对应后端代码，不等于真实外部系统已经验收。

| REQ / SPEC | 实现范围 | 验收边界 |
|---|---|---|
| 001—003 身份、项目隔离、职责分离 | `identity_access`、安全 HTTP 边界、数据库约束 | HTTP / PostgreSQL 已验证；不含企业 OIDC |
| 004 Secret | SecretReference、Vault 端口、轮转与版本绑定 | 适配器测试；真实 Vault 未联调 |
| 005—006 连接与只读探查 | 连接维护、异步测试、ProfileTask、PostgreSQL 只读探查 | 真实业务数据源及其他 connector 阻塞 |
| 007 文件 | CSV / Excel / JSON / Parquet 上传与解析、安全 Schema / 预览 | 文件测试已覆盖；未知分级不默认低风险 |
| 008—015 流水线与生成 | Pipeline、候选、对话、澄清、LangGraph 恢复、有限修复、门禁、冻结与差异 | PostgreSQL 检查点实际运行；模型 I/O 受控，真实 Provider 未联调 |
| 016—018 准备与审批 | 冻结事实、分级规则、P0 拒绝、双槽、自然人分离、当前角色复核 | 实际事务测试；生产分级策略必须配置 |
| 019—021 提交、能力、投递 | 原子 Run / Command / Authorization / Outbox / Audit；Ed25519；Worker 唯一消费；短租约恢复 | PostgreSQL 与受控代理端口已测；真实 Redis/Linux Worker 未联调 |
| 022—023 执行与试运行 | 受管执行协议、状态回写、静态试算、隔离用途权限 | 实际 SeaTunnel 提交、Secret 注入、影子渲染仍需适配；默认阻塞 |
| 024 调度 | 一次性 / 五段 Cron / IANA 时区、重复时刻去重、不重叠；到期只建新准备和待审记录 | 领域与事务已验证；自动重新探查并替换过期冻结 Profile 未实现 |
| 025—028 质量、发布、预算 | 规则编译 / 参考求值、报告、预算观察、受管停止意图、发布子能力、目标 CAS、24 小时保留 | 真实数据流质量算子及引擎指标映射未接入；参考求值不替代真实引擎 |
| 029—031 运行、取消、暂停恢复 | 列表、详情、平台安全日志、受管命令及查询确认 | 日志当前来自控制面审计，不冒充真实引擎日志；暂停恢复需验证引擎能力 |
| 032 回滚清理 | 新回滚准备与双槽；发布前冻结资源清理协调；发布后目标代次及保留期校验、恢复与对账 | 发布后恢复已用受控目标端验证；真实目标证据与清理适配器尚未联调 |
| 033 诊断与重跑 | 安全错误与审计证据诊断、新准备和新运行；保留原运行 | 当前为可证实错误码解释，不宣称模型已完成真实根因分析 |
| 034 审计 | 追加链、项目序列、摘要重算、检查点签名、文件锚原语 | 外部独立可信锚及周期固化未接入；无检查点时 valid 仅表示已存链条内部一致 |
| 035—036 总览与健康 | 项目统计、空分母不造成功率、真实 DB / Redis 检查、未验证依赖降级 | 公共健康仅总体状态；具体延迟 SLA 未确认 |
| 037—038 评测 | 版本化目录、不可覆盖导入、L0 / L1、L2 受管关联与汇总、可复算指标 | L2 真实运行、真实模型生成样本、批次总预算待外部能力验证 |
| 039 安全进化 | 候选持久对象、证据约束、基线指标、限界授权和撤销 | 候选生成及实际影子 / 小流量路由端口未接入；不宣称已晋级或放量 |
| 040 完整演示 | 后端启动、配置模板、脚本、迁移、API 契约与测试说明 | 完整真实端到端业务演示未完成；前端不在本次任务范围 |

## 4. 主要文件及职责

| 文件或目录 | 本轮交付 |
|---|---|
| `src/etl_agent/main.py`、`bootstrap.py`、`config.py` | 应用入口、模块装配和配置校验 |
| `src/etl_agent/api/` | 认证、资产、生成、审批、执行、调度、评测、进化、查询；严格类型及安全错误 |
| `src/etl_agent/shared/` | 上下文、规范字节、签名、CSRF、游标、ETag、安全异常 |
| `src/etl_agent/infrastructure/persistence/` | 48 表及 PostgreSQL 会话、访问边界、响应映射 |
| `src/etl_agent/modules/` | 需求表对应的领域规则、服务、跨模块协调与发布 |
| `src/etl_agent/adapters/` | 文件、身份、Secret、只读探查、模型、持久图适配器 |
| `src/etl_agent/workers/tasks.py` | 持久任务分发、至少一次 Outbox、Worker、调度与评测执行 |
| `migrations/versions/0001_initial.py` | 初始表、索引、复合 FK 与不变量触发器 |
| `migrations/versions/0002_langgraph_checkpoints.py` | 固定版本 PostgresSaver 检查点表与迁移 |
| `migrations/versions/0003_agent_request_context.py` | 生成任务精确恢复输入上下文 |
| `migrations/versions/0004_password_hash_format.py` | 密码验证串必须是 scrypt 格式，不能误按制品 SHA256 限制 |
| `scripts/` | 预置账号、开发密钥、评测集导入、契约导出及仅限本项目的测试 PostgreSQL 管理 |
| `tests/` | 单元、契约、HTTP、真实 PostgreSQL 与受控外部端口验证 |
| `.env.example`、`pyproject.toml`、`uv.lock` | 配置模板、依赖声明和实际安装锁文件 |
| `docs/backend-openapi.json` | 从实际应用导出的 78 个路径；既有 21 个方法路径保留 |

## 5. 验证记录

最终全量回归已实际执行，结果如下：

| 检查 | 实际结果 |
|---|---|
| 全量 pytest | **279 passed**，63.71 秒；无失败、无跳过 |
| 单元与契约测试 | 246 项，包含规范化固定向量、权限、文件解析、质量、预算、调度和评测规则 |
| PostgreSQL / HTTP 集成测试 | 33 项；真实 PostgreSQL 事务与 LangGraph；外部模型、代理与引擎按各测试说明使用受控端口 |
| Ruff | **All checks passed** |
| Mypy | **Success: no issues found in 49 source files** |
| OpenAPI 导出 | **78 个路径**，实际应用生成 `docs/backend-openapi.json` |

没有将 202 受理响应当成外部作业成功，没有以删除测试或放宽既有业务断言掩盖失败。调度单元测试补充了新增项目锁所需的测试替身输入，原有成功、重复、重叠和缺少端口断言保留。

实际执行方式：

```powershell
$env:TEST_DATABASE_URL = Get-Content -LiteralPath '.test-runtime/test-database-url' -Raw
.\.venv\Scripts\python.exe -m pytest -q --tb=short
.\.venv\Scripts\python.exe -m ruff check src tests scripts --no-cache
.\.venv\Scripts\python.exe -m mypy src/etl_agent
.\.venv\Scripts\python.exe scripts/export_openapi.py
```

测试数据库是便携 PostgreSQL 17.11 新建隔离实例，各集成测试进一步创建随机 schema。没有使用 SQLite 代替事务测试，没有修改现有业务数据库。

实际观察的关键场景包括：

- 会话 CSRF 稳定、退出后旧 Cookie 无效、未允许 Origin 和错误 CSRF 拒绝、登录失败计数持久化。
- 项目隔离、外键约束、并发唯一消费、事务中断不留半条消费事实。
- 候选生成与澄清恢复使用真实 LangGraph / PostgreSQL；冻结五类制品，保存文本与实际摘要一致。
- 未取得双槽审批不创建 Run 或 Outbox；同键提交复用已受理运行；连接变化使指纹失效。
- 引擎提交结果未知只对账、不盲目重提；重复 Worker 不覆盖其他认领；代理失败保留待发记录。
- 真实数据库中的质量报告、预算检查、独立发布能力与目标代次递增；新审批回滚和重复恢复命令安全返回。外部引擎与目标仅使用明确受控端口。
- 调度只创建新的待审批准备单；评测集版本拒绝覆盖；L0 对冻结版本执行静态门禁。
- URL `limit` 只在 HTTP 边界转换成严格整数；游标不能换查询重用；ETag 冲突返回 412。

## 6. 尚未解决的风险与限制

1. **外部接入**：需要实际 SeaTunnel / 目标数据库版本、资源、受管 Secret 注入和质量流协议，才能完成真实提交、分流、原子发布及停止 / 恢复验收。只设置 URL 或能力名称不会自动得到适配器。
2. **长时间调度**：冻结 Profile 有有效期。过期会拒绝新 Prepare，目前没有自动重探查并替换冻结 Profile 引用的流程；不能把每天重试旧准备称为调度完成。
3. **批次 L2**：逐例资源隔离和批次总预算必须由实际受管执行端保证并验证；未满足时拒绝多例 L2，不用每例预算替代批次预算。
4. **审计可信性**：本地签名 / 文件锚不等于已经部署独立不可篡改存储；未完成外部锚和周期固化前，不宣称能证明所有历史从未被截断。
5. **安全进化**：授权和撤销可记录，实际候选提案、影子和小流量执行尚未接入；样本量与效果门槛仍须业务基准确认。
6. **并发与性能**：项目行锁用于审计与关键授权串行一致性；尚未做生产规模压测。没有确认高可用、吞吐或实时延迟 SLA。
7. **外部副作用一致性**：目标未知结果保留为 reconciling；必须按持久命令标记核验，禁止盲重提。Vault 写入与控制 DB 之间没有分布式事务，真实部署还需对孤立 Secret 版本进行运维核对。
8. **完整业务演示**：尚无真实源数据、生产分级策略、真实 Provider / Redis / SeaTunnel / 目标端链路，未执行完整成功、质量拒绝、预算停止、取消与发布竞态的真实端到端演示。

运行和配置方式见 `ETL-Agent_后端运行与联调说明_v0.1.md`。上述限制分别说明阻塞范围，不覆盖已完成的控制面代码和数据库测试，也不能据此把未完成的真实集成标为通过。
