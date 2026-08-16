#!/bin/sh
set -eu

APP_DIR="${R730XD_APP_DIR:-/opt/r730xd-fan-web}"
CONTAINER_NAME="r730xd-fan-web"
NETWORK_NAME="r730xd-fan-control"
EXPECTED_IMAGE="r730xd-fan-web:0.4.1"
EXPECTED_ADDRESS="172.30.73.2"
EXPECTED_SUBNET="172.30.73.0/29"
EXPECTED_GATEWAY="172.30.73.1"
EXPECTED_BRIDGE="br-r730xd"

die() {
    printf '%s\n' "[R730XD] VERIFY FAILED: $*" >&2
    exit 1
}

[ -f "$APP_DIR/.r730xd-fan-managed" ] || die "managed-install marker is missing"
[ -f "$APP_DIR/.env" ] || die ".env is missing"
[ -f "$APP_DIR/compose.yaml" ] || die "compose.yaml is missing"
[ -s "$APP_DIR/secrets/idrac_password" ] || die "iDRAC secret is missing"

env_value() {
    sed -n "s/^$1=//p" "$APP_DIR/.env" | head -n 1
}
expected_mac=$(env_value WEB_CONTAINER_MAC)
[ -n "$expected_mac" ] || expected_mac="02:73:0d:73:00:01"
expected_cidr=$(env_value IDRAC_DISCOVERY_CIDR)
[ -n "$expected_cidr" ] || die "IDRAC_DISCOVERY_CIDR is missing"

assert_uci() {
    key=$1
    expected=$2
    actual=$(uci -q get "$key" 2>/dev/null || true)
    [ "$actual" = "$expected" ] || die "$key is '$actual', expected '$expected'"
}

(
    cd "$APP_DIR"
    docker compose --project-name r730xd-fan-web --env-file .env \
        -f compose.yaml config -q
) || die "Compose configuration is invalid"

image=$(docker inspect -f '{{.Config.Image}}' "$CONTAINER_NAME" 2>/dev/null || true)
state=$(docker inspect -f '{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || true)
health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)
address=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)
mac=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.MacAddress}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)
container_networks=$(docker inspect -f '{{range $name, $value := .NetworkSettings.Networks}}{{$name}} {{end}}' "$CONTAINER_NAME" 2>/dev/null || true)
project=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER_NAME" 2>/dev/null || true)
service=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$CONTAINER_NAME" 2>/dev/null || true)
arp_mount=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/run/host-proc-net-arp"}}{{.Source}}|{{.RW}}{{end}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)

[ "$state" = "running" ] || die "container state is $state"
[ "$health" = "healthy" ] || die "container health is $health"
[ "$image" = "$EXPECTED_IMAGE" ] || die "container image is $image"
[ "$project" = "r730xd-fan-web" ] || die "container Compose project is $project"
[ "$service" = "$CONTAINER_NAME" ] || die "container Compose service is $service"
[ "$address" = "$EXPECTED_ADDRESS" ] || die "container address is $address"
[ "$mac" = "$expected_mac" ] || die "container MAC is $mac"
[ "$container_networks" = "$NETWORK_NAME " ] || die "container networks are $container_networks"
[ "$arp_mount" = "/proc/1/net/arp|false" ] || die "host ARP mount is $arp_mount"
docker exec "$CONTAINER_NAME" sh -c 'test -r /run/host-proc-net-arp' || die "container cannot read the host ARP table"

driver=$(docker network inspect -f '{{.Driver}}' "$NETWORK_NAME" 2>/dev/null || true)
subnet=$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "$NETWORK_NAME" 2>/dev/null || true)
gateway=$(docker network inspect -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}' "$NETWORK_NAME" 2>/dev/null || true)
bridge=$(docker network inspect -f '{{index .Options "com.docker.network.bridge.name"}}' "$NETWORK_NAME" 2>/dev/null || true)
attached=$(docker network inspect -f '{{range .Containers}}{{.Name}} {{end}}' "$NETWORK_NAME" 2>/dev/null || true)
[ "$driver" = "bridge" ] || die "network driver is $driver"
[ "$subnet" = "$EXPECTED_SUBNET" ] || die "network subnet is $subnet"
[ "$gateway" = "$EXPECTED_GATEWAY" ] || die "network gateway is $gateway"
[ "$bridge" = "$EXPECTED_BRIDGE" ] || die "network bridge is $bridge"
[ "$attached" = "$CONTAINER_NAME " ] || die "network containers are $attached"

docker_zone=$(uci -q show firewall.docker 2>/dev/null || true)
if printf '%s\n' "$docker_zone" | grep -q "device='$EXPECTED_BRIDGE'"; then
    die "$EXPECTED_BRIDGE is still attached to the shared docker firewall zone"
fi

