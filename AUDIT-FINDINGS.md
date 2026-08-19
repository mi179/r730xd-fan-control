# 全量审计发现（2026-08-20）

本文件是一次全量代码与文档审计的结论清单，依据与可复现验证见 `EVIDENCE.md` E-045。
每条发现带状态：**已修**（提交号）／**Open**（已转 TASKS）／**撤回**（复核后不成立）。

## 验证方式

- 通读全部治理文件（D-001..D-032 / E-001..E-044 / T-001..T-032）与全部源码、
  部署脚本、测试、CI；结论一律用代码和命令输出复核，不采信文档自述。
- 实测：桌面 95 项 unittest（Linux 跳过 13 跑 82）、Web 58 项、`ruff check .` 全绿、
  `scripts/verify_governance.py` OK；副本 HEAD `5fcaf58` 领先公开仓库 13 提交。

## 已验证为真的关键声明（抽查）

- 密码三不：`ipmitool -E` + 子进程环境变量 + `redact_and_limit`（两端一致）；
  `.dockerignore` 白名单阻止 secrets 进镜像；无 `innerHTML`/`eval`，XSS 面干净。
- D-020「公开路由零 IPMI 写」由 `PublicSurfaceInvariantTests` 行为锁定；
  `test_restore_auto_is_never_gated` 锁定恢复自动温控永不受联锁门禁。
- D-019 子网分桶、D-022 SIGSEGV partial、D-023 零 PowerMetrics、E-026 指纹固定
  在代码与测试中均有对应实现。
- 副本声明（REVIEW.md）全部属实：无 `webapp/secrets/`、无 `dist/`、版本单一来源同步。

## 发现清单

### F1（中，已修）STATUS.md Open risks 被最后提交推翻

`STATUS.md` 声称"没有任何机制阻止桌面产物落后源码再次发生"，但最后提交
`5fcaf58`（D-032/E-044）恰好加上了 `verify_governance.py` 的产物陈旧检查且
T-019 已 Done。STATUS 的 "Last updated" 未跟上。本次回填。

### F2（中，已修）webapp/README.md 自相矛盾

第 178 行"历史在内存中，重启容器后会重新积累"与同文件「遥测历史持久化」一节
及 D-015（SQLite 落盘）直接相反。D-015 上线时漏改的旧句，本次修正。

### F3（中，Open → T-038）IDRAC_HOST 默认值三处矛盾，共 7 处散落

| 值 | 位置 |
|---|---|
| `192.168.5.151` | `webapp/app.py:1580`、`webapp/compose.yaml:33`、根 `.env.example`、`webapp/templates/index.html:258` placeholder、`webapp/.env.example` openssl 注释 |
| `192.168.5.111` | `webapp/installer/install.sh:312`、`webapp/.env.example`、`installer/README.txt:15` |
| 空 | `r730xd_fan/config.py:12`（D-032 正确落地） |

真实地址 `.130`（E-031/STATUS）一个都不是。E-044 声称"文档核对并修正过期地址"只做了
`README.md:128` 一处点修。统一策略（留空还是必填）影响部署行为，需决策后动手。

### F4（中，Open → T-039）RMCP 探测/pong 校验三份实现

`r730xd_core/discovery.py`、`webapp/app.py:369-428`（Web 线不 import core）、
`scripts/discover_idrac_rmcp.py` 各一份。E-040 说发现逻辑"其余全部共用"与实现不符；
D-027 消除"两端各写一份"的初衷未在 discovery 上落地。

### F5（中，Open → T-040）SPEC 启动门禁弱于表述

SPEC：「`IDRAC_MAC` 与 `IDRAC_DISCOVERY_CIDR` 缺失时容器拒绝启动」。实现只在
compose `${VAR:?}` / install.sh / verify.sh 层；`app.py` 的
`_mac_discovery_from_environment()` 缺失时静默返回 None，此后身份核验直接关闭。
generic Linux 用户自配 compose 漏设 MAC 时，凭据发往未核验主机。

