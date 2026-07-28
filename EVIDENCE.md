# EVIDENCE

一条结论只有在这里有行、且指向可核验的文件或命令输出时才算成立。大文件（EXE、镜像包）不进 Git，记录路径 + SHA-256。

| ID | 结论 | 证据 | 日期 |
|---|---|---|---|
| E-001 | 治理骨架引入前，本仓库只有一次提交（`5dfc96e Publish R730xd fan control console`），无增量开发历史 | `git log --oneline`，2026-07-28 实测 | 2026-07-28 |
| E-002 | `.github/workflows/` 目录为空，项目没有任何 CI | `ls .github/workflows` 无输出，2026-07-28 实测 | 2026-07-28 |
| E-003 | 版本号漂移：桌面版 `pyproject.toml` 为 0.4.0，Web 安装器 `webapp/installer/VERSION` 为 0.3.1 | 两文件内容，2026-07-28 实测 | 2026-07-28 |
| E-004 | 当前本地产物哈希：EXE `1BC0903F…F0F3F`（15,222,718 B）；Docker 离线包 `E9758BC5…96BD8D`（23,307,745 B）；镜像包 `F9CD35D5…F0FBAB`（23,376,271 B） | `dist/R730xdFanConsole-AllInOne-v0.4.0.exe`、`dist/docker/` 两文件，SHA-256 于 2026-07-28 计算 | 2026-07-28 |
| E-005 | 双形态交付 | `README.md`（GUI 与 `webapp/` 两节）、`webapp/README.md` | 回溯 |
| E-006 | 密码只存内存 | `README.md`“安全设计”第 1 条；`r730xd_fan/ipmi.py` `_validate_settings` 报错文案 | 回溯 |
| E-007 | `-E` 传密码 | `r730xd_fan/ipmi.py` `build_command()`（`"-E"` 入参、`IPMI_PASSWORD` 环境变量） | 回溯 |
| E-008 | 调速联锁与自动/手动 raw 命令 | `r730xd_fan/ipmi.py` `MANUAL_MODE_RAW`/`AUTO_MODE_RAW`、`speed_request()` 5–100 强校验；`README.md`“安全设计” | 回溯 |
| E-009 | 控制区用 iDRAC 凭据、无第二套密码、恒定时间比对 | `webapp/README.md`“快速遥测”；`webapp/app.py` `LoginLimiter`/登录路由 | 回溯 |
| E-010 | Docker secret 方案 | `webapp/README.md`“OpenWrt 启动”与密码章节；`webapp/compose.yaml` secrets 配置 | 回溯 |
| E-011 | MAC 身份与必填启动条件 | `webapp/README.md`“快速遥测”末两条；`webapp/app.py` `MacAddressDiscovery`、`_mac_discovery_from_environment()` | 回溯 |
| E-012 | 防火墙区与固定容器 MAC | `webapp/README.md`“WRT 防火墙”全节（三条窄规则 + `src_mac` 条件说明） | 回溯 |
| E-013 | 不公开内嵌 MSI 的 EXE | `README.md`“一体版自动依赖安装”末段 | 回溯 |
| E-014 | WSL 开发、Windows 运行 | `README.md`“VS Code + WSL 开发”；`scripts/*-from-wsl.sh` 三个脚本 | 回溯 |
| E-015 | LAN HTTP 边界与 HTTPS 反代要求 | `webapp/README.md` 引用块（`WEB_COOKIE_SECURE`） | 回溯 |
| E-016 | 测试存在且分两套：桌面 `tests/`（dependency/ipmi/ui_startup）与 Web `webapp/tests/`（含对部署实例的无损 live smoke） | `tests/test_*.py`、`webapp/tests/` 目录与 `live_smoke.py` 文件头注释 | 2026-07-28 |

## 外部原始资料（不进仓库）

| 资料 | 位置 | 说明 |
|---|---|---|
| Dell R730xd 官方手册 PDF | `D:\UserData\Documents\r730xd-ompublication-zh-cn.pdf` | 厂商原始文档，摘要不能替代原文 |
