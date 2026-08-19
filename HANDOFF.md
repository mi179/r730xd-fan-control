# HANDOFF

如何在一个全新会话里接手本项目。本文件**刻意不复述当前进度**——进度在 STATUS.md，
写在这里会腐烂。保持这样。

## Read in this order

1. `PROJECT.md` —— 做什么、为什么
2. `SPEC.md` —— 范围、非目标、硬安全边界
3. `DECISIONS.md` —— 每个判断及其理由；依赖某条决策前先看 Status 列有没有 Superseded
4. `STATUS.md` —— 现在到哪了、下一步是什么
5. `PLAN.md` —— 阶段门禁
6. `TASKS.md` —— 未结事项
7. `EVIDENCE.md` —— 以上所有结论背后的证据
8. `AUDIT-FINDINGS.md` —— 2026-08-20 全量审计的结论清单（F1..F15）；已修项带提交号，撤回项带复核记录，未修项对应 `TASKS.md` 的 T-038..T-041
9. `README.md` / `webapp/README.md` —— 面向用户的操作文档（最详细的使用说明在这里）

## The one-line version

**Windows GUI v0.4.1 和 OpenWrt Docker Web 版 v0.4.1 都已发布可用**；Web 版跑在
`192.168.5.2:8088`（E-033）。两条产品线各自独立版本（D-013）。CI（T-002）与版本号
策略（T-003）已于 2026-07-28 完成。

## Environment

- 宿主 Windows 11；开发在 WSL Ubuntu（`/mnt/d/UserData/Documents/r730xd_fan`），
  GUI 用 `.venv-win` 的 Windows Python 运行，测试在 WSL 用 `.venv-wsl` 跑。
- iDRAC 走 DHCP，**地址不是稳定身份**——代码按 MAC `d0:94:66:8c:e0:e3` 重发现；
  2026-08-16 ARP 实测为 `192.168.5.130`（此前文档记的 `192.168.5.151` 已失效）。
  WRT 宿主 `root@192.168.5.2`，部署目录 `/opt/r730xd-fan-web`。
- 远程仓库只有一个：`https://github.com/mi179/r730xd-fan-control`。
- Dell 官方手册 PDF 在仓库外：`D:\UserData\Documents\r730xd-ompublication-zh-cn.pdf`。

## Things that will bite you immediately on resuming

- **不要在 WSL 里启动 GUI**——`config.py` 按 `os.name` 分两套 ipmitool 候选路径，
  WSL 候选指向 `/mnt/d/...` 的 Dell 安装，GUI 必须走 `.venv-win`。
- **改 `webapp` 部署相关代码前**，先读 `webapp/README.md` 的“升级与回滚”：升级前
  `docker image tag ... :rollback`，且**先在界面恢复自动温控**。
- **一体 EXE 的 BMC.msi 不进仓库也不在公开 Release**（D-009）。自行构建需要把签名
  一致的 `BMC.msi` 放到 `C:\OpenManage\BMC.msi`，SHA-256 硬编码在
  `r730xd_fan/dependency.py` 的 `BMC_MSI_SHA256`。
- **防火墙规则里的 `src_mac='02:73:0d:73:00:01'` 是 Compose 给容器固定的 MAC**，
  改 Compose 的 `mac_address` 而不改防火墙规则会直接把 iDRAC 链路掐断。

## Tests

```bash
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s webapp/tests -p 'test_*.py' -v
```

两套都要跑。`webapp/tests/live_*.py` 针对真实部署实例，默认不发送调速命令。

## Verify the project is intact

```bash
python3 scripts/verify_governance.py
```

检查治理文件存在、EVIDENCE 非空、无凭据文件被 Git 跟踪、Git 历史存在、
含非 ASCII 的 `.ps1` 带 BOM。提交前运行。

## Session notes

本文件只写**长期有效**的接手方法。当前进度一律看 `STATUS.md` / `TASKS.md` /
`EVIDENCE.md`——2026-08-16 有一轮把整个会话现场（317 行，占全文 83%）写进了这里，
下一轮做完就全部过期，还反过来和 STATUS 矛盾：它坚持"功耗修复尚未落地"，而那时
修复已经部署并真机验证。开头那条规则是对的，别再破例。

需要跨会话保留的东西按类型归位：

| 内容 | 去处 |
|---|---|
| 判断与其理由 | `DECISIONS.md`（被推翻时标 Superseded，不删旧行） |
| 结论的依据、实测数据、产物哈希 | `EVIDENCE.md` |
| 现在到哪了 | `STATUS.md` |
| 未结事项 | `TASKS.md` |
| 会咬人的坑 | 本文件上面的 "Things that will bite you immediately" |

如果确实需要留一段会话现场（比如停在某个失败的中途），写清楚**它什么时候作废**，
并在完成后立即删除。
