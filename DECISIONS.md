# DECISIONS

每条决策注明状态。被推翻的决策标记 **Superseded by D-00N** 并说明原因，不删除——推理轨迹本身就是价值。

由于本项目在 2026-07-28 才引入治理文件，D-001..D-010 是从代码、README 与 webapp 文档中**回溯重建**的既有决策，依据见各自 Evidence 列。

| ID | 决策 | 理由 | 状态 | Evidence |
|---|---|---|---|---|
| D-001 | 双形态交付：Windows GUI + OpenWrt Docker Web | 桌面场景要完整控制；手机/局域网场景要随时看温度，两者共享同一套 iDRAC 凭据模型 | Active | E-005 |
| D-002 | 桌面版密码只保存在 GUI 进程内存，不写入任何项目文件 | 防止凭据随仓库或配置文件泄漏 | Active | E-006 |
| D-003 | 调用 `ipmitool -E`，密码经子进程环境变量传递 | 密码不出现在进程参数列表（`ps`/任务管理器可见）和 UI 日志里 | Active | E-007 |
| D-004 | 固定转速前必须解除安全联锁并成功关闭自动温控 | 防止误操作削弱服务器自我保护；“恢复自动温控”始终可用作兜底 | Active | E-008 |
| D-005 | Web 版无第二套 Web 密码，控制区直接用 iDRAC 凭据解锁 | 减少凭据数量；后端本地恒定时间比对，输错不消耗 iDRAC8 远程失败次数 | Active | E-009 |
| D-006 | Web 版 iDRAC 密码存宿主机 `secrets/idrac_password`，只读 Docker secret 挂载 | `.env` 易被误提交、镜像层会留存明文；secret 文件 `chmod 400` 且不复制进镜像 | Active | E-010 |
| D-007 | iDRAC 用 DHCP 时以网卡 MAC 为稳定身份；`IDRAC_MAC`、`IDRAC_DISCOVERY_CIDR` 缺失则容器拒绝启动 | 旧 IP 失效后需要可靠重新定位；宁可启动失败也不静默退回未核验的固定 IP | Active | E-011 |
| D-008 | 容器用独立 Docker 网络 + 独立默认拒绝防火墙区，固定容器 MAC，只放行 TCP 443 / UDP 623 / LAN→8080 | 把 `/24` 探测权限限定在单一容器身份，普通其他 Docker 容器无法冒用 | Active | E-012 |
| D-009 | 公开 Release 只提供源码和 Docker 包，不提供内嵌 `BMC.msi` 的 EXE | 未确认 Dell 再分发许可；源码构建者自行从合法来源取得签名一致的 MSI | Active | E-013 |
| D-010 | 开发在 WSL（代码、测试、Git），GUI 在 Windows Python 运行 | CustomTkinter GUI 需要直接调用 Dell Windows 版 `ipmitool.exe` | Active | E-014 |
| D-011 | Web 版接受局域网明文 HTTP | 公开遥测无凭据风险；控制区密码只在可信 LAN 内传输；跨不可信网络时要求前置 HTTPS 反代并设 `WEB_COOKIE_SECURE=true` | Active | E-015 |
| D-012 | 引入 ax3000t 式治理骨架（本套文件 + 证据纪律 + 每次改动即提交） | 项目此前只有一次 Git 提交，过程记忆为零，新会话无法接手 | Active | E-001, E-002 |
| D-013 | 桌面与 Web 是两条产品线，各自独立语义化版本，不强行统一。单一事实来源：桌面 = `pyproject.toml`（`r730xd_fan/__init__.py` 同步）；Web = `webapp/installer/VERSION`。Git tag 格式 `desktop-vX.Y.Z` / `web-vX.Y.Z`，发布说明必须注明产品线 | 两者发布节奏不同（Web 0.3.1 已发布时桌面已演进到 0.4.0），强行统一会制造无意义的版本跳跃；真正要消除的不是数字差异而是"哪个数字是真的"的不确定性 | Active | E-003, E-018 |
