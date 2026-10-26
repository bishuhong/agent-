$ErrorActionPreference = 'Stop'
$taskProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskProjectRoot
$taskPython = Join-Path $taskProjectRoot '.venv\Scripts\python.exe'
& $taskPython scripts/manage_development_postgres.py start
if ($LASTEXITCODE -ne 0) { throw '本地开发数据库启动失败' }
& $taskPython -m uvicorn etl_agent.main:app --host 127.0.0.1 --port 8000 --reload
