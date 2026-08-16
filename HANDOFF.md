# HANDOFF

如何在一个全新会话里接手本项目。本文件**刻意不复述当前进度**——进度在 STATUS.md，
写在这里会腐烂。保持这样。

## Read in this order

1. `PROJECT.md` —— 做什么、为什么
2. `SPEC.md` —— 范围、非目标、硬安全边界
3. `DECISIONS.md` —— 每个判断及其理由；依赖某条决策前先看 Status 列有没有 Superseded
4. `STATUS.md` —— 现在到哪了、下一步是什么
5. `PLAN.md` —— 阶段门禁
6. `TASKS.md` —— 未结事项
7. `EVIDENCE.md` —— 以上所有结论背后的证据
8. `README.md` / `webapp/README.md` —— 面向用户的操作文档（最详细的使用说明在这里）

## The one-line version

**Windows GUI v0.4.0 和 OpenWrt Docker Web 版 v0.4.1 都已发布可用**；Web 版跑在
`192.168.5.2:8088`（E-033）。两条产品线各自独立版本（D-013）。CI（T-002）与版本号
策略（T-003）已于 2026-07-28 完成。

## Environment

- 宿主 Windows 11；开发在 WSL Ubuntu（`/mnt/d/UserData/Documents/r730xd_fan`），
  GUI 用 `.venv-win` 的 Windows Python 运行，测试在 WSL 用 `.venv-wsl` 跑。
- iDRAC 走 DHCP，**地址不是稳定身份**——代码按 MAC `d0:94:66:8c:e0:e3` 重发现；
  2026-08-16 ARP 实测为 `192.168.5.130`（此前文档记的 `192.168.5.151` 已失效）。
  WRT 宿主 `root@192.168.5.2`，部署目录 `/opt/r730xd-fan-web`。
- 远程仓库只有一个：`https://github.com/mi179/r730xd-fan-control`。
- Dell 官方手册 PDF 在仓库外：`D:\UserData\Documents\r730xd-ompublication-zh-cn.pdf`。

## Things that will bite you immediately on resuming

- **不要在 WSL 里启动 GUI**——`config.py` 按 `os.name` 分两套 ipmitool 候选路径，
  WSL 候选指向 `/mnt/d/...` 的 Dell 安装，GUI 必须走 `.venv-win`。
- **改 `webapp` 部署相关代码前**，先读 `webapp/README.md` 的“升级与回滚”：升级前
  `docker image tag ... :rollback`，且**先在界面恢复自动温控**。
- **一体 EXE 的 BMC.msi 不进仓库也不在公开 Release**（D-009）。自行构建需要把签名
  一致的 `BMC.msi` 放到 `C:\OpenManage\BMC.msi`，SHA-256 硬编码在
  `r730xd_fan/dependency.py` 的 `BMC_MSI_SHA256`。
- **防火墙规则里的 `src_mac='02:73:0d:73:00:01'` 是 Compose 给容器固定的 MAC**，
  改 Compose 的 `mac_address` 而不改防火墙规则会直接把 iDRAC 链路掐断。

## Tests

```bash
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s webapp/tests -p 'test_*.py' -v
```

两套都要跑。`webapp/tests/live_*.py` 针对真实部署实例，默认不发送调速命令。

## Verify the project is intact

```bash
python3 scripts/verify_governance.py
```

检查治理文件存在、EVIDENCE 非空、无凭据文件被 Git 跟踪、Git 历史存在、
含非 ASCII 的 `.ps1` 带 BOM。提交前运行。

## Current handoff — 2026-08-16 交给 Claude

以下内容是本轮的具体现场。它补充上面的长期接手说明；当前进度仍以 `STATUS.md`、
`TASKS.md`、`EVIDENCE.md` 为正式记录。

### 1. 当前目标

用户正在修正 Web v0.4.0 的传感器与功耗显示。用户真正需要的结果是：

