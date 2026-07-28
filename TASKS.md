# TASKS

| ID | 任务 | 状态 | 关联 |
|---|---|---|---|
| T-001 | 每次有意义的改动单独提交，提交信息带 D/E/T 编号 | 长期规则 | D-012 |
| T-002 | 补最小 CI workflow：桌面 + Web 两套 unittest，加 ruff | **Done 2026-07-28**（`.github/workflows/ci.yml`，含治理自检；首次运行三 job 全绿，含 windows runner 上的 GUI 测试，E-017） | E-002, E-016, E-017 |
| T-003 | 明确桌面版/Web 版版本号策略（统一还是各自独立），写进 README | **Done 2026-07-28**（独立版本 + 单一来源 + tag 规范，D-013；build 脚本已断言一致性，E-018） | E-003, E-018 |
| T-004 | 发布流程固化：发布前跑两套测试 + 记录产物 SHA-256 到 EVIDENCE.md | Open | E-004 |
| T-005 | 写 HTTPS 反向代理部署文档（Caddy/Nginx 前置 + `WEB_COOKIE_SECURE=true`） | Open | D-011 |
| T-006 | 为 `webapp/tests/` 的 live 脚本（live_smoke / live_idrac_readonly / inspect_idrac_network）补一段使用说明 | Open | E-016 |
| T-007 | 治理文件引入 | **Done 2026-07-28** | D-012 |

关闭任务时把状态改为 Done + 日期，不删除行。