### F6（低，Open → T-040）D-005 缺少限定条件

"输错不消耗 iDRAC8 远程失败次数"只在 startup 凭据已配置分支成立（本地恒定时间比对）；
未配置分支（`app.py:1865-1889`）把表单密码直发 BMC `/redfish/v1/Managers` 真实认证，
输错照烧配额。线上恒为已配置，不触发。

### F7（低，已修）PLAN.md 仍写 `web-v0.4.0` tag

与 T-018（v0.4.0 被 0.4.1 取代、不单独发布）矛盾，已改为 `web-v0.4.1`。

### F8（低，已修）deploy_wrt.ps1 断言缺 README.txt

bundle 脚本断言 4 个 payload 文件（E-018），维护者路径只断言 3 个。已补齐
（提交 `96f77cd`）。

### F9（中，已修）CPU 读数卡借无关传感器冒充

`r730xd_fan/ipmi.py:125` 原兜底 `remaining[0]`：没有 "cpu" 命名的传感器时，把
剩余温度列表第一个（可能是 `DIMM A1 Temp`、`HDD Temp`）显示为"CPU 温度"，违反
D-025"绝不借用无关传感器凑数"。已收紧为只借用**处理器实体（3.x）的 `Temp`**
（Dell 对 CPU 二极管温度的真实命名），并补测试锁定 DIMM/HDD 不再冒充
（提交 `83198c1`，桌面测试 94→95 项）。

### F10（撤回）install.sh 地址校验前缀穿透——不成立

初判 `install.sh:350` 的 `grep -q "inet ${WEB_BIND_ADDRESS}/"` 会让 `.2` 匹配
`.20`。实测 `printf 'inet 192.168.5.20/24\n' | grep -q 'inet 192.168.5.2/'` **无
匹配**：模式自带 `/` 锚定，`.2/` 不是 `.20/` 的子串。判定撤回，install.sh 未改动。
记录在此是为了保留推理轨迹（治理纪律：复核推翻的结论不删除，标撤回）。

### F11（低，Open → 并入 T-038）compose.yaml 与 compose.offline.yaml 行为不一致

离线 compose 的 `IDRAC_HOST` 必填（`:?`），在线 compose 却带过期默认 `.151`，
无 MAC 时凭据可能被发往已证实失效的地址。

### F12（低，Open → T-041）enforce_same_origin 信任 Host 头

`app.py:1716-1733` 用 `request.host_url`（Host 头派生、攻击者可控）与 Origin 比较；
非浏览器客户端伪造 Host 头可穿过同源检查。浏览器场景有 `Sec-Fetch-Site`/cookie 域
兜底，建议加 Host 白名单。

### F13（低，Open → T-041）桌面 UI 线程跑阻塞命令

`ui.py:357` `scan_range()` 内含 PowerShell（timeout 15 s）、`ui.py:1391`
`read_arp_table()` 跑 `arp -a`（10 s），都在 Tk 主线程，与"所有命令在后台线程运行"
的 README 承诺不完全一致。

### F14（提示级）verify.sh 不直接检查 IDRAC_MAC

`verify.sh:28-29` 只查 `.env` 里的 CIDR；compose config 的 `:?` 间接兜底。不单独处理。

### F15（提示级，已修）README.txt 两个"4."编号

已修正为 4./5.（提交 `3563f9c`）。

## 修复提交

| 提交 | 内容 | 关联 |
|---|---|---|
| `83198c1` | CPU 槽位兜底收紧 + 测试 | D-025, T-034 |
| `3563f9c` | README.txt 编号修正 | T-036 |
| `96f77cd` | deploy_wrt.ps1 断言补齐 | D-013, T-037 |
| 治理文档回填提交 | STATUS / PLAN / webapp README / DESKTOP-USAGE / TASKS / EVIDENCE E-045 / 本文件 | T-033, T-035 |

未修项一律在 `TASKS.md`（T-038..T-041），本文件不重复展开。
