# STATUS

Last updated: 2026-08-15（前端重做 + 遥测持久化 + 安全加固）

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

- **T-010 未做：新版 Web 尚未部署到 WRT。** 线上仍是 v0.3.1 镜像。新版 Dockerfile 增加了 `/data`（属主 10001），必须重建镜像；首次 `docker compose up` 会创建 `r730xd-telemetry` volume。升级前先在界面恢复自动温控。


按优先级见 [TASKS.md](TASKS.md)。最紧要的三件：

- **T-001** —— 从此每次有意义的改动单独提交，提交信息带 D/E/T 编号。
- **T-004** —— 固化发布流程：发布前跑两套测试 + 产物 SHA-256 入 EVIDENCE.md。
- **T-005** —— HTTPS 反代部署文档（跨不可信网络访问 Web 版的安全路径）。
- （T-002 CI、T-003 版本策略已于 2026-07-28 完成，见 E-017 / D-013。）

## Things that will waste your time if you forget them

- **GUI 必须在 Windows Python 下跑**，WSL 里只能开发和跑测试；`config.py` 的候选路径分
  `nt`/非 `nt` 两套，别“简化”掉任何一套。
- **`webapp/secrets/idrac_password` 真实存在于本机**，已被 `.gitignore` 拦截。任何
  “整理项目”操作都不得把它纳入提交。
- **遥测历史现在会落盘**（SQLite，`/data/telemetry.db`，30 天保留，D-015）。内存 90 条样本仍是热路径；`?range=` 缺省时行为与旧版一致。把 `TELEMETRY_DB_PATH` 置空即回到纯内存模式。
- **`docker compose ps` 的 healthy 只说明 Web 进程存活**，iDRAC 链路是否正常要看页面
  上的 iDRAC 状态。
- **升级 Web 版前先在界面恢复自动温控**；部署/回滚不改变物理风扇模式，重启后软件
  状态显示 `unknown`。

## Open risks

- **~~无 CI~~ → 已关闭 2026-07-28**：`.github/workflows/ci.yml` 三个 job（ruff+治理自检 /
  windows 桌面测试 / ubuntu Web 测试）首次运行全部 success，windows runner 无头会话下
  GUI 测试正常（T-002, E-017）。
- **~~版本漂移~~ → 已关闭 2026-07-28**：D-013 确立双产品线独立版本 + 单一事实来源 +
  `desktop-v*`/`web-v*` tag 规范；bundle 脚本对 payload 硬编码副本做一致性断言（E-018）。
- **~~LAN 明文凭据 / Redfish MITM~~ → 2026-08-15 接受为已知风险（D-021），不阻塞上线。**
  两条都转入待办：T-012（填 `REDFISH_TLS_FINGERPRINT`，一条命令）、T-013（HTTPS 反代）。
  背景仍需知道：Web 版没有独立密码，登录表单传的**就是 iDRAC root 凭据本身**，泄漏后对方
  拿到的是虚拟介质 / 电源 / BIOS / KVM。缓解手段代码里已经就绪（D-018 指纹固定、
  T-005 反代文档），只是线上未启用。
  **复议触发条件见 E-029**——服务变得可从内网外访问、内网不再是单一信任域、或 iDRAC
  凭据被复用到其他系统，任意一条成立就要重新评估优先级。
  IPMI 链路本身是安全的（RAKP 挑战应答，密码不上线），弱的只有 Redfish 这条。
