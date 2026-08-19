# TASKS

| ID | 任务 | 状态 | 关联 |
|---|---|---|---|
| T-001 | 每次有意义的改动单独提交，提交信息带 D/E/T 编号 | 长期规则 | D-012 |
| T-002 | 补最小 CI workflow：桌面 + Web 两套 unittest，加 ruff | **Done 2026-07-28**（`.github/workflows/ci.yml`，含治理自检；首次运行三 job 全绿，含 windows runner 上的 GUI 测试，E-017） | E-002, E-016, E-017 |
| T-003 | 明确桌面版/Web 版版本号策略（统一还是各自独立），写进 README | **Done 2026-07-28**（独立版本 + 单一来源 + tag 规范，D-013；build 脚本已断言一致性，E-018） | E-003, E-018 |
| T-004 | 发布流程固化：发布前跑两套测试 + 记录产物 SHA-256 到 EVIDENCE.md | **Done 2026-08-16**（Web v0.4.0 首次完整走通：测试 → WRT 原生构建 → 离线包 → SHA-256 → installer → verify/live smoke，E-030） | E-004, E-030 |
| T-005 | 写 HTTPS 反向代理部署文档（Caddy/Nginx 前置 + `WEB_COOKIE_SECURE=true`） | **Done 2026-08-15**（`webapp/README.md`「凭据传输」节，含 Caddy/nginx 两版与 `TRUSTED_ORIGINS`） | D-011 |
| T-006 | 为 `webapp/tests/` 的 live 脚本（live_smoke / live_idrac_readonly / inspect_idrac_network）补一段使用说明 | Open；同目录的 `dev_preview.py` 已于 2026-08-17 在 `webapp/README.md` 有说明，可作为格式参考 | E-016 |
| T-007 | 治理文件引入 | **Done 2026-07-28** | D-012 |
| T-008 | 两端前端统一为近黑单色设计语言，删除装饰性元素 | **Done 2026-08-15**（D-014，E-020） | D-014 |
| T-009 | Web 版遥测历史 SQLite 持久化 + 时间区间查询 | **Done 2026-08-15**（D-015，E-021..E-022） | D-015 |
| T-011 | 完整 SDR 对匿名开放 + 触发冷却；传感器中文注解；等宽字体收敛 | **Done 2026-08-16**（另补 iDRAC8 真机 `SIGSEGV` 部分结果兼容，D-022 / E-031） | D-016, D-017, D-022 |
| T-012 | 在 WRT 上取 iDRAC 证书指纹并填入 `.env` 的 `REDFISH_TLS_FINGERPRINT`（关闭 Redfish 凭据 MITM 路径） | Backlog（D-021 接受风险，不阻塞上线）—— 代价极低：一条 openssl 命令 + 重启 | D-018, D-021 |
| T-013 | 部署 HTTPS 反代并设 `WEB_COOKIE_SECURE=true` / `TRUSTED_ORIGINS`；同时处理反代后 `REMOTE_ADDR` 塌缩导致限速退化为全局桶的问题（ProxyFix 或共享网络命名空间） | Backlog（D-021 接受风险，不阻塞上线） | D-011, D-019, D-021 |
| T-010 | 部署 Web v0.4.0 到 WRT：镜像在 WRT 上原生构建（Windows 侧已无 Docker daemon），`docker save` 回传后仍走 `build_openwrt_bundle.ps1` + `install.sh` 正规流程，保留备份/哈希/回滚/verify | **Done 2026-08-16**（image `fd1db718…`, installer / verify / live smoke 全通过，E-030） | D-013, D-015, E-030 |
| T-014 | 找到不会触发 iDRAC8 session 泄漏的**完整** SDR 读取路径：评估不同 `ipmitool` 版本、FreeIPMI 或分段读取；真机验证前先设计会话回收与重试上限 | Open；当前 D-022 只保留 83 条部分结果，不能宣称完整扫描可用 | D-022, E-031 |
| T-016 | 功耗页不得把 iDRAC 的无效全零 `PowerMetrics` 显示为真实 0 W；改用本地历史样本统计并标明区间与样本数；完整扫描分类计数与 partial 缺项说明 | **Done 2026-08-16**（D-023, E-032）。注：提交 `1c2e7f1` 的信息里把本项误标为 T-014，实际 T-014 仍 Open | D-023, E-032 |
| T-015 | 将本地提交 push 到 `origin/main` | **Done 2026-08-16**（用户明确授权 push） | D-013, E-033 |
| T-018 | 创建 `web-v0.4.1` tag 与 GitHub Release，上传 E-033 的离线包和 `.sha256` | Open；用户 2026-08-16 明确本轮只 push、不对外发布。注：v0.4.0 已被 v0.4.1 取代，不单独发布。2026-08-18 已把 README「Docker 离线一键安装」一节改为如实说明「公开 Release 仅 web-v0.3.1」——在此之前 README 指名让用户下载 Releases 里并不存在的 v0.4.1 离线包 | D-013, E-033 |
| T-017 | WRT overlay 回收策略：`docker system df` 实测 130.7 MB（59%）可回收。需决定保留几个历史镜像（当前 0.4.1 / 0.4.0 / 0.3.1 三个不同 image），以及部署后是否清理构建拉取的基础镜像层。**方向已更正**：删 rollback 标签几乎不释放空间（E-035 更正 E-034），真正占用来自「在目标机构建」必然拉取的基础镜像 | Open | E-034, E-035 |
| T-019 | 防止桌面「源码已改、dist 里还是旧 EXE、版本号却相同」再次发生 | **Done 2026-08-20**（D-032, E-044）：`verify_governance.py` 比对 `dist/*.exe` 与桌面源码的时间戳。此前只记录未实现，问题因此在一天内复发一次 | D-013, D-024, E-036, D-032 |
| T-020 | 桌面版对齐 Web 外观 + 三温度一功耗读数卡，中文化，不做趋势图 | **Done 2026-08-19**（D-025, E-037；EXE 已重建） | D-025, E-037 |
| T-021 | 简化部署：维护者路径与发布路径分离（`deploy_wrt.ps1`）、升级免提问、自动 verify、`/tmp` 用完即删 | **Done 2026-08-19**（D-026, E-038）——但只做了静态验收 | D-026, E-038 |
| T-022 | 在真机 `192.168.5.2` 上跑一轮 `deploy_wrt.ps1` 验收：源码上传 → WRT 构建 → `--use-local-image` 安装 → 自动 verify → `/tmp` 已清空；再故意让 verify 失败一次，确认回滚真的触发 | Open；这是对生产路由器的写操作，需业主明确授权后执行 | D-026, E-038 |
| T-023 | 抽出 `r730xd_core/`：协议常量、SDR 解析分类、输出脱敏三块两端共用；不合并 runner | **Done 2026-08-19**（D-027, E-039） | D-027, E-039 |
| T-024 | 把 `webapp/tests/` 里的 4 个非测试脚本（`live_smoke` / `live_idrac_readonly` / `inspect_idrac_network` / `dev_preview`）挪到 `tools/live/`。现在拦着 `live_smoke.py` 不被 CI 对真机执行的只是一个 `-p "test_*.py"` 参数，一个文件名就能捅穿 | Open；顺带可关闭 T-006 | E-016 |
| T-025 | 桌面 `ui.py` 拆成 `view/cards.py` + `dialogs.py` + `window.py` | Open；纯文件分割，随时可做，刻意排在 core 抽取之后以免文件搬两次家 | D-025 |
| T-026 | 桌面版补 iDRAC 自动发现（扫描 + 按 MAC 重定位），消除与 Web 版的功能差 | **Done 2026-08-19**（D-028, E-040） | D-028, E-040 |
| T-027 | `HANDOFF.md:27` 与 `EVIDENCE.md:41` 里写有业主真实 iDRAC MAC，且已推送到公开仓库。MAC 不是凭据、且只在同网段有意义，风险低，但属于环境标识不该进仓库（`.env.example` 用占位符是对的）。D-028 之后任何人都能自己扫描发现，仓库里已无保留必要 | Open；改写历史代价高，建议只做"往后不再新增"，由业主决定是否清理 | D-028 |
| T-028 | 普通 Linux + Docker 部署文档，并把构建上下文改成仓库根让构建命令可照抄 | **Done 2026-08-19**（D-029, E-041；真机 docker build 验证） | D-029, E-041 |
| T-029 | 桌面界面自动折叠：窗口变窄先并读数卡、再堆叠两栏，变矮收起事件日志 | **Done 2026-08-19**（D-030, E-042） | D-030, E-042 |
| T-030 | 扫描范围读真实子网掩码，不再假设 /24；上限提到 512 让 /23 可完整扫描 | **Done 2026-08-19**（D-031, E-043） | D-031, E-043 |
| T-031 | `install.sh` 把 OpenWrt 判定挪到工具检查之前。现在普通 Linux 用户先撞到第 220 行的 `uci is required`，会以为自己少装了个包，而不是看到第 225 行那句"只支持 OpenWrt"。检查顺序反了，报错把人引向错误方向 | Open；一处挪动，见 docs/DOCKER-GENERIC.md | D-029 |
| T-032 | 桌面默认 iDRAC 地址改为空，配合 D-028 的扫描；README 同步 | **Done 2026-08-20**（D-032, E-044） | D-028, D-032 |

关闭任务时把状态改为 Done + 日期，不删除行。
