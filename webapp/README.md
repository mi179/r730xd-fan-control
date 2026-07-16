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

## 升级与回滚

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
