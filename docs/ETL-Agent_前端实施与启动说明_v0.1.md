# ETL-Agent 前端实施与启动说明

更新日期：2026-10-23。项目：`D:\python\ETL_Agent`。前端：`frontend/`。

## 1. 交付结果与依据

本轮实际创建了 Vue 3、TypeScript、Vite 前端，覆盖前端 Spec 的 P-01 至 P-16 逻辑页面。使用中文业务标签、浅色背景、森林绿主色、统一表格/卡片/表单，桌面侧栏与手机抽屉导航。质量和诊断合并为运行详情标签，三类治理页面共用一个页面组件。

依据同目录的 PRD、概要设计、低保真页面规划、核心数据对象与 API 契约、全模块功能 Spec、前端功能 Spec，以及本轮实际后端导出的 `backend-openapi.json`。历史设计中的未定路由在本轮落实为下面的前端地址；它们不替代或修改既有 HTTP API 路径。

实现的是可接真实 API 的前端及独立的开发预览。真实登录、创建流水线、CSV 上传解析已完成浏览器联调。未将真实 ETL 数据面、所有外部连接器、原子发布和生产部署标记为完成。

## 2. 页面及需求追溯

以下路径除登录、项目入口、健康外，均以 `/projects/:projectId` 为前缀。

| 原型 | 页面 / 前端路径 | 对应需求 | 实现内容 |
|---|---|---|---|
| P-01 | `/login` | REQ-001 | 本地预置账户登录、HttpOnly 会话恢复、退出；无注册及找回密码 |
| P-02 | `/projects` | REQ-002/003 | 可见项目、成员面板、按后端授权授予既有用户角色或职责槽 |
| P-03 | `/overview` | REQ-035 | 服务端统计、资源分布、最近流水线、既有页面入口 |
| P-04 | `/assets` | REQ-004—007 | 连接维护、Secret 轮换、连接测试、异步 Profile、四种文件资产 |
| P-05 | `/pipelines` | REQ-008/015 | 名称搜索、游标分页、创建、进入最近版本 |
| P-06 | `/pipelines/:pipelineId/studio/:versionId` | REQ-008—014/023 | Profile 选择、自然语言生成、澄清恢复、只读方案/映射/DAG/HOCON/质量契约、静态试算 |
| P-07 | `/versions/:versionId` | REQ-015—017/024 | 校验审查、冻结、版本比较、新候选、准备申请、创建及条件更新调度 |
| P-08 | `/preparations` | REQ-016/018/019 | 准备单及审批入口、游标分页 |
| P-09 | `/preparations/:id` | REQ-003/016—021 | 冻结输入、双槽审批、按用途提交或返回 L2/回滚/重跑入口 |
| P-10 | `/runs` | REQ-022/029 | 运行列表，执行/发布/回滚分别展示 |
| P-11 | `/runs/:id` | REQ-020—022/026/028—033 | 受管运行、取消/暂停/恢复、重新准备回滚/重跑、平台安全日志和操作历史 |
| P-12 | 运行详情的质量/诊断标签 | REQ-025—027/033 | 服务端质量报告、诊断证据及未知状态 |
| P-13 | `/governance/audit` | REQ-034 | 安全事件和账本验证，区分内部一致与独立锚覆盖 |
| P-14 | `/governance/benchmarks` | REQ-037/038 | 既有用例集、L0/L1/L2 受理及报告，L2 绑定已批准准备单 |
| P-15 | `/governance/evolution` | REQ-039 | 候选审查、有界授权及撤销；无自动或全量上线按钮 |
| P-16 | `/health-status`，不带项目前缀 | REQ-036 | 读取 `/health` 安全总体结果，503 仍展示真实数据 |

截图为明确标识的模拟预览：[桌面](frontend-desktop.png)、[手机](frontend-mobile.png)、[手机 Studio](frontend-studio-mobile.png)。[真实 API 联调截图](frontend-real-integration.png)来自隔离测试项目。

## 3. 代码组织及责任

| 位置 | 职责 |
|---|---|
| `frontend/src/App.vue`、`router.ts`、`style.css` | 应用外壳、导航、会话路由守卫、响应式视觉规范 |
| `frontend/src/pages/` | 页面组合及各页面用户操作 |
| `frontend/src/components/` | 模态框、状态反馈、资源表格、空状态、准备表单、安全文本详情等 |
| `frontend/src/api/generated.ts` | 从实际 OpenAPI 自动生成的 API 类型，不手写平行 DTO |
| `frontend/src/api/client.ts` | Cookie、CSRF、ETag、幂等键、单次提交、错误及结果未知处理 |
| `frontend/src/api/validate.ts` | 关键响应结构、权限布尔值及游标一致性运行时检查 |
| `frontend/src/api/format.ts` | ID/计数/精确小数展示、UTC 与 IANA 时区转换 |
| `frontend/src/composables/` | 最小会话共享状态、请求竞态保护、分页、变更及离开提醒 |
| `frontend/src/mocks/` | 仅显式开发预览可用的独立传输；不访问真实接口 |
| `frontend/tests/` | 前端边界单测及模拟/真实浏览器测试 |
| `scripts/build_frontend_preview_fixtures.py` | 用后端 Pydantic DTO 校验后生成模拟样本 |
| `scripts/serve_frontend_integration.py` | 仅在指定回环测试库新建随机 schema 的临时真实后端 |

