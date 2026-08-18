# R730xd Fan Web

这是独立于 Windows 桌面版的 OpenWrt Docker Web 控制台。

## 推荐：离线安装包

普通用户无需在 WRT 上构建镜像。从
[GitHub Releases](https://github.com/mi179/r730xd-fan-control/releases) 下载 Docker
离线包、同名 `.sha256` 和 `Install-R730xdFan-Web.ps1`，三者放在同一目录后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-R730xdFan-Web.ps1
```

默认上传到 `root@192.168.5.2`，可用 `-WrtHost` 和 `-WrtUser` 覆盖。安装器会交互
收集网络参数、隐藏输入 iDRAC 密码，完成镜像校验、专网、防火墙、健康检查与备份。
以下章节主要用于源码开发和手动部署。

**宿主不是 OpenWrt？** 普通 Linux + Docker 的部署走
[docs/DOCKER-GENERIC.md](../docs/DOCKER-GENERIC.md)——程序完全不用改，镜像是同一个，
差别只有几处配置，而且**不要跑 `install.sh`**（它会检查 `/etc/openwrt_release` 直接拒绝）。
那份文档里标红了一个会让人白折腾半天的坑：`IDRAC_ARP_INTERFACE` 的默认值 `br-lan` 是
OpenWrt 专有的，在普通 Linux 上会让自动发现静默失效。

## 自己升级：一条命令

维护者升级自己那台 WRT 不需要走离线包。离线包是给**从网上拿到它的人**用的——所以
才有预构建镜像和 SHA-256 清单。自己升级时源码就在手边，走这条：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\deploy_wrt.ps1
```

它把 webapp 源码（约 66 KB，不含镜像）传到 WRT，在 WRT 上 `docker build`，然后用
`install.sh --use-local-image` 安装，最后清理 `/tmp`。

对比之前的流程：镜像在 WRT 上构建 → `docker save` → scp 23 MB 回 Windows → 打包
→ scp 23 MB 回同一台 WRT → `docker load`。**同一个镜像在局域网上来回跑两趟**，
纯粹因为「离线包是交付单元」。现在它根本不动。

常用参数：`-Reconfigure`（重新填网络参数）、`-RotateSessionKey`、`-WrtHost` /
`-WrtUser`、`-StageOnly`（只打包不连机器，用来检查要传什么）。

安全边界没有放松：备份、失败自动回滚、防火墙独立区、密码不进 argv 全部不变。
少掉的只有「校验一个你自己三分钟前打的包」这一步——没有 `SHA256SUMS` 时安装器会
明确警告，且只允许配合 `--use-local-image`。

## 升级不再逐项提问

`install.sh` 检测到已有 `.env` 就直接沿用里面的值，不再问那 7 个网络参数——升级时
一路回车既不能发现错误，反而是打错字的来源。要改配置加 `--reconfigure`。首次安装
仍然逐项询问。

安装成功前会自动跑一遍 `verify.sh`（它检查容器 MAC、ARP 只读挂载、网络挂载关系和
`.env` 逐键比对，这些 `install.sh` 自己不查）。**验证不过就走回滚**，不会把一个坏
的安装交付成「完成」。

## 快速遥测

- 主页面无需登录，局域网设备可直接查看温度、风扇转速、功耗、最近 5 分钟趋势与最近三次采样；后端最多保留 90 条内存样本。
- 主页面只读取 iDRAC8 Redfish `Thermal` 与 `Power`，默认缓存 8 秒并每 15 秒在后台检查刷新；打开手机页面时直接读取缓存。
- Redfish 缺项时才调用按类型查询的 `ipmitool`。
- `sdr elist all` 只由“完整扫描”按钮触发，不会拖慢主页面。
- 调速、恢复自动温控、连接设置和完整扫描必须使用同一套 iDRAC 用户名与密码解锁；没有第二套 Web 密码。
- iDRAC 使用 DHCP 时，以网卡 MAC 作为稳定身份；旧 IP 失效后只发送无凭据的
  RMCP/ASF 存在探测，并从 WRT 宿主 ARP 表精确匹配 MAC 后更新地址。不会把密码
  逐个发送给局域网候选设备。
- `IDRAC_MAC` 与 `IDRAC_DISCOVERY_CIDR` 是必填安全条件；遗漏时容器拒绝启动，
  不会静默退回到未经身份核验的固定 IP 模式。

## OpenWrt 启动

```sh
cd /opt/r730xd-fan-web
cp .env.example .env
# 生成随机会话密钥，切勿保留示例占位值
sed -i "s|replace-with-at-least-32-random-bytes|$(head -c 48 /dev/urandom | base64 | tr -d '\n')|" .env
chmod 600 .env
mkdir -p secrets
# 交互输入 iDRAC 密码，避免写入命令历史或 .env
read -s -p 'iDRAC password: ' IDRAC_SECRET; echo
printf '%s' "$IDRAC_SECRET" > secrets/idrac_password
unset IDRAC_SECRET
chmod 700 secrets
chown 10001:10001 secrets/idrac_password
chmod 400 secrets/idrac_password
```

源码手动部署时，先创建一次专用 Docker 网络；防火墙按下文独立
`r730xd_fan` 区配置：

```sh
docker network create --driver bridge \
  --subnet 172.30.73.0/29 --gateway 172.30.73.1 \
  --opt com.docker.network.bridge.name=br-r730xd \
  r730xd-fan-control
docker compose up -d --build
```

`r730xd-fan-control` 是外部持久网络，后续 `docker compose down/up` 不会删除；
不要把其他容器接入这个网络。

默认访问地址为 `http://192.168.5.2:8088`。改端口只需编辑 `.env` 的
`WEB_PORT`，然后执行 `docker compose up -d`。默认只绑定 WRT 的 LAN 地址
`192.168.5.2`，不会监听其他接口。

> 当前部署是局域网 HTTP。公开监控没有凭据风险，但在登录控制区时，iDRAC
> 密码会经过本地网络。只应在可信 LAN 使用；需要跨设备或不可信 Wi-Fi 登录时，
> 应先在 WRT 前面配置 HTTPS 反向代理，再把 `WEB_COOKIE_SECURE` 改为 `true`。

iDRAC 密码不要写入 `.env`，而是保存到宿主机的
`secrets/idrac_password`。该文件不会复制进镜像；容器以只读 Docker secret
方式挂载。登录控制区时直接输入 iDRAC 的用户名与密码，后端会在本地恒定时间
比对，避免输错密码时额外消耗 iDRAC8 的远程失败次数。公开访客只能调用只读
遥测接口，不能调速或修改连接信息。

## WRT 防火墙

容器使用独立且默认拒绝的 `r730xd_fan` 区，不加入共享 `docker` 区。只需要三条
窄规则：LAN 访问 Web，以及固定容器 IP+MAC 在指定 `/24` 管理网内访问 Redfish
和 IPMI。子网范围是 DHCP 自动发现所需，但协议仍只放行 TCP 443 与 UDP 623；
`IDRAC_MAC` 负责最终身份匹配。

```sh
uci -q del_list firewall.docker.device='br-r730xd' 2>/dev/null || true
uci -q delete firewall.r730xd_fan_zone 2>/dev/null || true
uci set firewall.r730xd_fan_zone=zone
uci set firewall.r730xd_fan_zone.name='r730xd_fan'
uci set firewall.r730xd_fan_zone.device='br-r730xd'
uci set firewall.r730xd_fan_zone.family='ipv4'
uci set firewall.r730xd_fan_zone.input='REJECT'
uci set firewall.r730xd_fan_zone.output='ACCEPT'
uci set firewall.r730xd_fan_zone.forward='REJECT'
uci set firewall.r730xd_fan_zone.masq='0'

uci -q delete firewall.r730xd_fan_web_lan 2>/dev/null || true
uci set firewall.r730xd_fan_web_lan=rule
uci set firewall.r730xd_fan_web_lan.name='Allow-R730xd-Fan-Web-from-LAN'
uci set firewall.r730xd_fan_web_lan.src='lan'
uci set firewall.r730xd_fan_web_lan.dest='r730xd_fan'
uci set firewall.r730xd_fan_web_lan.family='ipv4'
uci set firewall.r730xd_fan_web_lan.dest_ip='172.30.73.2'
uci set firewall.r730xd_fan_web_lan.proto='tcp'
uci set firewall.r730xd_fan_web_lan.dest_port='8080'
uci set firewall.r730xd_fan_web_lan.target='ACCEPT'

uci -q delete firewall.r730xd_fan_redfish 2>/dev/null || true
uci set firewall.r730xd_fan_redfish=rule
uci set firewall.r730xd_fan_redfish.name='Allow-R730xd-Web-to-iDRAC-Redfish'
uci set firewall.r730xd_fan_redfish.src='r730xd_fan'
uci set firewall.r730xd_fan_redfish.dest='lan'
uci set firewall.r730xd_fan_redfish.src_ip='172.30.73.2'
uci set firewall.r730xd_fan_redfish.src_mac='02:73:0d:73:00:01'
uci set firewall.r730xd_fan_redfish.family='ipv4'
uci set firewall.r730xd_fan_redfish.dest_ip='192.168.5.0/24'
uci set firewall.r730xd_fan_redfish.proto='tcp'
uci set firewall.r730xd_fan_redfish.dest_port='443'
uci set firewall.r730xd_fan_redfish.target='ACCEPT'

uci -q delete firewall.r730xd_fan_ipmi 2>/dev/null || true
uci set firewall.r730xd_fan_ipmi=rule
uci set firewall.r730xd_fan_ipmi.name='Allow-R730xd-Web-to-iDRAC-IPMI'
uci set firewall.r730xd_fan_ipmi.src='r730xd_fan'
uci set firewall.r730xd_fan_ipmi.dest='lan'
uci set firewall.r730xd_fan_ipmi.src_ip='172.30.73.2'
uci set firewall.r730xd_fan_ipmi.src_mac='02:73:0d:73:00:01'
uci set firewall.r730xd_fan_ipmi.family='ipv4'
uci set firewall.r730xd_fan_ipmi.dest_ip='192.168.5.0/24'
uci set firewall.r730xd_fan_ipmi.proto='udp'
uci set firewall.r730xd_fan_ipmi.dest_port='623'
uci set firewall.r730xd_fan_ipmi.target='ACCEPT'

uci commit firewall
fw4 check && /etc/init.d/firewall reload
```

`docker compose ps` 的 `healthy` 只说明 Web 进程存活；页面上的 iDRAC 状态才表示
带外管理链路与遥测是否正常。历史在内存中，重启容器后会重新积累。

自动发现只适用于与 WRT 直连的二层 IPv4 子网。Compose 将宿主
`/proc/1/net/arp` 只读挂载进容器；不要改成 `/proc/net/arp`，后者看到的是 Docker
自己的网络命名空间。Compose 同时给本容器固定一个本地 MAC；防火墙的
`src_mac` 条件确保 `/24` 探测权限不会授予普通的其他 Docker 容器。拥有 WRT
root 或 Docker 管理权限的进程仍能伪造该 MAC，因此这些权限本身必须受信任。

## 凭据传输：先读这一节

这个 Web 版**没有自己的密码**——控制区用的就是 iDRAC 账号（D-005）。所以在本项目里，
「登录密码」和「服务器带外管理的最高权限凭据」是同一个东西。丢了它，对方拿到的不是
调风扇的权限，而是虚拟介质、电源、BIOS 和 KVM。

凭据会经过两条链路，安全性并不相同：

| 链路 | 协议 | 密码是否上线 | 默认是否安全 |
|---|---|---|---|
| 浏览器 → Web 容器 | HTTP（LAN） | **明文** | ❌ 需要下面的 HTTPS 反代 |
| 容器 → iDRAC IPMI | RMCP+/RAKP | 否（挑战应答） | ✅ |
| 容器 → iDRAC Redfish | HTTPS + Basic | **是**（Base64 头） | ⚠️ 需要下面的证书固定 |

### 1. 固定 iDRAC 证书指纹（代价最低，先做这个）

iDRAC 用自签证书，所以打开普通 CA 校验是做不到的——但**不校验服务端身份**意味着
Redfish 的 Basic 认证会把 iDRAC 密码交给任何在 443 端口应答的东西。中间人拿到的就是
完整凭据。

固定叶证书指纹即可关掉这条路径，不需要任何 PKI：

```bash
IDRAC_IP=192.168.5.130  # 换成当前按 MAC 发现到的地址，DHCP 后可能变化
openssl s_client -connect "${IDRAC_IP}:443" </dev/null 2>/dev/null | \
  openssl x509 -noout -fingerprint -sha256
```

把输出的十六进制填进 `.env` 的 `REDFISH_TLS_FINGERPRINT`（冒号可留可去），重启容器。
指纹不匹配时 urllib3 会**在握手阶段中断**，`Authorization` 头根本不会被写出去。

> iDRAC 重新生成证书（固件升级、重置、改主机名）后指纹会变，此时 Redfish 会失败并
> 回落到 ipmitool。届时重新取一次指纹即可。

### 2. HTTPS 反向代理（T-005）

这条解决的是浏览器到容器那一段的明文问题。在 WRT 上用 Caddy 前置最省事——它自带
本地 CA，能给内网 IP 签证书：

```caddyfile
# /etc/caddy/Caddyfile
https://192.168.5.2:8443 {
    tls internal
    reverse_proxy 127.0.0.1:8088 {
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }
}
```

nginx 版本：

```nginx
server {
    listen 8443 ssl;
    server_name 192.168.5.2;
    ssl_certificate     /etc/nginx/certs/r730xd.crt;
    ssl_certificate_key /etc/nginx/certs/r730xd.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    location / {
        proxy_pass http://127.0.0.1:8088;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**反代启用后必须同步改 `.env`，否则等于没做**：

```dotenv
WEB_COOKIE_SECURE=true
TRUSTED_ORIGINS=https://192.168.5.2:8443
```

同时把容器端口绑回本机，不再直接暴露明文口：

```yaml
ports:
  - "127.0.0.1:8088:8080"
```

> **注意 `REMOTE_ADDR`**：加了反代之后，容器看到的来源地址会全部变成反代自己的地址，
> 登录限速（按 /24 分桶）会退化成一个全局桶。要保留真实来源 IP，需要在应用前加
> `ProxyFix` 或让反代与容器共享网络命名空间——**在没有处理这一点之前，不要把这个服务
> 暴露到不可信网络**。

## 本地预览（不需要硬件）

`webapp/tests/dev_preview.py` 用一个假 iDRAC 把整套 Web 界面跑在本机，**不接触任何
硬件**：所有 ipmitool 调用和 Redfish 请求都由进程内的合成数据回答，风扇调速类的
`raw` 命令会被记录并吞掉，不会发出去。改模板和样式时用它，不需要服务器、iDRAC 或
容器。

```powershell
.\.venv-win\Scripts\python.exe webapp\tests\dev_preview.py --port 8099 --alerts
```

打开 `http://127.0.0.1:8099`，控制区用 `root` / `devpassword` 解锁。
`--alerts` 让一个传感器报 Warning，用来检查告警横幅和警告行样式。

默认会**故意复现两个 iDRAC8 firmware 2.70 的真实缺陷**，否则只能看到理想路径、
测不到降级状态：

| 复现的缺陷 | 界面应有的表现 |
|---|---|
| `PowerMetrics` 全零而实时功耗正常（E-032） | 功耗页丢弃这三个值，改用本地样本统计并标注区间（D-023） |
| `sdr elist all` 走到功耗/电压/电流前段错误（E-031） | 完整扫描显示带标记的部分结果（D-022） |

与 `live_*.py` 的区别：那三个脚本连的是**已部署的真实实例**，这个不连任何东西。

## 匿名可见范围

访客无需登录即可看到：三项总览读数、每个传感器的明细读数与阈值、趋势图，以及
**完整 SDR 扫描的全部记录**（每条带中文注解）。

触发一次新的完整扫描（`POST /api/sensors/deep-scan`）匿名也可以，但有全局冷却
`DEEP_SCAN_MIN_INTERVAL`（默认 60 秒）：冷却期内返回上次结果并带 `throttled: true`，
不会启动第二次 SDR 遍历。已登录操作员不受冷却限制。

> 这道限制不是为了保护数据，而是保护 BMC：`sdr elist all` 对 iDRAC8 是重操作
> （60 秒超时），没有冷却时局域网内任何人都能把它反复打满。

### iDRAC8 完整 SDR 的已知限制

当前 WRT 镜像内的 `ipmitool 1.8.19` 在这台 iDRAC8（firmware 2.70）上遍历完整 SDR
时会在途中发生 `SIGSEGV`。应用只在确认是该信号退出、且已经解析出有效记录时保留
崩溃前的数据，并在 UI 与 API 明确标记 `partial`；认证失败、其他非零退出或空结果仍按
扫描失败处理。2026-08-16 真机一次扫描取得 83 条部分记录。

这不是“完整扫描已修复”：重复触发崩溃可能暂时耗尽 iDRAC 的 IPMI session，所以看到
“部分结果”后不要连续刷新。完整且不泄漏 session 的替代读取路径由 T-014 跟踪；日常
Redfish 遥测和风扇 `raw` 控制不走完整 SDR 遍历，不受这个兼容问题影响。

**所有会改变机器状态的接口边界不变**：联锁、手动模式、调速、连接设置仍需 iDRAC 凭据。

## 遥测历史持久化

遥测样本除了保存在内存（`HISTORY_MAX_SAMPLES`，默认 90 条，仍是仪表盘的热路径）之外，
还会写入 SQLite。容器重启不再清零历史。

| 变量 | 默认 | 说明 |
|---|---|---|
| `TELEMETRY_DB_PATH` | `/data/telemetry.db` | **置空即回到纯内存模式** |
| `TELEMETRY_RETENTION_DAYS` | `30` | 超期行在刷盘时裁剪，每小时最多一次 |
| `TELEMETRY_FLUSH_INTERVAL` | `60` | 秒。批量刷盘，不是每条样本写一次盘 |
| `TELEMETRY_FLUSH_THRESHOLD` | `20` | 攒够这么多条也会立即刷盘 |

`/data` 是独立的 Docker named volume `r730xd-telemetry`。**named volume 在
`read_only: true` 下依然可写**，所以容器的只读根文件系统约束不需要放宽。镜像里
`/data` 属主为 UID 10001，否则 Docker 会用 root 建目录、非 root 进程写不进去。

如果路径不可写或数据库损坏，持久化会自动禁用并继续用内存服务，**不会阻止容器启动**。
`/api/status` 在已登录时返回 `telemetry.persistence`（行数、最旧/最新时间戳、错误）。

`GET /api/telemetry/history?range=5m|1h|6h|24h|7d` 返回该区间的样本；长区间在 SQL 里
分桶降采样（最多 240 点），温度取桶内最大值、转速与功耗取平均。返回的是 SQLite 与内存
的时间戳并集——容器刚重启时数据库还没攒够样本，此时内存补足。不带 `range` 时行为与
旧版完全一致。

## 升级与回滚

> 从 v0.3.1 升级到带持久化的版本必须**重建镜像**（Dockerfile 新增了 `/data`），
> 首次 `docker compose up -d` 会创建 `r730xd-telemetry` volume。升级前先在界面恢复自动温控。

升级前先保留当前镜像和 Compose 配置，不删除 secret：

```sh
cd /opt/r730xd-fan-web
docker image tag "$(docker inspect -f '{{.Image}}' r730xd-fan-web)" r730xd-fan-web:rollback
cp -p compose.yaml compose.yaml.rollback
docker compose config -q
docker compose build
docker compose up -d --force-recreate
docker exec r730xd-fan-web sh -c 'test -r /run/secrets/idrac_password'
wget -qO- http://192.168.5.2:8088/healthz
```

回滚：

```sh
cd /opt/r730xd-fan-web
cp -p compose.yaml.rollback compose.yaml
docker image tag r730xd-fan-web:rollback r730xd-fan-web:0.3.1
docker compose up -d --no-build --force-recreate
```

部署或回滚不会主动改变服务器的物理风扇模式；应用重启后软件状态显示为
`unknown`。如服务器当前为手动温控，维护前请先在界面恢复 iDRAC 自动温控。

## 常用命令

```sh
docker compose ps
docker compose logs --tail=100
docker compose restart
docker compose down
```