1. 常规 Redfish 遥测和风扇控制继续可用，不能因为修传感器页面破坏安全联锁。
2. iDRAC8 上完整 SDR 遍历会崩溃时，保留真实 partial 数据并明确说明不完整。
3. 功耗页不能把 iDRAC 返回的无效零值显示成真实的平均、最小和最大功耗；应使用应用
   自己已经写入 SQLite 的历史功耗计算。
4. 完整扫描的温度、风扇、功耗、电压、电流、系统和其他分类都要核对，不能用错误分类
   掩盖“partial 结果里根本没有返回该类记录”。

用户最后明确说“停先停先停到这里，然后写交接文档，我交给 Claude”。因此本轮已经停止，
不得把下面尚未落地的功耗修复描述为已完成。

### 2. 已完成并已提交

工作区在写本交接前是 clean；本地 `main` 相对 `origin/main` **ahead 8**，尚未 push。

最近两个提交：

```text
86ed8eb 治理：记录 Web v0.4.0 部署与 partial SDR 边界 (D-022, E-030..E-031, T-010)
78eeef8 Web SDR：SIGSEGV 时保留并标记部分结果 (T-011)
```

- `D:\UserData\Documents\r730xd_fan\webapp\app.py`，`Backend._run_deep_scan()`：只在
  `ipmitool` 以 `SIGSEGV` 返回 `-11` 或 `139`、且 stdout 至少解析出一条记录时接受
  partial 结果。其他非零退出和空输出仍失败。
- `D:\UserData\Documents\r730xd_fan\webapp\static\app.js`，
  `acceptSensorRecords()` / `renderSensorRows()`：显示 partial 警告。
- `D:\UserData\Documents\r730xd_fan\webapp\tests\test_app.py`，三个
  `test_deep_scan_*` 用例：覆盖 `-11`、`139`、其他非零退出和空结果。
- `D:\UserData\Documents\r730xd_fan\DECISIONS.md`：D-022 已记录 partial 策略。
- `D:\UserData\Documents\r730xd_fan\EVIDENCE.md`：E-030 记录 v0.4.0 构建/部署，
  E-031 记录真机 `SIGSEGV` 和 83 条 partial 结果。
- `D:\UserData\Documents\r730xd_fan\STATUS.md`、`TASKS.md`、`PLAN.md`、
  `SPEC.md`、`webapp\README.md` 已同步当前部署和 T-014/T-015。

Web v0.4.0 已正式部署到 WRT：

```text
URL: http://192.168.5.2:8088
Image: sha256:fd1db7184f88ba9fb6897ad47b7e180330c6308671c90a2aac1746259979acca
Configured image: r730xd-fan-web:0.4.0
Health: healthy
```

产物不进 Git，当前文件与哈希为：

```text
D:\UserData\Documents\r730xd_fan\dist\docker\r730xd-fan-web-0.4.0-linux-amd64.tar.gz
23,497,775 bytes
SHA-256 6838FF1AEE873615490C0DA50E1EC0D270432B0EE2F71C6B8520BAFEEF556137

D:\UserData\Documents\r730xd_fan\dist\docker\R730xdFan-Web-Docker-v0.4.0.tar.gz
23,428,725 bytes
SHA-256 396369075017D94003D15ABC26B187FA6C9D05030C8F8DC2270D9F0412A6CEFE
```

部署验收已经通过：`verify.sh` 输出 `VERIFY OK`；SQLite 文件属主为 UID/GID 10001；
三条 nftables 项目规则存在；`live_smoke.py` 输出 `LIVE_SMOKE_OK`；Chrome 页面无横向
溢出、无 console error。安装器没有发送任何 fan-control command。

### 3. 当前未解决问题与准确诊断

用户截图文件：

```text
D:\SystemCache\Temp\codex-clipboard-9ec82fa9-2dfc-400c-a3f3-6e400c3e8c06.png
```

截图表现：功耗页“实时”为约 134 W，但“平均、最小、最大”为 0 W；完整扫描选择“功耗”
后显示 0 / 83 条。

真机只读 Redfish 请求已经确认，当前代码的字段路径没有写错；iDRAC8 firmware 2.70
本身返回了以下数据：