没有引入全局状态库、外部字体、图标服务或组件库。API 字段保持 snake_case；页面展示值不回写覆盖原始数据。配置及 LLM 密钥不属于前端状态。

## 4. 模型配置

用户指定的 `https://shanshuiapi.com/v1`、`deepseek-v4.1-flash` 及 API key 已写入项目根目录被忽略的 `.env`，由 `ETL_MODEL_PROVIDERS` 提供给后端受管 `deepseek` 适配器。原有 `.env` 请保留，不要用示例覆盖。

本轮实际发送了一次最小聊天请求，返回 HTTP 200 且含 choices。这仅证明该端点、凭据与模型名能够进行基础调用；没有把它等同于结构化 ETL 方案生成、LangGraph 恢复或真实数据搬运已完成联调。

前端仅显示公开模型名，生成请求提交 `provider: deepseek`；服务端解析模型版本。浏览器不直接请求模型服务，不包含 API key。`VITE_MODEL_NAME` 是公开显示信息，不是模型供应商目录或服务端权限配置；没有增加模型管理页。

已扫描前端源码、构建产物和 docs 共 77 个文件：未发现用户提供的实际密钥。生产构建不包含模拟 transport/fixtures 分块；强制生产 mock 构建会明确失败。

## 5. 启动页面

### 5.1 只预览页面与交互

```powershell
Set-Location 'D:\python\ETL_Agent\frontend'
npm ci --cache 'D:\python\ETL_Agent\.runtime\npm-cache'
npm run dev -- --mode preview --port 5174
```

打开 `http://127.0.0.1:5174/`。本轮结束时保留此开发预览服务供查看。

顶部始终显示“全部为模拟数据”。预览身份是设计/操作角色，没有审批或成员管理权；界面会保留对应禁用状态。预览支持候选创建、方案生成等演示；尚未模拟的写操作明确返回不可用，不伪造成功。模拟修改仅保留在当前页面运行周期，刷新会重置，不能当作持久化或真实审批记录。

### 5.2 连接真实后端

```powershell
Set-Location 'D:\python\ETL_Agent\frontend'
if (-not (Test-Path -LiteralPath '.env.local')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env.local'
}
npm run dev
```

打开 `http://127.0.0.1:5173/`。默认 real 模式，`/api` 和精确 `/health` 代理到 `127.0.0.1:8000`，保留浏览器 Origin。真实模式失败不会回退模拟数据。

后端启动、预置账号、数据库、Redis、Worker、允许地址及密钥配置遵循《ETL-Agent_后端运行与联调说明_v0.1.md》。本轮只补入了 LLM 及本地前端 Origin 配置，没有擅自连接或迁移业务数据库。真实业务账号需按原有 CLI 预置，不能使用浏览器测试账户作为正式账户。

本机 HTTP 开发使用 `ETL_COOKIE_SECURE=false`；生产必须恢复 HTTPS、Secure Cookie、同源反向代理及明确 Origin。生产静态站点应将前端路由回退到 `index.html`，将 `/api/*` 与 `/health` 转发后端。没有执行公网部署。

## 6. 关键交互和安全边界

- 按后端 `available_actions` 启用动作，不从角色名称推导最终权限。对象缺少允许动作时不启用写入。服务端与 Worker 继续独立校验。
- GET me 恢复本会话稳定 CSRF；请求使用 HttpOnly Cookie。无 localStorage 令牌、密码或密钥。
- 条件更新使用服务端返回的原始 ETag。审批前重新读取并比较展示修订，避免将未审查的新事实直接提交。
- 执行及运行控制按既有契约携带幂等键。重复点击不并发提交；网络中断后的写操作进入结果待核实，不换新键自动重试。
- 同一会话运行周期内保留结果未知的请求范围锁；刷新或重新登录后仍须核对原对象，不能据按钮恢复可点推断此前未提交。跨刷新持久化不确定操作账本尚未实现。
- 读取请求使用 AbortController 和递增请求序号，旧响应不覆盖新请求。切换项目使用会话代次检查，后台标签页暂停轮询 HTTP。
- 运行状态、发布、质量和审批分别展示。null 指标显示未知，不冒充零；前端不计算权威评分或允许发布结论。
- 大计数与精确小数保持十进制字符串；展示比例不经过大整数浮点转换。调度及授权使用明确 IANA 时区；夏令时重复/不存在时刻拒绝提交。
- 列表使用后端游标，不伪造总页数。日志和操作历史可分段读取。查询失败显示错误，不生成空列表充当成功。
- 连接 Secret 明文仅存在当前表单内存，提交/关闭清除；轮换明确提示共享连接与旧授权的影响。不展示 SecretProvider locator 或旧密码。
- 服务端文本、HOCON 和报告使用文本渲染，没有 v-html。错误提供请求编号及可定位字段，不显示堆栈和私密请求值。
- 手机导航保留全部入口；表格折叠为卡片，表单及弹窗可滚动。使用原生表单标签、键盘焦点、模态框 Escape/焦点恢复、aria 状态反馈。桌面低高度侧栏可滚动，退出按钮保持可达。

