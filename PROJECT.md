# PROJECT: Dell R730xd iDRAC 风扇控制台

## Purpose

为 Dell PowerEdge R730xd 提供一套安全、可审计的 iDRAC 风扇调速工具，包含两种交付形态：

1. **Windows 桌面版** —— CustomTkinter GUI，直接调用 Dell `ipmitool.exe`，可打包为内嵌依赖自动安装的一体 EXE。
2. **OpenWrt Docker Web 版** —— 部署在 WRT 上的 Flask 控制台，局域网访客免密看遥测，调速操作需 iDRAC 凭据解锁。

核心约束：调速能力必须建立在明确的安全联锁之上，密码永远不落盘、不进命令行参数、不进日志。

## Success criteria

- 新会话只靠仓库内文件就能恢复项目目的、决策、状态和下一步。
- 每次有意义的改动都有对应的 Git 提交。
- 发布产物（EXE、Docker 离线包）带 SHA-256 校验，且校验值记录在案。
- “恢复自动温控”在任何路径下都始终可用。
