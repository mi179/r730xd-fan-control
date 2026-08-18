# 在普通 Linux 上用 Docker 部署 Web 版

面向**不是 OpenWrt 路由器**的宿主：一台 NAS、一台小主机、一台虚拟机，只要它跑
Linux + Docker，并且**和 iDRAC 在同一个局域网段**。

OpenWrt 的部署走 [webapp/README.md](../webapp/README.md)，那条路有一键安装器。
这里是手动路径，因为要装的东西更少。

## 结论先说

**程序不需要任何改动**，镜像是同一个。差别只有三处配置，和**不要运行
`install.sh`**。

| | OpenWrt | 普通 Linux |
|---|---|---|
| 防火墙 | 必须建独立区并放行，`install.sh` 自动做 | **不用管**，Docker 自己管 iptables |
| `install.sh` | 用它 | **别用**，它会检查 `/etc/openwrt_release` 后直接拒绝 |
| `IDRAC_ARP_INTERFACE` | `br-lan` | **必须改**，见下面的坑 |
| `WEB_BIND_ADDRESS` | 路由器的 LAN 地址 | 宿主的 LAN 地址，或 `0.0.0.0` |

## 会让你白折腾半天的那个坑

自动发现 iDRAC 是靠读宿主的 ARP 表实现的，而匹配时会**要求 ARP 记录的网卡名和
`IDRAC_ARP_INTERFACE` 完全相同**（`webapp/app.py:339`）。

默认值 `br-lan` 是 OpenWrt 的网桥名。普通 Linux 上网卡叫 `eth0`、`enp3s0`、
`ens18`……**默认值会把所有 ARP 记录都过滤掉，于是自动发现永远找不到 iDRAC，而且
不会报任何错**——看起来就像"iDRAC 掉线了"。

两种改法，二选一：

```bash
# 推荐：留空，等于不按网卡过滤。单网卡的机器这样最省事。
IDRAC_ARP_INTERFACE=

# 或者填你自己的网卡名。多网卡、想限定只认某一张时用这个。
ip -4 -o addr show | awk '{print $2, $4}'    # 先查出网卡名
IDRAC_ARP_INTERFACE=enp3s0
```

## 步骤

### 1. 准备目录

```bash
sudo mkdir -p /opt/r730xd-fan-web/secrets
cd /opt/r730xd-fan-web
sudo chmod 700 . secrets
```

把仓库里这两个文件放进来：

- `webapp/installer/compose.offline.yaml` → 改名为 `compose.yaml`
- `webapp/.env.example` → 改名为 `.env`

### 2. 写 `.env`

```bash
# 宿主上哪个地址对外提供页面。0.0.0.0 表示所有网卡。
WEB_BIND_ADDRESS=0.0.0.0
WEB_PORT=8088

# 随机会话密钥，务必换掉示例占位值，否则重启后所有登录失效。
FLASK_SECRET_KEY=<把下面那条命令的输出贴进来>

# iDRAC 最后已知地址；地址会变没关系，MAC 才是身份。
IDRAC_HOST=192.168.1.50            # 换成你扫描到的地址
IDRAC_MAC=aa:bb:cc:dd:ee:ff        # 换成你自己 iDRAC 网口的 MAC
IDRAC_DISCOVERY_CIDR=192.168.1.0/24

# ← 这一行就是上面说的坑。留空或填你自己的网卡名。
IDRAC_ARP_INTERFACE=

IDRAC_USER=root
```

生成会话密钥：

```bash
head -c 48 /dev/urandom | base64 | tr -d '\n'
```

**不知道 iDRAC 的 MAC 和地址？** 在同网段的任意一台机器上：

```bash
sudo nmap -sU -p 623 --script ipmi-version 192.168.1.0/24
```

或者直接用桌面版 exe 的「扫描局域网找 iDRAC」，它会把地址和 MAC 一起显示出来。

```bash
sudo chmod 600 .env
```

### 3. 放密码

密码走 Docker secret，不进 `.env`、不进命令行参数：

```bash
sudo sh -c 'printf %s "你的iDRAC密码" > /opt/r730xd-fan-web/secrets/idrac_password'
sudo chown 10001:10001 /opt/r730xd-fan-web/secrets/idrac_password
sudo chmod 400 /opt/r730xd-fan-web/secrets/idrac_password
```

`10001` 是容器内的运行用户，容器以只读方式跑，改不了它。

### 4. 装镜像

镜像不在公开 registry 上，两种拿法：

```bash
# A. 自己构建。构建上下文是仓库根目录，因为镜像同时需要 webapp/ 和
#    r730xd_core/，而后者在 webapp/ 外面。
cd <仓库根目录>
sudo docker build -f webapp/Dockerfile -t r730xd-fan-web:0.4.1 .

# B. 用离线包里的镜像
sudo docker load < r730xd-fan-web-0.4.1-linux-amd64.tar.gz
```

> 仓库根的 `.dockerignore` 是**默认全排除的白名单**，所以虽然上下文是整个仓库，
> 实际只有 `webapp/` 的四项和 `r730xd_core/` 进得去——`.venv-win/`、`dist/`、
> `.git/` 以及 `webapp/secrets/` 里的真实密码都进不来。

### 5. 起来

```bash
cd /opt/r730xd-fan-web
sudo docker compose up -d
sudo docker compose ps          # 等 health 变成 healthy
curl -s http://127.0.0.1:8088/healthz
```

浏览器打开 `http://<宿主地址>:8088`。看温度不需要密码，调速才要 iDRAC 凭据。

## 怎么确认自动发现真的在工作

```bash
# 宿主的 ARP 表里应该能看到 iDRAC（把 MAC 换成你自己的）
ip neigh | grep -i aa:bb:cc

# 容器看到的是同一张表
sudo docker exec r730xd-fan-web cat /run/host-proc-net-arp | grep -i aa:bb:cc
```

容器里那条命令**没有输出**，就是上面那个网卡名的坑；页面上 iDRAC 状态会一直是
离线。

## 不适用的场景

**Mac / Windows 的 Docker Desktop 不要用这条路。** 容器实际跑在一个 Linux 虚拟机
里，挂进去的 `/proc/1/net/arp` 是**那个虚拟机的** ARP 表，你的 iDRAC 永远不会出现
在里面——按 MAC 自动发现直接失效。

控制和遥测仍然能用（出站流量走 NAT 够得着 iDRAC），但 DHCP 一换地址就得手工改
`IDRAC_HOST`。要保住自动发现，宿主必须是一台真正待在那个局域网里的 Linux
机器（物理机、或者桥接模式的虚拟机）。

## 升级与回滚

这条路没有 `install.sh` 的自动备份和回滚，自己来：

```bash
# 升级前留一个可回退的标签
sudo docker image tag r730xd-fan-web:0.4.1 r730xd-fan-web:rollback
sudo cp .env .env.bak

# 换新镜像后
sudo docker compose up -d

# 要回退就把 compose.yaml 里的 image 改回 rollback 再 up -d
```

**升级前先在界面上恢复自动温控。** 换容器不会改变物理风扇模式——你设的固定转速会
一直留在 iDRAC 里，而重启后软件这边的状态会显示"未知"。