```json
{
  "PowerConsumedWatts": 135,
  "PowerAllocatedWatts": 896,
  "PowerCapacityWatts": 896,
  "PowerMetrics": {
    "AverageConsumedWatts": 0,
    "IntervalInMin": 1,
    "MaxConsumedWatts": 0,
    "MinConsumedWatts": 0
  }
}
```

所以平均/最小/最大为 0 的根因是 BMC 提供了明显无效的 `PowerMetrics`，不是
`webapp/app.py::_parse_redfish_telemetry()` 取错字段。

应用自己的历史数据正常。一次 `GET /api/telemetry/history?range=5m` 实测有 15 个功耗
样本，最小 131 W、最大 138 W、平均 133.8 W、最后一个样本 135 W。正确方向是用当前
趋势区间的历史样本计算平均/最小/最大，并把 BMC 的“实时值为正但三个 metrics 全为 0”
判定为 unavailable，而不是显示为真实 0 W。

完整 SDR 当前线上状态：

```json
{
  "status": "complete",
  "records": 83,
  "partial": true,
  "partial_reason": "ipmitool_sigsegv"
}
```

83 条记录的后端分类为：

```text
temperature: 4
fan: 7
system: 72
power: 0
voltage: 0
current: 0
```

分类核对结论：现有 83 条中的 4 条温度、7 条风扇和 72 条系统分类合理。`Fan Redundancy`
属于 fan；`PS1` / `PS2` FRU、`PS1 PG Fail`、`3.3V PG` 等是状态/Power Good 信号，属于
system，而不是功耗或真实电压测量。真机 83 条的 name/reading 中没有任何 `power`、
`watt`、`voltage`、`current`、`amp` 记录。功耗、电压、电流为 0 是因为 `ipmitool` 在走到
这些记录前已经崩溃，不是前端筛选器漏判。

Redfish `/Power` 还能提供两路 PSU 的 `LineInputVoltage=234`，但 `PowerSupplies` 中的
`PowerInputWatts=900` 是额定/容量性质的数据，不能冒充实时功耗。Redfish `Voltages`
大量 `ReadingVolts=1` 实际对应 PG 布尔状态，也不能当作 1 V 真实电压展示。

容器重启后软件控制状态按设计为：

```json
{"mode":"unknown","percent":null,"safety_unlocked":false}
```

安装器没有改变物理风扇模式。此前用户说明风扇控制本身正常。

### 4. 当前停点和原始错误

曾准备修改以下文件，但 `apply_patch` 因定位不到函数名而整体失败，**没有任何部分修改
落地**：

```text
D:\UserData\Documents\r730xd_fan\webapp\app.py
D:\UserData\Documents\r730xd_fan\webapp\tests\test_app.py
D:\UserData\Documents\r730xd_fan\webapp\static\app.js
D:\UserData\Documents\r730xd_fan\webapp\templates\index.html
D:\UserData\Documents\r730xd_fan\webapp\static\app.css
```

原始错误：

```text
apply_patch verification failed: Failed to find expected lines in D:\UserData\Documents\r730xd_fan\webapp\app.py:
def _telemetry_history_sample(telemetry: Mapping[str, Any]) -> dict[str, Any]:
```

失败原因是实际函数名/位置没有在提交补丁前重新精确读取。接手后必须先用 `rg` 找到生成
history sample 的真实函数名，再小块 patch；不要直接重放失败的大补丁。

写本交接文档前执行的 `git status --short --branch` 为：

```text
## main...origin/main [ahead 8]
```

除本次按用户要求修改 `HANDOFF.md` 外，没有未提交代码改动。

### 5. 已否决的方案及理由

1. 不接受“只要非零退出但有几行 stdout 就算 partial”。这会掩盖认证或链路错误。当前
   D-022 已收紧为只接受 `SIGSEGV -11/139` 且至少一条有效记录。
2. 不再反复触发 `sdr elist all` 做验证。真机 `ipmitool 1.8.19` 会段错误，重复运行曾导致
   `insufficient resources for session`，说明会暂时耗尽 iDRAC IPMI session。线上 partial
   修复只做过一次受控真机扫描。
