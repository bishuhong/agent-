# 本地开发登录与启动

更新日期：2026-10-23。

经用户确认，已创建独立、持久化的本地 PostgreSQL 开发库并初始化现有迁移。该库不使用测试数据库，也没有迁移或修改现有业务库。

## 登录

- 真实前端：`http://127.0.0.1:5173/`。
- 账号：`1111`。
- 密码：本次随机生成并已在聊天交付，另存于本机被忽略的 `.runtime/local-login.json`，不写入文档或源码。
- 项目：本地开发工作空间。
- 角色：maker、operator；未赋予自我审批资格或隐式管理员权限。

如果浏览器仍保留旧错误，请刷新登录页后重新输入账号密码。

## 日常启动

后端窗口（包含启动本地开发数据库）：

```powershell
cd D:\python\ETL_Agent
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_backend.ps1
```

前端窗口：

```powershell
cd D:\python\ETL_Agent\frontend
npm run dev
```

浏览器打开 `http://127.0.0.1:5173/`。每个窗口保持运行；按 Ctrl+C 停止对应前后端进程。停止后端不会删除数据，也不会自动停止独立数据库。

单独管理开发数据库：

```powershell
cd D:\python\ETL_Agent
.\.venv\Scripts\python.exe scripts/manage_development_postgres.py status
.\.venv\Scripts\python.exe scripts/manage_development_postgres.py start
# 需要停止数据库时，先停止后端，再执行：
.\.venv\Scripts\python.exe scripts/manage_development_postgres.py stop
```

## 本机配置

| 项目 | 位置/值 |
|---|---|
| 数据目录 | `.runtime/pgdata-development` |
| 绑定地址 | `127.0.0.1:53553`，仅本机 |
| 数据库 | `etl_agent_development` |
| 应用数据库角色 | `etl_app`，无超级用户/创建库/创建角色权限 |
| PostgreSQL 二进制 | 复用项目已有 `.test-runtime/postgresql/pgsql/bin`，数据与测试库分离 |
| 配置 | 根目录 `.env`，已补入数据库连接、会话/游标及开发签名密钥，保留原有模型配置 |
| 本机私密文件 | `.runtime/development-credentials.json`、`.runtime/local-login.json`，均被 Git 忽略 |

不要删除 `.runtime/pgdata-development`；这是持久化业务开发数据。清理测试目录时需保留上述 PostgreSQL 二进制目录。

初始化脚本 `scripts/setup_local_development.py` 限定本项目的数据目录和开发库身份，拒绝覆盖其他数据库配置。重跑保留原账号密码，不重置既有账号；不会在每次后端启动时自动迁移。首次沙箱初始化超时留下的无控制文件目录已保留为 `.runtime/pgdata-development-init-incomplete-*`，实际运行使用完整初始化的新目录。

## 实际验证

通过真实前端代理 `127.0.0.1:5173` 验证：登录 200、GET me 200、项目列表 200（1 个项目）、退出 200、退出后 GET me 401。后端已通过 reload 重新加载新增配置。

该结果说明真实登录已接通。Redis/Worker、SeaTunnel、Vault 和目标发布服务仍需按后端说明接入；登录成功不等于真实 ETL 数据搬运已可用。当前本地 HTTP 使用非 Secure Cookie；生产仍须 HTTPS、安全配置及独立部署。
