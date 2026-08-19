# PLAN

## Phase 0 — 治理补齐（已完成）

- [x] 引入治理骨架：PROJECT / SPEC / DECISIONS / EVIDENCE / STATUS / TASKS / PLAN / HANDOFF / AGENTS（D-012）
- [x] 记录当前产物哈希与项目事实（E-001..E-016）
- [x] 建立 `scripts/verify_governance.py` 提交前自检
- [x] 治理文件入库提交

**出口标准**：新会话只读仓库文件即可完整接手（HANDOFF.md 的阅读顺序走通）。

## Phase 1 — 工程基础

- [x] T-002 最小 CI（两套 unittest + ruff + 治理自检）—— 2026-07-28 完成（E-017）
- [x] T-003 版本号策略 —— 2026-07-28 完成：独立版本 + 单一来源 + tag 规范（D-013, E-018）
- [x] T-004 发布流程固化（测试 → 打包 → 哈希入 EVIDENCE → installer / verify / live smoke，E-030）

**出口标准**：一次发布全流程有可复现的清单，哈希留档。

## Phase 2 — 部署加固

- [x] T-005 HTTPS 反代文档
- [ ] T-006 live 测试脚本使用说明

**出口标准**：跨不可信网络访问有 documented 的安全路径。

## Phase 3 — Web v0.4.0 上线与真机兼容

- [x] T-010 WRT 原生构建、离线包、回滚保护与部署验收（E-030）
- [x] D-022 `ipmitool` `SIGSEGV` 时保留并标记部分 SDR，其他失败不掩盖（E-031）
- [ ] T-014 找到能在 iDRAC8 上完整读取 SDR 且不泄漏 session 的实现
- [ ] T-015 push / `web-v0.4.1` tag / GitHub Release（外部发布需明确授权；v0.4.0 已被取代，不单独发布）

**出口标准**：完整 SDR 真机读取不崩溃、不泄漏 session；在此之前 UI 必须诚实标记 partial。

## 规则

- 阶段出口标准未满足不进入下一阶段；允许并行做下一阶段的准备性研究。
- 任何阶段发现既有决策错误，回 DECISIONS.md 标 Superseded 并立新行，不改旧行。
