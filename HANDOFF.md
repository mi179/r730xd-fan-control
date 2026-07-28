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
8. `README.md` / `webapp/README.md` —— 面向用户的操作文档（最详细的使用说明在这里）

## The one-line version

**Windows GUI v0.4.0 和 OpenWrt Docker Web 版 v0.3.1 都已发布可用**；Web 版跑在
`192.168.5.2:8088`。项目代码成熟，治理文件 2026-07-28 才补上，最缺的是 CI（T-002）
和版本号策略（T-003）。

## Environment

- 宿主 Windows 11；开发在 WSL Ubuntu（`/mnt/d/UserData/Documents/r730xd_fan`），
  GUI 用 `.venv-win` 的 Windows Python 运行，测试在 WSL 用 `.venv-wsl` 跑。
- iDRAC 在 `192.168.5.151`（DHCP）；WRT 宿主 `root@192.168.5.2`，部署目录
  `/opt/r730xd-fan-web`。
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