3. 不把所有包含电源含义的名称都归到 power。`Power Good`、PSU presence/status 和 FRU 是
   系统状态；power 应保留给 Watt 数值或明确的 power-consumption measurement。
4. 不把 Redfish `Voltages` 中 PG 传感器的 `ReadingVolts=1` 当作真实 1 V，也不从总功耗和
   线电压推导电流；这种合成值缺少 PSU 分配和效率信息，会制造假精度。
5. 不使用 iDRAC 返回的全零 `PowerMetrics` 作为历史统计。应用 SQLite 已有真实实时功耗
   样本，应以所选趋势区间的样本计算。
6. 不把功耗/电压/电流缺失说成分类器 bug；当前证据证明这些记录根本没有出现在 83 条
   partial stdout 中。UI 应明确显示“当前 partial 未返回”，不能假装完整。
7. 不在没有用户明确授权时 push、创建 `web-v0.4.0` tag 或 GitHub Release。T-015 仍 Open。
8. 不把密码写进命令参数、日志、聊天或仓库。iDRAC 密码只允许从
   `webapp/secrets/idrac_password` / Docker secret 读取。

### 6. 下一步建议

接手后的第一件事是重新精确定位函数，再分别做小改动和测试：

1. 在 `D:\UserData\Documents\r730xd_fan\webapp\app.py` 的
   `_parse_redfish_telemetry()` 中识别不可能的零指标：当 `PowerConsumedWatts > 0` 且
   Average/Min/Max 三个值全部存在并全部为 0 时，不向公开 telemetry 输出这三个字段。
   实时、已分配和容量字段保持不变。
2. 找到真实的 history sample helper，并新增纯函数统计 `power_watts` 的 sample count、
   average、minimum、maximum。把统计结果加入 `GET /api/telemetry/history`，统计范围应与
   API 返回的当前趋势区间 samples 一致。长区间 samples 已经 SQL downsample，所以 UI 文案
   应写“按当前趋势区间的图表样本计算”，不要暗示是原始逐点极值。
3. 在 `webapp/tests/test_app.py` 增加：iDRAC 不可能的全零 metrics 被忽略；历史统计忽略
   null；5 分钟样本统计正确；分类代表用例覆盖温度、风扇、Watt、Volt、Amp、PSU FRU、
   PG 状态。
4. 在 `webapp/static/app.js` 保存最近 power 对象和 history power statistics；
   `renderHistory()` 后刷新功耗 detail。`webapp/templates/index.html` 应明确平均/最小/最大
   来自当前趋势区间，并显示样本数。
5. 完整扫描的 type select 应显示每类 count。若 `state.sensorsPartial` 且所选类别为 0，
   empty row 应显示“当前部分结果未包含功耗/电压/电流记录”，而不是泛化的“没有匹配”。
6. 不建议把 Redfish power/voltage 合成记录混入 SDR 表，除非另立决策并在 UI 明确 source；
   当前更诚实的做法是修复顶部历史统计，同时准确说明 partial SDR 的缺项。
7. 验证通过后单独提交代码；再重建 WRT image/离线包、正式 installer 部署、只读验收，最后
   更新 EVIDENCE/STATUS/TASKS。不得复用旧 v0.4.0 包冒充包含新修复。

### 7. 可直接运行的命令

项目根目录：

```powershell
Set-Location 'D:\UserData\Documents\r730xd_fan'
```

定位代码：

```powershell
rg -n -C 8 "PowerMetrics|PowerConsumedWatts|power_watts|telemetry/history|renderPowerDetail|renderHistory|inferSensorType|renderSensorRows" webapp\app.py webapp\static\app.js webapp\tests\test_app.py
```

完整验证：

```powershell
.\.venv-win\Scripts\python.exe -m ruff check .
.\.venv-win\Scripts\python.exe scripts\verify_governance.py
.\.venv-win\Scripts\python.exe -m unittest discover -s tests -v
.\.venv-win\Scripts\python.exe -m unittest discover -s webapp\tests -p 'test_*.py' -v
git diff --check
```

