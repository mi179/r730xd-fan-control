# STATUS

Last updated: 2026-08-19（桌面分层重构落地 + 抽出 r730xd_core，EXE 重建为 v0.4.1）

## Where we are

**两种交付形态均已发布可用。** 桌面一体版 `R730xdFanConsole-AllInOne-v0.4.1.exe`
已构建（内嵌 BMC.msi 自动安装）；Web v0.4.1 已于 2026-08-16 通过正式离线 installer
部署到 WRT（`root@192.168.5.2`），访问地址 `http://192.168.5.2:8088`。新镜像、
离线包和 SHA-256 均在 `dist/docker/`，详见 E-033。本地提交已 push 到 origin/main；
tag 与 GitHub Release 仍未发布（T-018）。

本项目此前的短板不是代码而是**过程记忆**：Git 只有一次 Publish 提交（E-001），
没有 CI（E-002），没有决策/证据记录。2026-07-28 引入本套治理骨架（D-012），
自此每次有意义的改动必须单独提交。

| 组成部分 | 状态 |
|---|---|
| Windows GUI（`r730xd_fan/` + `main.py`） | 完成，v0.4.1。已按 Web 的分层重构：`console.py` 状态机（零 tkinter）/ `presenters.py` 纯派生 / `view/theme.py` 配色。界面全中文，读数卡 = 进风/排风/CPU 温度 + 实时功耗，**刻意没有趋势图**（D-025） |
| 一体 EXE（含 BMC 自动安装） | v0.4.1，**当前哈希见 E-039**（同日 E-036 / E-037 的更早哈希均已作废）。此前 dist 里是 07-14 的旧产物（D-024） |
| Web 版（`webapp/`） | v0.4.1 已部署到 WRT；常规遥测与控制可用。完整 SDR 只能返回带标记的部分结果（E-031，T-014）；功耗平均/最小/最大改由本地样本统计（D-023，E-032） |
| Docker 离线安装包 | v0.4.1 已构建并用于 WRT 部署（E-033）；GitHub Releases 仍只有 v0.3.1（T-018） |
| 测试 | 两套 unittest 存在（E-016），CI 于 2026-07-28 建立（E-017） |
| 共用层 `r730xd_core/` | 协议常量、SDR 解析分类、输出脱敏；两端共用，runner 各自保留（D-027） |
| 治理文件 | 2026-07-28 建立 |

## 环境事实（改动前先核对，可能已变化）

| 项 | 值 |
|---|---|
| iDRAC | DHCP，Web 版按 MAC 重发现；2026-08-16 ARP 实测为 `192.168.5.130`，不要把地址当稳定身份 |
| WRT 宿主 | `root@192.168.5.2`，部署目录 `/opt/r730xd-fan-web` |
| Web 访问 | `http://192.168.5.2:8088`（LAN HTTP，见 D-011） |
| GitHub | `https://github.com/mi179/r730xd-fan-control`（唯一远程） |
| ipmitool | `D:\Program Files (x86)\Dell\SysMgt\bmc\ipmitool.exe` |

## Next actions

按优先级见 [TASKS.md](TASKS.md)：

**未结事项一律以 [TASKS.md](TASKS.md) 为准，本节只排优先级、不复述内容**——同一事实
存三份副本就退化成需要人工同步的负担，而这正是版本号那边已经用断言解决过的问题。

1. **T-014** —— 唯一还会产生错误数据的技术债（完整扫描目前只是 partial）。
2. **T-022** —— `deploy_wrt.ps1` 的真机验收；只做过静态验收。
3. **T-024** —— live 脚本仍靠一个 `-p` 参数拦着不被 CI 对真机执行。
4. **T-017 / T-018 / T-025 / T-006** —— 空间回收、对外发布、拆包、脚本文档。
5. **T-012 / T-013** —— 已接受的 LAN-only backlog，复议触发条件见 E-029。

长期规则 T-001：每次有意义的改动单独提交，提交信息带 D/E/T 编号。

## Things that will waste your time if you forget them

- **GUI 必须在 Windows Python 下跑**，WSL 里只能开发和跑测试；`config.py` 的候选路径分
  `nt`/非 `nt` 两套，别“简化”掉任何一套。
- **`webapp/secrets/idrac_password` 真实存在于本机**，已被 `.gitignore` 拦截。任何
  “整理项目”操作都不得把它纳入提交。
- **遥测历史现在会落盘**（SQLite，`/data/telemetry.db`，30 天保留，D-015）。内存 90 条样本仍是热路径；`?range=` 缺省时行为与旧版一致。把 `TELEMETRY_DB_PATH` 置空即回到纯内存模式。
- **完整 SDR 目前不是完整数据。** `ipmitool 1.8.19` 在 iDRAC8 2.70 上途中 `SIGSEGV`；
  UI 显示 partial 后不要连续刷新，否则可能暂时耗尽 BMC IPMI session（D-022, E-031）。
- **`docker compose ps` 的 healthy 只说明 Web 进程存活**，iDRAC 链路是否正常要看页面
  上的 iDRAC 状态。
- **升级 Web 版前先在界面恢复自动温控**；部署/回滚不改变物理风扇模式，重启后软件
  状态显示 `unknown`。

## Open risks

- **~~无 CI~~ → 已关闭 2026-07-28**：`.github/workflows/ci.yml` 三个 job（ruff+治理自检 /
  windows 桌面测试 / ubuntu Web 测试）首次运行全部 success，windows runner 无头会话下
  GUI 测试正常（T-002, E-017）。
- **版本漂移 → 换了一种形态**：D-013 关掉的是**两条产品线之间**的漂移（独立版本 + 单一事实来源
  + `desktop-v*`/`web-v*` tag，bundle 脚本对 payload 硬编码副本做一致性断言，E-018）。2026-08-19
  发现它没覆盖**源码与已构建产物之间**的漂移：桌面源码改过两次，dist 里仍是旧 EXE，版本号一模一样
  （E-036）。已按 D-024 重建为 v0.4.1，但**没有任何机制阻止它再次发生**——桌面线没有「产物是否落后于
  源码」的自动检查，Web 线因为每次部署都重新构建才没暴露这个问题。
- **完整 SDR `SIGSEGV` → 部分缓解，未关闭**：D-022 让真机崩溃前的 83 条有效记录可用，
  并严格标为 partial；T-014 仍需解决真正完整读取和 session 泄漏。常规 Redfish 遥测与
  风扇 `raw` 控制不走完整遍历，不受影响（E-031）。
- **~~LAN 明文凭据 / Redfish MITM~~ → 2026-08-15 接受为已知风险（D-021），不阻塞上线。**
  两条都转入待办：T-012（填 `REDFISH_TLS_FINGERPRINT`，一条命令）、T-013（HTTPS 反代）。
  背景仍需知道：Web 版没有独立密码，登录表单传的**就是 iDRAC root 凭据本身**，泄漏后对方
  拿到的是虚拟介质 / 电源 / BIOS / KVM。缓解手段代码里已经就绪（D-018 指纹固定、
  T-005 反代文档），只是线上未启用。
  **复议触发条件见 E-029**——服务变得可从内网外访问、内网不再是单一信任域、或 iDRAC
  凭据被复用到其他系统，任意一条成立就要重新评估优先级。
  IPMI 链路本身是安全的（RAKP 挑战应答，密码不上线），弱的只有 Redfish 这条。
