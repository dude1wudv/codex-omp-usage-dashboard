# AI Agent 一键部署说明

目标：在用户的 Windows 电脑上从 GitHub 部署本看板，启动本地服务，并确保浏览器成功打开前端。不要读取、打印、提交或转述 `~/.codex/auth.json`、OMP OAuth 凭据、邮箱、账户 ID、会话正文或 `%LOCALAPPDATA%\CodexOmpUsageDashboard` 的内容。

## 自动执行流程

仓库地址：`https://github.com/dude1wudv/codex-omp-usage-dashboard`

1. 确认 `git` 与 Python 3.10+ 可用。
2. 克隆仓库到用户指定目录；未指定时使用 `E:\codex-omp-usage-dashboard`。
3. 在仓库根目录运行测试。
4. 运行 `Start.ps1`。它会复用已在运行的服务，或后台启动 `server.py`，轮询就绪后调用默认浏览器打开前端。
5. 请求 `http://127.0.0.1:8766/api/data`，确认 HTTP 200、`accounts` 为数组、`events` 为数组且响应文本不含 `access_token`、`refresh_token` 或 `Bearer `。
6. 打开或导航浏览器到 `http://127.0.0.1:8766/`，确认页面标题为“Codex 用量分析”，并确认没有显示数据加载失败。
7. 向用户报告仓库目录、浏览器地址、测试结果。若 Codex App 或 OMP 尚未登录，只报告页面中的缺失来源提示，不索要或代为粘贴 token。

推荐 PowerShell 命令：

```powershell
$repoPath = 'E:\codex-omp-usage-dashboard'
git clone https://github.com/dude1wudv/codex-omp-usage-dashboard.git $repoPath
Set-Location -LiteralPath $repoPath
python -m unittest discover -p 'test_*.py' -v
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start.ps1
Invoke-WebRequest -Uri http://127.0.0.1:8766/api/data -UseBasicParsing -TimeoutSec 30
```

如果目标目录已经是本仓库，不要重新克隆或删除目录；运行 `git pull --ff-only` 后继续测试和启动。代理失败、测试失败或端口被其他程序占用时停止，并把确切错误报告给用户，不要绕过安全检查。

## 成功标准

- 9 项或更多单元测试通过。
- `GET /api/data` 返回 200。
- API 响应只有脱敏账户标识和统计数字，不含凭据字段。
- 浏览器前端成功打开并能渲染账户、用量与模型模块。
- Git 工作区没有因运行产生未跟踪的账户数据或缓存。
