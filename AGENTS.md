# Project rules

用中文解释，保留英文 UI 标签、命令和技术术语。动手前先读治理文件——阅读顺序见
[HANDOFF.md](HANDOFF.md)。不要对着模糊需求直接规划或实现：先弄清用户拿成品做什么，
再确认范围和验收标准。重要的外部原始资料（如 Dell 官方手册 PDF）保留原件，
摘要永远不能替代原件。构建工作与测试、验收分开记录。

## Operating rules learned the hard way

这些规则大多继承自隔壁 ax3000t 项目用失败换来的教训（同一台宿主、同一套工具链），
以及本项目代码里已经固化的安全设计。不要重新踩一遍。

- **每次有意义的改动都单独提交。** 本项目到 2026-07-28 为止只有一次 Publish 提交
  （E-001），过程记忆为零——治理文件只有在历史存在时才是持久记忆（D-012）。提交
  信息带相关 D/E/T 编号。
- **密码三不，没有例外**：iDRAC 密码不落盘（桌面版只存进程内存）、不进进程参数
  （必须走 `ipmitool -E`）、不进日志和聊天。Web 版只经 `secrets/idrac_password`
  Docker secret 传递。任何“调试方便一下”把密码打进日志的改动都是红线。
- **`webapp/secrets/`、`.env`、产物目录已被 `.gitignore` 拦截，保持如此。**
  本机 `webapp/secrets/idrac_password` 是真实凭据。
- **任何含非 ASCII 字符的 `.ps1` 必须存为 UTF-8 with BOM。** 这台宿主的 Windows
  PowerShell 5.1 会把无 BOM 脚本按 GBK 解码，报错行号还会指错地方（ax3000t D-009）。
  本项目的治理校验脚本因此用 Python 写（`scripts/verify_governance.py`），绕开这个坑。
- **产物（EXE、tar.gz）不进 Git。** 在 EVIDENCE.md 记录路径 + SHA-256（格式见 E-004）。
- **调速命令是有物理后果的写操作。** 安装器、测试脚本默认不得发送任何风扇调速
  命令；live 测试只做无损验证（`live_smoke.py` 的文件头注释就是契约）。
- **恢复自动温控永远保持可用。** 任何 UI 重构不得把“恢复自动温控”藏到联锁之后。

## Evidence discipline

结论要进 EVIDENCE.md 才算成立：一行记录，指向文件路径（带行号或函数名更佳）或
可复现的命令输出；大文件记 SHA-256。决策被推翻时，旧行标 **Superseded by D-00N**，
不编辑不删除——推理轨迹就是意义所在。
