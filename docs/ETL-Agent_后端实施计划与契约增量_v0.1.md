# ETL-Agent 后端实施计划与契约增量

日期：2026-10-23。基线：PRD、概要设计、数据对象与API、全模块功能Spec、前端Spec及原型。

## 本轮用户确认

- 实现总Spec全部后端功能；没有就绪的外部接入明确阻塞，不伪造搬运/发布成功。
- 允许必要后端依赖与建表迁移；只在新建隔离测试库执行迁移，不修改现有数据库。
- 允许补齐CSRF刷新恢复、实体ETag、G类读取/既有动作REST路径、权限映射；既有18个业务及3个认证方法路径保留。
- 采用总Spec I.3版本化摘要规则，并以固定字节/摘要向量验证。

## 文件与实施顺序

| 文件范围 | 目的 | 影响 |
|---|---|---|
| pyproject.toml、uv.lock | Python3.12依赖与开发工具锁定 | 当前依赖为空，新增已获授权 |
| src/etl_agent/shared、config.py | 安全错误、类型、UTC、规范化、部署配置 | 所有模块使用一致基础协议 |
| infrastructure/persistence、migrations、alembic.ini | PostgreSQL模型、项目复合FK、迁移、事务边界 | 不自动create_all，不连接/修改未知现有库 |
| modules/identity_access、assets、pipelines、generation、validation | 登录/权限、接入、生成澄清、门禁与版本 | 无既有业务实现可复用 |
| modules/governance、harness、execution、publication | 准备/审批、原子提交、Worker消费、受管执行和恢复 | Commit不消费；不确定外部状态先对账 |
| modules/quality、supervision、scheduling、audit、reporting、evaluation | 质量/预算、到期准备、账本、统计、评测进化 | 真实外部能力须通过受管适配器 |
| api、application、workers、adapters | HTTP、跨模块事务、Celery/LangGraph/模型/引擎/Secret端口 | 无前端实现；未配置能力失败关闭 |
| scripts、tests | 受控初始化与单元/接口/隔离PG一致性测试 | 不运行破坏性迁移、不以SQLite代替PG验证 |

## 必要契约增量

1. AUTH-02增加csrf_token；服务端从会话原始Token和受管session_key派生稳定CSRF，数据库只存摘要。GET不会轮换使其他标签页失效，退出/会话撤销后全部不可用。
2. 实体读取和写响应提供服务器生成ETag；If-Match只接受该实体当前ETag。客户端不从revision猜造；变更冲突412。
3. G类路由按后续OpenAPI完整登记；只覆盖已有业务查询和动作，不加注册、资金、导出、任意SQL或管理员角色。
4. ActionCode增加create_connection、manage_schedule、revoke_evolution；现有五角色不变。项目成员管理只由部署project_managers显式授予，不默认操作员拥有管理权。
5. 规范化schema_version=1；JSON键排序/UTF-8/保留数组顺序/精确值文本、禁止浮点与非有限值；HOCON按冻结原文UTF-8；五类制品固定顺序形成根摘要。签名不包含signature自身。

## 测试边界

实际测试结果另写报告。PostgreSQL并发/复合FK/短事务、唯一消费、Outbox与审计必须在真实隔离PostgreSQL验证；尚未运行不得写通过。模型端点、Redis/Linux Worker、SeaTunnel/Doris、Vault和外部审计锚缺配置时，只可验证端口失败关闭及确定性领域逻辑，不能当真实链路通过。
