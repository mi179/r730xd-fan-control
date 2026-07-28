# PLAN

## Phase 0 — 治理补齐（当前）

- [x] 引入治理骨架：PROJECT / SPEC / DECISIONS / EVIDENCE / STATUS / TASKS / PLAN / HANDOFF / AGENTS（D-012）
- [x] 记录当前产物哈希与项目事实（E-001..E-016）
- [ ] 建立 `scripts/verify_governance.py` 提交前自检
- [ ] 治理文件入库提交

**出口标准**：新会话只读仓库文件即可完整接手（HANDOFF.md 的阅读顺序走通）。

## Phase 1 — 工程基础

- [x] T-002 最小 CI（两套 unittest + ruff + 治理自检）—— 2026-07-28 完成（E-017）
- [ ] T-003 版本号策略
- [ ] T-004 发布流程固化（测试 → 打包 → 哈希入 EVIDENCE → Release）

**出口标准**：一次发布全流程有可复现的清单，哈希留档。

## Phase 2 — 部署加固

- [ ] T-005 HTTPS 反代文档
- [ ] T-006 live 测试脚本使用说明

**出口标准**：跨不可信网络访问有 documented 的安全路径。

## 规则

- 阶段出口标准未满足不进入下一阶段；允许并行做下一阶段的准备性研究。
- 任何阶段发现既有决策错误，回 DECISIONS.md 标 Superseded 并立新行，不改旧行。
