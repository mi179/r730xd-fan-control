# 桌面版使用说明

适用于 Windows 单文件程序 `R730xdFanConsole-AllInOne-v<版本>.exe`。
开发、构建和打包方式见 [../README.md](../README.md)，本文只讲**拿到 exe 之后怎么用**。

## 先看这三条

1. **固定低转速会削弱主板的自动保护。** 程序界面上写着同样的话。设完固定转速后，
   请持续观察 CPU、内存和硬盘温度。
2. **`RESTORE AUTO THERMAL`（恢复自动温控）在任何时候都可用**，不需要先解锁任何东西。
   出问题时先点它。
3. **退出程序不会改变风扇模式。** 你设的固定转速会一直留在 iDRAC 里，直到你恢复
   自动温控或者服务器断电重启。关掉窗口不等于恢复原状。

## 前提条件

- 一台 Dell PowerEdge R730xd，iDRAC 网口可从这台 Windows 电脑访问。
- iDRAC 里已启用 **IPMI over LAN**（iDRAC 设置 → 网络 → IPMI 设置）。没开的话
  程序连不上，报的是超时而不是「未启用」。
- 一个有权限的 iDRAC 账号（通常是 `root`）。
- `ipmitool.exe`。一体版 exe 内嵌了 Dell 官方签名的 `BMC.msi`，缺少时会自动安装，
  见下一节。

## 第一次启动

双击 exe，程序先检测 `ipmitool.exe`：

- **已安装** —— 直接进入主界面，不请求管理员权限。
- **未安装** —— 显示一个首次运行进度窗口，弹出一次 Windows 管理员权限请求（UAC），
  静默安装 Dell BMC 工具，然后自动进入同一个主界面。**不需要再运行第二个程序。**

安装日志在：

```text
C:\ProgramData\R730xdFanConsole-<随机标识>\install-bmc.log
```

这个目录只在确实需要自动安装时才创建。安装载荷会先校验 SHA-256 再执行。

如果你拒绝了 UAC 提示，程序不会崩，但会一直处于找不到 `ipmitool.exe` 的状态——
重开一次，或者自己装好 Dell BMC 工具再启动。

## 连接 iDRAC

主界面右上角的状态标签一开始是 `● IDRAC SETUP REQUIRED`。点 `OPEN CONNECTION
SETTINGS` 填四项：

| 字段 | 填什么 |
|---|---|
| `IDRAC HOST` | iDRAC 的 IP 地址 |
| `USERNAME` | iDRAC 账号，通常 `root` |
| `PASSWORD` | 该账号密码；勾选「显示密码」可以核对 |
| `IPMITOOL EXECUTABLE` | `ipmitool.exe` 路径，一般会自动填好，不用改 |

点 `APPLY SETTINGS` 保存，再点 `TEST` 验证。连上以后状态变成 `● IDRAC ONLINE`。

> **iDRAC 走 DHCP 的话，地址会变。** 程序里填的默认地址只是上次用过的值，不是身份。
> 连不上先在路由器或 iDRAC 前面板确认当前地址。

密码**只存在于当前程序进程的内存里**，不写进任何配置文件；调用 `ipmitool` 时用的是
`-E` 参数，密码不会出现在进程命令行参数和日志里。所以每次重开程序都要重新输入。
也可以在启动前设好当前进程的 `IPMI_PASSWORD` 环境变量。

## 调速

这是一条有意做长的路径，每一步都是刹车：

1. 勾选 **`解除安全联锁`**。不勾，下面所有调速按钮都是灰的。
2. 点 **`ENABLE MANUAL CONTROL`**。这一步真正关闭 iDRAC 的自动温控，成功后状态从
   `AUTO THERMAL` 变成 `MANUAL OVERRIDE`。
3. 选转速。两种方式：
   - **`QUICK PRESETS`** —— `10% QUIET`、`15% DAILY`、`20% SUMMER`、`30% LOAD`。
   - **自定义** —— 拖滑块（显示为 `CUSTOM N%`），再点 `APPLY CUSTOM OUTPUT`。
4. 左侧仪表盘显示当前 `PERCENT OUTPUT`，状态行显示 `ACTIVE · N%`；未接管时显示
   `LOCKED`。

**恢复原状**：点 `RESTORE AUTO THERMAL`。这个按钮不受安全联锁限制，永远可点。

> 刚打开程序时状态可能显示 `STATE UNKNOWN`。这不是故障——程序没法从 iDRAC 读回
> 「当前是不是手动模式」，只能记住自己这一次做过什么。服务器重启过、或者上次是别的
> 途径改的，就是 unknown。不确定的时候点一次 `RESTORE AUTO THERMAL` 回到已知状态。

## 看传感器

主界面点 `SENSORS` 打开独立窗口 `IDRAC SENSOR MONITOR`，内容是 `ipmitool sdr elist
all` 的只读结果：温度、风扇 RPM、功耗、电压和其余 SDR 记录。

- `REFRESH` 重新读取，读取期间按钮显示 `READING...`。
- 上方搜索框按名称、类型、读数或状态过滤。
- 勾 **`只看异常`** 只留下非正常状态的项。
- 顶部会显示本次读取用时。

**颜色只表示报警**：琥珀 = warning，红 = critical。界面其他地方不使用饱和色，所以
一旦看到颜色，那就是真的有事。正常状态下异常横幅完全不占位置。

## 出问题时

| 现象 | 多半是什么 |
|---|---|
| 一直 `SETUP REQUIRED`，`TEST` 超时 | iDRAC 地址变了（DHCP），或 IPMI over LAN 没开 |
| 提示找不到 `ipmitool.exe` | UAC 被拒绝，或 Dell BMC 工具没装成功；看上面的日志路径 |
| 认证失败 | 账号密码错，或该账号在 iDRAC 里没有 IPMI 权限 |
| 按钮全灰 | 没勾「解除安全联锁」 |
| 风扇没反应 | 先确认状态是 `MANUAL OVERRIDE`；`AUTO THERMAL` 下 iDRAC 会覆盖你设的值 |
| 界面卡住 | 不应该发生——所有命令都在后台线程跑。真卡了请留下复现步骤 |

## 不想用了

- 想恢复服务器原本的行为：点 `RESTORE AUTO THERMAL`，然后关掉程序。
- exe 是单文件的，直接删掉即可，不写注册表。
- 如果当初自动装过 Dell BMC 工具，那是一个独立的 Windows 程序，需要的话到
  「应用和功能」里单独卸载。
- `C:\ProgramData\R730xdFanConsole-<随机标识>\` 只有安装日志，可以直接删。

## macOS / Linux

目前没有 Mac 和 Linux 的桌面版。这些平台请用 Web 版——浏览器打开就行，功能覆盖
日常监控和调速，部署方式见 [../webapp/README.md](../webapp/README.md)。