无损线上 smoke test；它会登录、读 protected config 后退出，但不会发送风扇控制命令：

```powershell
.\.venv-win\Scripts\python.exe webapp\tests\live_smoke.py
```

只读线上状态：

```powershell
Invoke-RestMethod 'http://192.168.5.2:8088/healthz'
Invoke-RestMethod 'http://192.168.5.2:8088/api/status'
Invoke-RestMethod 'http://192.168.5.2:8088/api/telemetry/history?range=5m'
Invoke-RestMethod 'http://192.168.5.2:8088/api/sensors/deep-scan'
```

SSH audit key 可用，不需要 SSH 密码：

```powershell
ssh -i "$env:USERPROFILE\.ssh\r730_audit_ed25519" -o BatchMode=yes root@192.168.5.2 "docker compose -f /opt/r730xd-fan-web/compose.yaml ps"
```

重新打离线包时使用：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_openwrt_bundle.ps1 -ImageArchivePath .\dist\docker\r730xd-fan-web-0.4.0-linux-amd64.tar.gz -OutputDirectory .\dist\docker
```

正式部署前必须重新阅读 `webapp/README.md` 的“升级与回滚”。安装器调用形式为：

```sh
cd /tmp/<new-unique-deploy-dir>/R730xdFan-Web-Docker-v0.4.0
sh install.sh --non-interactive
```

不要直接复制上面的 `<new-unique-deploy-dir>` 占位符；每次创建新的明确目录。installer 会
沿用现有 `.env` 和 Docker secret，不需要把密码传进参数。部署会重启 Web 容器，但不会
改变物理风扇模式；升级前仍应优先恢复自动温控，除非用户明确选择保持当前物理模式。

### 8. 环境、偏好和禁区

- 所有解释使用中文，但 UI label、命令、technical term 和 error 原文保留 English，并在旁边
  给中文解释。用户希望借此熟悉英文文档和报错。
- LAN-only 风险接受仍有效：T-012 Redfish certificate fingerprint 和 T-013 HTTPS reverse
  proxy 是 backlog，不阻塞内网上线；E-029 的复议触发条件不变。
- 任何风扇调速、手动/自动模式切换都是有物理后果的写操作。不得因测试或部署擅自发送。
- “恢复 iDRAC 自动温控”必须始终可用，不能被安全联锁隐藏。
- `D:\UserData\Documents\r730xd_fan\webapp\secrets\idrac_password` 是真实凭据，已被
  `.gitignore` 拦截。不得读取后输出、提交或复制到构建上下文。
- OpenWrt shell 文件必须 LF；非 ASCII PowerShell `.ps1` 必须 UTF-8 with BOM。
- EXE、Docker tar.gz 等产物不进 Git；哈希写入 `EVIDENCE.md`。
- 当前 iDRAC 使用 DHCP，MAC `d0:94:66:8c:e0:e3` 在 2026-08-16 ARP 表对应
  `192.168.5.130`。地址不是稳定身份，代码必须继续按 MAC 发现。

### 9. 临时文件残留

本轮构建产生的以下 WRT `/tmp` 文件仍在 tmpfs。一次自动清理命令被安全策略拦截，未删除
任何内容。合计约 68 MB，`/tmp` 当时仍有约 857 MB 可用，WRT 重启后会自动消失：

```text
/tmp/r730xd-build-78eeef8
/tmp/r730xd-deploy-78eeef8
/tmp/r730xd-fan-web-78eeef8.tar
/tmp/r730xd-fan-web-0.4.0-78eeef8-linux-amd64.tar.gz
```

若要手工清理，先逐项执行 `readlink -f` 并确认解析结果与上面四个绝对路径完全一致，再做
删除；不要对 `/tmp`、`/opt` 或其他宽目录做递归删除。

本地还有一个约 389 KB 的临时源码归档，可保留或在核对绝对路径后删除：

```text
D:\SystemCache\Temp\r730xd-fan-web-78eeef8-5e32dae174724df38d11c4da1d5574e9.tar
```
