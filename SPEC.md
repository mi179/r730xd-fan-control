# SPEC

## Scope

- Windows 桌面 GUI：连接测试、SENSORS 只读扫描（`sdr elist all`）、固定转速 5%–100%、恢复自动温控、后台线程执行不卡界面。
- 一体 EXE：首次运行检测 `ipmitool`，缺失时校验内嵌 `BMC.msi` 的 SHA-256 后提权静默安装。
- Web 版：公开只读遥测（Redfish `Thermal`/`Power`，8 秒缓存，内存保留 90 条样本）；控制区（调速、恢复自动、连接设置）需 iDRAC 用户名+密码解锁；完整 SDR 扫描公开但有匿名冷却，已知 `ipmitool` `SIGSEGV` 时只返回明确标记的部分结果；iDRAC DHCP 环境下按网卡 MAC 自动重新发现地址。
- OpenWrt 部署：独立 Docker 网络 `r730xd-fan-control`、独立默认拒绝防火墙区 `r730xd_fan`、离线安装包一键安装/校验/回滚。

## Non-goals

- 不做跨广域网暴露：Web 版定位是可信 LAN 内的 HTTP 服务。
- 不做多服务器管理：当前只面向一台 R730xd / 一个 iDRAC。
- 不修改服务器 BIOS/iDRAC 固件设置；除风扇模式与转速 raw 命令外一律只读。
- 不在公开 Release 分发内嵌 `BMC.msi` 的 EXE（Dell 再分发许可未确认，见 D-009）。

## Hard safety boundaries

- **密码三不**：桌面版密码只存进程内存；命令行用 `ipmitool -E` 从环境变量读密码；日志/UI 永不出现密码。Web 版密码存宿主机 `secrets/idrac_password`，以只读 Docker secret 挂载，不进 `.env`、不进镜像。
- **联锁**：固定转速前必须先在 UI 解除安全联锁，并成功关闭自动温控（`raw 0x30 0x30 0x01 0x00`）。“恢复自动温控”按钮始终可用。
- **转速范围**：只允许 5%–100%，下限防止完全停转（`speed_request` 强校验）。
- **公开面最小化**：Web 公开接口只读；`sdr elist all` 只由按钮触发；容器防火墙只放行 TCP 443（Redfish）、UDP 623（IPMI）、LAN→TCP 8080（Web）。
- **身份核验**：`IDRAC_MAC` 与 `IDRAC_DISCOVERY_CIDR` 缺失时容器拒绝启动；地址重发现只发无凭据 RMCP/ASF 探测，绝不把密码发给候选设备。
- **写操作门禁**：安装器首次安装/升级默认不发送任何风扇调速命令。
