R730xd Fan Web 0.4.1 — OpenWrt x86_64 离线安装包
=================================================

这个包不需要联网构建。它已经包含经过实机验证的 linux/amd64 Docker 镜像，
安装过程中只会执行 docker load，不会下载 Alpine、Python、pip 包或 ipmitool。

安装前提
--------

- x86_64 OpenWrt / ImmortalWrt
- 已安装并启动 Docker
- 已安装 Docker Compose v2（命令为 docker compose）
- 以 root 执行
- 默认 WRT 地址 192.168.5.2、Web 端口 8088
- 默认 iDRAC 最后地址 192.168.5.111
- iDRAC MAC 默认为占位值；首次安装必须填写物理 iDRAC 网卡 MAC

默认 IP 只是示例值，不属于密码。安装时会逐项显示；首次安装必须输入物理
iDRAC 网卡 MAC，换服务器或换网段时输入对应的新值。

直接安装
--------

1. 把整个解压目录复制到 WRT，例如 /tmp/R730xdFan-Web-Docker-v0.4.1。
2. SSH 登录 WRT 后执行：

   cd /tmp/R730xdFan-Web-Docker-v0.4.1
   sh install.sh

3. 首次安装会要求隐藏输入 iDRAC 密码。升级时默认保留原密码。
4. 安装完成后访问：

   http://192.168.5.2:8088

Windows 一键上传
----------------

把压缩包和 Install-R730xdFan-Web.ps1 放在同一目录，在 PowerShell 执行：

  powershell -ExecutionPolicy Bypass -File .\Install-R730xdFan-Web.ps1

默认目标为 root@192.168.5.2。脚本会先校验同目录的 .sha256 文件，再上传、解压，
然后进入远程交互安装。

非交互安装
----------

密码不能作为命令行参数。请先在 WRT 创建仅 root 可读的临时文件：

  chmod 600 /tmp/idrac-password
  sh install.sh --non-interactive --password-file /tmp/idrac-password
  rm -f /tmp/idrac-password

可通过环境变量覆盖配置：WEB_BIND_ADDRESS、WEB_PORT、IDRAC_HOST、IDRAC_MAC、
IDRAC_DISCOVERY_CIDR、IDRAC_ARP_INTERFACE、IDRAC_USER。

检查与回滚
----------

  sh /opt/r730xd-fan-web/verify.sh
  sh /opt/r730xd-fan-web/rollback.sh

每次升级前会保存 Compose、.env 和防火墙备份，但不会复制 iDRAC 密码。
安装失败会自动尝试恢复旧镜像、旧配置和旧容器；回滚失败也会恢复回滚前状态。
首次安装中断后可直接重新执行。不会执行 docker system prune，也不会
删除未知容器、网络或镜像。

安全边界
--------

- 密码仅写入 /opt/r730xd-fan-web/secrets/idrac_password。
- secret 目录权限 0700，密码文件 UID/GID 10001:10001、权限 0400。
- .env 权限 0600；只含会话密钥和非密码连接参数。
- 容器只读、无 Linux capabilities、启用 no-new-privileges。
- 专用网络固定为 172.30.73.0/29，只有本应用接入。
- WRT 使用独立的 r730xd_fan 防火墙区，默认拒绝输入与转发。
- 仅允许 LAN 访问本容器 8080，并只允许本容器 IP+MAC 访问管理网 TCP 443/UDP 623。
- 安装器和检查脚本绝不会发送风扇调速命令。

文件完整性
----------

安装器会在修改系统前校验 SHA256SUMS，镜像校验失败会立即中止。