## 7. 后端配套增量

既有 21 个业务/认证接口保持原路径。没有修改数据库结构或新增迁移。

| 变更 | 原因及约束 |
|---|---|
| 项目列表/详情投影完整受支持动作 | 原先仅投影 view/manage，前端无法正确启用既有业务操作；仍由原有角色规则及显式项目管理配置判定 |
| `GET /api/v1/secret-references/{id}` | 既有轮换请求必须提供 expected_revision；只读返回既有安全 DTO 和 ETag，不返回 locator 或 Secret 值，跨项目为 404 |
| `docs/backend-openapi.json` | 从真实注册路由重新导出，共 79 条路径；前端 generated.ts 同步生成 |
| `tests/conftest.py` | 测试禁止自动加载个人 .env 并隔离 ETL 环境配置，避免真实模型凭据影响确定性测试 |

## 8. 实际验证结果

| 检查 | 实际结果 |
|---|---|
| `npm run build` | 通过，包含 vue-tsc 严格类型检查及 Vite 生产构建 |
| `npm run test` | 18 项通过，覆盖精确数值、时区边界、Cookie/CSRF/ETag、未知结果和契约结构 |
| `npm run test:e2e` | 3 项通过，桌面全页面、390px 手机导航/弹窗/冻结状态、候选生成；模拟模式零真实 API 请求 |
| `npm run test:integration` | 1 项通过，真实登录、刷新恢复、创建和重新查询流水线、CSV 上传生成 Profile、退出撤销 |
| 后端 `pytest -q --tb=short --basetemp <本次新建目录>` | 281 项通过，真实 PostgreSQL 随机隔离 schema；没有跳过替代为 SQLite |
| `ruff check src tests scripts` | 通过 |
| `mypy src` | 49 个源码文件通过 |
| 生产模拟模式保护 | `VITE_API_MODE=mock npm run build` 按预期失败 |
| 凭据及产物检查 | 前端源码、dist、docs 未包含实际 key；生产无模拟传输分块 |

首轮全量后端测试出现 2 失败、4 环境错误：个人 .env 导致模型配置污染，以及系统临时目录不可创建。已增加明确的测试配置隔离，并在项目 .test-runtime 下使用新建的独立临时目录重跑；未删除测试或放宽断言。最终为上述 281 项通过。

真实浏览器联调使用 `scripts/serve_frontend_integration.py` 新建的专用测试 schema、测试账户和 CSV；本轮已停止临时后端并清理该 schema，测试 PostgreSQL 已停止。开发模拟预览无需数据库，继续保留。

### 复验命令

```powershell
Set-Location 'D:\python\ETL_Agent'
.\.venv\Scripts\python.exe scripts/export_openapi.py
Set-Location 'D:\python\ETL_Agent\frontend'
npm run generate:api
npm run build
npm run test
npm run test:e2e
```

真实联调需先在另一终端显式启动本项目测试库及临时服务（保持运行）：

```powershell
Set-Location 'D:\python\ETL_Agent'
.\.venv\Scripts\python.exe scripts/manage_test_postgres.py start
$env:TEST_DATABASE_URL = (Get-Content -LiteralPath '.test-runtime/test-database-url' -Raw).Trim()
.\.venv\Scripts\python.exe scripts/serve_frontend_integration.py
```

然后在 frontend 执行 `npm run test:integration`。临时服务限定回环地址的 `etl_agent_test` 数据库，使用随机 schema；不要将该脚本用于业务库。

## 9. 已知限制与剩余验收

1. 模型基础请求成功不等于完整生成链路成功。真实结构化模型输出、LangGraph/Worker、真实数据源联调需在完整环境进一步验收。
2. SeaTunnel、Vault、目标原子发布/恢复、其他连接器、外部审计锚及安全进化流量路由仍遵循后端报告的阻塞。前端只展示真实拒绝或未就绪，不提供假完成状态。
3. 当前公开健康接口只返回总体状态，没有编造组件检查详情。成员目录无可授予用户查询接口，使用负责人提供的已有 UUID；本版成员表尚未单独展示全部职责授予记录。
4. Studio 的 Profile 及版本比较选项目前最多加载前 100 项；大量资源项目仍需扩展同契约下的目录选择和游标加载。没有添加后端不支持的状态过滤和排序。
5. 当前列表返回详情后重新查询，尚未实现跨页面恢复全部筛选/游标及全部表单草稿。关键编辑有离开提示；没有声称覆盖每个治理表单的持久化恢复。
6. 本轮浏览器验证使用 Windows Chrome；390px 模拟手机已通过布局/操作检查，真实 iOS/Android、多浏览器及完整辅助技术审计尚未执行。
7. 本轮没有新增业务数据库、生产账户、公开地址或生产部署。正常 real 模式需要按后端说明完成基础设施配置。

这些限制是当前交付边界，不应将“页面可达”和“模拟交互通过”当作全量业务验收完成。
