# TASKS

| ID | 任务 | 状态 | 关联 |
|---|---|---|---|
| T-001 | 每次有意义的改动单独提交，提交信息带 D/E/T 编号 | 长期规则 | D-012 |
| T-002 | 补最小 CI workflow：桌面 + Web 两套 unittest，加 ruff | **Done 2026-07-28**（`.github/workflows/ci.yml`，含治理自检；首次运行三 job 全绿，含 windows runner 上的 GUI 测试，E-017） | E-002, E-016, E-017 |
| T-003 | 明确桌面版/Web 版版本号策略（统一还是各自独立），写进 README | **Done 2026-07-28**（独立版本 + 单一来源 + tag 规范，D-013；build 脚本已断言一致性，E-018） | E-003, E-018 |
| T-004 | 发布流程固化：发布前跑两套测试 + 记录产物 SHA-256 到 EVIDENCE.md | **Done 2026-08-16**（Web v0.4.0 首次完整走通：测试 → WRT 原生构建 → 离线包 → SHA-256 → installer → verify/live smoke，E-030） | E-004, E-030 |
| T-005 | 写 HTTPS 反向代理部署文档（Caddy/Nginx 前置 + `WEB_COOKIE_SECURE=true`） | **Done 2026-08-15**（`webapp/README.md`「凭据传输」节，含 Caddy/nginx 两版与 `TRUSTED_ORIGINS`） | D-011 |
| T-006 | 为 `webapp/tests/` 的 live 脚本（live_smoke / live_idrac_readonly / inspect_idrac_network）补一段使用说明 | Open | E-016 |
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
| T-018 | 创建 `web-v0.4.1` tag 与 GitHub Release，上传 E-033 的离线包和 `.sha256` | Open；用户 2026-08-16 明确本轮只 push、不对外发布。注：v0.4.0 已被 v0.4.1 取代，不单独发布 | D-013, E-033 |
| T-017 | 给 `install.sh` 加 rollback 镜像标签保留策略（只保留最近 N 个） | Open；当前每次部署单调增长，8 个标签已让 overlay 可用从 358 MB 降到 276 MB（E-034） | E-034 |

关闭任务时把状态改为 Done + 日期，不删除行。
