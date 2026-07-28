# STATUS

Last updated: 2026-07-28（治理骨架建立日）

## Where we are

**两种交付形态均已发布可用。** 桌面一体版 `R730xdFanConsole-AllInOne-v0.4.0.exe`
已构建（内嵌 BMC.msi 自动安装）；Web 版 v0.3.1 Docker 离线包在
`dist/docker/` 并对应 GitHub Release。Web 版部署在 WRT（`root@192.168.5.2`），
访问地址 `http://192.168.5.2:8088`。

本项目此前的短板不是代码而是**过程记忆**：Git 只有一次 Publish 提交（E-001），
没有 CI（E-002），没有决策/证据记录。2026-07-28 引入本套治理骨架（D-012），
自此每次有意义的改动必须单独提交。

| 组成部分 | 状态 |
|---|---|
| Windows GUI（`r730xd_fan/` + `main.py`） | 完成，v0.4.0 |
| 一体 EXE（含 BMC 自动安装） | 已构建，本地 dist，哈希见 E-004 |
| Web 版（`webapp/`） | 完成，v0.3.1 已部署到 WRT |
| Docker 离线安装包 | 已构建并发布（GitHub Releases） |
| 测试 | 两套 unittest 存在（E-016），CI 于 2026-07-28 建立（E-017） |
| 治理文件 | 2026-07-28 建立 |

## 环境事实（改动前先核对，可能已变化）

| 项 | 值 |
|---|---|
| iDRAC | `192.168.5.151`（DHCP，Web 版按 MAC 重发现） |
| WRT 宿主 | `root@192.168.5.2`，部署目录 `/opt/r730xd-fan-web` |
| Web 访问 | `http://192.168.5.2:8088`（LAN HTTP，见 D-011） |
| GitHub | `https://github.com/mi179/r730xd-fan-control`（唯一远程） |
| ipmitool | `D:\Program Files (x86)\Dell\SysMgt\bmc\ipmitool.exe` |

## Next actions

按优先级见 [TASKS.md](TASKS.md)。最紧要的三件：

- **T-001** —— 从此每次有意义的改动单独提交，提交信息带 D/E/T 编号。
- **T-003** —— 明确桌面版与 Web 版的版本号策略，消除 0.4.0/0.3.1 漂移（E-003）。
- **T-004** —— 固化发布流程：发布前跑两套测试 + 产物 SHA-256 入 EVIDENCE.md。
- （T-002 CI 已于 2026-07-28 完成，见 E-017。）

## Things that will waste your time if you forget them

- **GUI 必须在 Windows Python 下跑**，WSL 里只能开发和跑测试；`config.py` 的候选路径分
  `nt`/非 `nt` 两套，别“简化”掉任何一套。
- **`webapp/secrets/idrac_password` 真实存在于本机**，已被 `.gitignore` 拦截。任何
  “整理项目”操作都不得把它纳入提交。
- **Web 版重启容器后遥测历史清零**（内存 90 条样本），这不是 bug。
- **`docker compose ps` 的 healthy 只说明 Web 进程存活**，iDRAC 链路是否正常要看页面
  上的 iDRAC 状态。
- **升级 Web 版前先在界面恢复自动温控**；部署/回滚不改变物理风扇模式，重启后软件
  状态显示 `unknown`。

## Open risks

- **~~无 CI~~ → 已关闭 2026-07-28**：`.github/workflows/ci.yml` 三个 job（ruff+治理自检 /
  windows 桌面测试 / ubuntu Web 测试）首次运行全部 success，windows runner 无头会话下
  GUI 测试正常（T-002, E-017）。
- **版本漂移**：桌面 0.4.0 / Web 0.3.1，发布说明容易写错（T-003）。
- **LAN HTTP**：控制区密码在可信 LAN 内明文传输（D-011 已接受；跨不可信网络需 T-005 的 HTTPS 反代文档）。
