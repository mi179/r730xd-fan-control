# R730xd Thermal Control Console

一套面向 Dell PowerEdge R730xd 的 iDRAC 风扇控制 GUI。界面采用宝玉 `blueprint`
设计语言：工程网格、冷蓝主色、琥珀告警和清晰的操作层级。

## 安全设计

- Windows 桌面版密码只保存在当前 GUI 进程内存，不写入项目文件。
- 调用 `ipmitool -E`，密码不会出现在进程参数或日志里。
- 固定转速前必须先解除安全联锁，并成功关闭自动温控。
- “恢复自动温控”始终可用。
- 所有命令在后台线程运行，不会卡死界面。
- `SENSORS` 使用只读命令 `sdr elist all`，在独立滚动窗口显示温度、风扇 RPM、
  功耗、电压及其他 SDR 状态；原始长输出不会写满主日志。
- 左侧控制栏支持鼠标滚轮，缩小窗口高度后仍能访问全部按钮。

> 固定低转速会削弱服务器自动保护能力。请持续监控 CPU、内存和硬盘温度。

## WRT 手机 Web 版

`webapp/` 是独立的 OpenWrt Docker 版本：访客无需密码即可查看温度、功耗、
风扇 RPM 和短期趋势；只有调速与管理操作需要同一套 iDRAC 凭据。部署、安全
边界、Docker secret 与回滚方法见 [webapp/README.md](webapp/README.md)。

### Docker 离线一键安装

从 [GitHub Releases](https://github.com/mi179/r730xd-fan-control/releases) 下载并放在同一目录：

- `R730xdFan-Web-Docker-v0.3.1.tar.gz`
- `R730xdFan-Web-Docker-v0.3.1.tar.gz.sha256`
- `Install-R730xdFan-Web.ps1`

在 Windows PowerShell 执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-R730xdFan-Web.ps1
```

安装器会校验 SHA-256、上传预构建的 `linux/amd64` 镜像、配置独立 OpenWrt
防火墙区并等待容器健康。首次安装会收集物理网卡 MAC，并隐藏输入 iDRAC 密码；升级
默认保留现有密码。安装及验证过程不会主动发送风扇调速命令。

## VS Code + WSL 开发

本项目采用“WSL 开发、Windows 运行 GUI”的方式：代码、测试和 Git 在 Ubuntu WSL 中完成，
CustomTkinter GUI 通过 Windows Python 启动，直接调用 Dell Windows 版 `ipmitool.exe`。

在 Ubuntu 终端执行：

```bash
cd /mnt/d/UserData/Documents/r730xd_fan
code .
```

首次进入 VS Code 后，可通过 `Terminal -> Run Task` 执行：

1. `Setup: Windows GUI venv`
2. `Tests: WSL`
3. `Run: Windows GUI`

只运行 Python 源码、不打包时，选择第 3 项即可。也可以在 Windows PowerShell 中执行：

```powershell
.\.venv-win\Scripts\python.exe .\main.py
```

如果需要重新生成可直接双击的 Windows 程序，运行任务 `Build: Windows EXE`。输出位置：

```text
dist\R730xdFanConsole-AllInOne-v0.4.0.exe
```

也可以直接执行：

```bash
bash scripts/setup-windows-from-wsl.sh
bash scripts/run-windows-from-wsl.sh
```

## Windows 直接运行

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\run_windows.ps1
```

打包单文件 EXE：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

## 一体版自动依赖安装

`R730xdFanConsole-AllInOne-v0.4.0.exe` 本身就是完整调速程序，并内嵌 Dell 官方签名的
`BMC.msi`：

1. 每次启动先检测 `ipmitool.exe`。
2. 已安装时直接进入调速 GUI，不请求管理员权限。
3. 未安装时显示首次运行进度窗口，触发一次 Windows 管理员权限请求并静默安装。
4. 安装成功后自动进入同一个调速 GUI，无需再运行第二个程序。

自动安装日志位于：

```text
C:\ProgramData\R730xdFanConsole-<随机标识>\install-bmc.log
```

该目录只在缺少工具并执行自动安装时创建；安装载荷会先校验 SHA-256，再由提升后的
进程复制到管理员专用的随机暂存目录。

源码仓库不包含 Dell 安装包。自行构建 Windows 一体版前，请从合法来源取得与脚本
预期版本一致且签名有效的 `BMC.msi`，放到 `C:\OpenManage\BMC.msi`。未确认 Dell
再分发许可前，公开 Release 只提供源码和 Docker 包，不提供内嵌该 MSI 的 EXE。

默认配置：

- iDRAC：`192.168.5.151`
- 用户：`root`
- ipmitool：`D:\Program Files (x86)\Dell\SysMgt\bmc\ipmitool.exe`

密码需要在 GUI 中输入，也可通过当前进程的 `IPMI_PASSWORD` 环境变量提供。

## 测试

```bash
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s webapp/tests -p 'test_*.py' -v
```
