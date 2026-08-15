# TASKS

| ID | 任务 | 状态 | 关联 |
|---|---|---|---|
| T-001 | 每次有意义的改动单独提交，提交信息带 D/E/T 编号 | 长期规则 | D-012 |
| T-002 | 补最小 CI workflow：桌面 + Web 两套 unittest，加 ruff | **Done 2026-07-28**（`.github/workflows/ci.yml`，含治理自检；首次运行三 job 全绿，含 windows runner 上的 GUI 测试，E-017） | E-002, E-016, E-017 |
| T-003 | 明确桌面版/Web 版版本号策略（统一还是各自独立），写进 README | **Done 2026-07-28**（独立版本 + 单一来源 + tag 规范，D-013；build 脚本已断言一致性，E-018） | E-003, E-018 |
| T-004 | 发布流程固化：发布前跑两套测试 + 记录产物 SHA-256 到 EVIDENCE.md | Open | E-004 |
| T-005 | 写 HTTPS 反向代理部署文档（Caddy/Nginx 前置 + `WEB_COOKIE_SECURE=true`） | **Done 2026-08-15**（`webapp/README.md`「凭据传输」节，含 Caddy/nginx 两版与 `TRUSTED_ORIGINS`） | D-011 |
| T-006 | 为 `webapp/tests/` 的 live 脚本（live_smoke / live_idrac_readonly / inspect_idrac_network）补一段使用说明 | Open | E-016 |
| T-007 | 治理文件引入 | **Done 2026-07-28** | D-012 |
| T-008 | 两端前端统一为近黑单色设计语言，删除装饰性元素 | **Done 2026-08-15**（D-014，E-020） | D-014 |
| T-009 | Web 版遥测历史 SQLite 持久化 + 时间区间查询 | **Done 2026-08-15**（D-015，E-021..E-022） | D-015 |
| T-011 | 完整 SDR 对匿名开放 + 触发冷却；传感器中文注解；等宽字体收敛 | **Done 2026-08-15**（D-016, D-017, E-024, E-025） | D-016, D-017 |
| T-012 | 在 WRT 上取 iDRAC 证书指纹并填入 `.env` 的 `REDFISH_TLS_FINGERPRINT`（关闭 Redfish 凭据 MITM 路径） | Backlog（D-021 接受风险，不阻塞上线）—— 代价极低：一条 openssl 命令 + 重启 | D-018, D-021 |
| T-013 | 部署 HTTPS 反代并设 `WEB_COOKIE_SECURE=true` / `TRUSTED_ORIGINS`；同时处理反代后 `REMOTE_ADDR` 塌缩导致限速退化为全局桶的问题（ProxyFix 或共享网络命名空间） | Backlog（D-021 接受风险，不阻塞上线） | D-011, D-019, D-021 |
| T-010 | 部署新版 Web 到 WRT：需重建镜像（Dockerfile 新增 `/data`）并首次创建 `r730xd-telemetry` volume | Open | D-015 |

关闭任务时把状态改为 Done + 日期，不删除行。