zone_sections=$(uci -q show firewall | sed -n 's/^firewall\.\([^.=]*\)=zone$/\1/p')
for section in $zone_sections; do
    [ "$section" = "r730xd_fan_zone" ] && continue
    devices=$(uci -q get "firewall.$section.device" 2>/dev/null || true)
    case " $devices " in
        *" $EXPECTED_BRIDGE "*) die "$EXPECTED_BRIDGE is also attached to firewall.$section" ;;
    esac
done

assert_uci firewall.r730xd_fan_zone.name r730xd_fan
assert_uci firewall.r730xd_fan_zone.device "$EXPECTED_BRIDGE"
assert_uci firewall.r730xd_fan_zone.input REJECT
assert_uci firewall.r730xd_fan_zone.output ACCEPT
assert_uci firewall.r730xd_fan_zone.forward REJECT
assert_uci firewall.r730xd_fan_zone.masq 0

assert_uci firewall.r730xd_fan_web_lan.src lan
assert_uci firewall.r730xd_fan_web_lan.dest r730xd_fan
assert_uci firewall.r730xd_fan_web_lan.dest_ip "$EXPECTED_ADDRESS"
assert_uci firewall.r730xd_fan_web_lan.proto tcp
assert_uci firewall.r730xd_fan_web_lan.dest_port 8080
assert_uci firewall.r730xd_fan_web_lan.target ACCEPT

for section in r730xd_fan_redfish r730xd_fan_ipmi; do
    assert_uci "firewall.$section.src" r730xd_fan
    assert_uci "firewall.$section.dest" lan
    assert_uci "firewall.$section.src_ip" "$EXPECTED_ADDRESS"
    assert_uci "firewall.$section.src_mac" "$expected_mac"
    assert_uci "firewall.$section.dest_ip" "$expected_cidr"
    assert_uci "firewall.$section.target" ACCEPT
done
assert_uci firewall.r730xd_fan_redfish.proto tcp
assert_uci firewall.r730xd_fan_redfish.dest_port 443
assert_uci firewall.r730xd_fan_ipmi.proto udp
assert_uci firewall.r730xd_fan_ipmi.dest_port 623

forwarding_sections=$(uci -q show firewall | sed -n 's/^firewall\.\([^.=]*\)=forwarding$/\1/p')
for section in $forwarding_sections; do
    src=$(uci -q get "firewall.$section.src" 2>/dev/null || true)
    [ "$src" != "r730xd_fan" ] || die "unexpected forwarding firewall.$section leaves r730xd_fan"
done

rule_sections=$(uci -q show firewall | sed -n 's/^firewall\.\([^.=]*\)=rule$/\1/p')
for section in $rule_sections; do
    src=$(uci -q get "firewall.$section.src" 2>/dev/null || true)
    target=$(uci -q get "firewall.$section.target" 2>/dev/null || true)
    if [ "$src" = "r730xd_fan" ] && [ "$target" = "ACCEPT" ]; then
        case "$section" in
            r730xd_fan_redfish|r730xd_fan_ipmi) ;;
            *) die "unexpected ACCEPT rule firewall.$section leaves r730xd_fan" ;;
        esac
    fi
done

fw4 check >/dev/null || die "fw4 validation failed"

bind_address=$(env_value WEB_BIND_ADDRESS)
web_port=$(env_value WEB_PORT)
[ -n "$bind_address" ] && [ -n "$web_port" ] || die "Web bind settings are missing"
published=$(docker inspect -f '{{range $port, $bindings := .NetworkSettings.Ports}}{{if eq $port "8080/tcp"}}{{range $bindings}}{{.HostIp}}:{{.HostPort}}{{end}}{{end}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)
[ "$published" = "$bind_address:$web_port" ] || die "published Web binding is $published"

health_body=$(wget -qO- "http://$bind_address:$web_port/healthz" 2>/dev/null || true)
printf '%s' "$health_body" | grep -q '"healthy"' || die "health endpoint did not respond"

secret_mode=$(ls -ln "$APP_DIR/secrets/idrac_password" | awk '{print $1}')
secret_owner=$(ls -ln "$APP_DIR/secrets/idrac_password" | awk '{print $3 ":" $4}')
secret_dir_mode=$(ls -ldn "$APP_DIR/secrets" | awk '{print $1}')
[ "$secret_mode" = "-r--------" ] || die "secret mode is $secret_mode, expected 0400"
[ "$secret_owner" = "10001:10001" ] || die "secret owner is $secret_owner"
[ "$secret_dir_mode" = "drwx------" ] || die "secret directory mode is $secret_dir_mode, expected 0700"

printf '%s\n' "[R730XD] VERIFY OK"
printf '%s\n' "[R730XD] image=$image state=$state health=$health"
printf '%s\n' "[R730XD] Web: http://$bind_address:$web_port"
printf '%s\n' "[R730XD] No fan-control command was sent."
