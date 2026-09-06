# v1.0.0

首个完整公开版本，面向 Windows 本地使用的 Codex App + OMP 用量看板。

## 功能

- 读取 Codex Desktop 与 OMP 的本地 OpenAI 会话用量。
- 固定拆分 Codex App Team 账户和 OMP Team 账户。
- 展示 7 天 OAuth 配额、重置时间、本轮与累计 token。
- 按模型展示 input、cache read、cache write、output 和等效 API 价值。
- 默认使用用户指定的订阅折算价，并保留官方 Standard API 价作为对照。
- 图表悬停查看每日、模型和周期估算细节。
- 展示两个账户独立明细、最近重置周期总可用等效 API 价值及 CSV 导出。
- 提供 `Start.cmd`、PowerShell 启动脚本和 Windows 桌面快捷方式安装脚本。

## 安全边界

- 服务只监听 `127.0.0.1`。
- OAuth token 只在内存中读取，不写入仓库或看板 API 响应。
- 会话正文不进入统计结果；本地缓存和账户绑定保存在 `%LOCALAPPDATA%\CodexOmpUsageDashboard`。

## 已知限制

等效 API 价值是本地日志重估指标，不代表订阅账单或官方额度金额。周期总可用价值由已观测用量和使用比例推测，会受到本机历史完整度、缓存比例和观测时点影响。
