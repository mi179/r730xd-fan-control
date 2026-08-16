#!/bin/sh
# Offline, idempotent installer for R730xd Fan Web on x86_64 OpenWrt.

set -eu
umask 077

APP_VERSION="0.4.1"
IMAGE_NAME="r730xd-fan-web:${APP_VERSION}"
IMAGE_ARCHIVE_REL="images/r730xd-fan-web-${APP_VERSION}-linux-amd64.tar.gz"
APP_DIR="${R730XD_APP_DIR:-/opt/r730xd-fan-web}"
CONTAINER_NAME="r730xd-fan-web"
PROJECT_NAME="r730xd-fan-web"
NETWORK_NAME="r730xd-fan-control"
NETWORK_SUBNET="172.30.73.0/29"
NETWORK_GATEWAY="172.30.73.1"
NETWORK_BRIDGE="br-r730xd"
CONTAINER_IP="172.30.73.2"
MARKER_NAME=".r730xd-fan-managed"
LOCK_FILE="/tmp/r730xd-fan-install.lock"

NONINTERACTIVE=0
ROTATE_SECRET=0
ROTATE_SESSION_KEY=0
PASSWORD_FILE=""
SUCCESS=0
MUTATED=0
NETWORK_CREATED=0
EXISTING_INSTALL=0
CONTAINER_EXISTS=0
FRESH_INSTALL_DIR=0
BACKUP_DIR=""
FIREWALL_BACKUP=""
CREATED_MARKER=0
PRELOAD_IMAGE_ID=""
PRELOAD_HOLD_REF=""
PRELOAD_CHECKED=0
STAGE="preflight"

log() {
    printf '%s\n' "[R730XD] $*"
}

warn() {
    printf '%s\n' "[R730XD] WARNING: $*" >&2
}

die() {
    printf '%s\n' "[R730XD] ERROR: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: sh install.sh [options]

  --non-interactive       Use environment/existing/default values without prompts
  --password-file FILE    Read a new iDRAC password from FILE (never from argv)
  --rotate-secret         Replace an existing iDRAC password (prompts if no file)
  --rotate-session-key    Generate a new Web session key and invalidate old sessions
  --app-dir DIR           Install under DIR (default /opt/r730xd-fan-web)
  -h, --help              Show this help

Configuration may be supplied through WEB_BIND_ADDRESS, WEB_PORT, IDRAC_HOST,
IDRAC_MAC, IDRAC_DISCOVERY_CIDR, IDRAC_ARP_INTERFACE and IDRAC_USER.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --non-interactive)
            NONINTERACTIVE=1
            ;;
        --password-file)
            [ "$#" -ge 2 ] || die "--password-file requires a path"
            PASSWORD_FILE=$2
            shift
            ;;
        --rotate-secret)
            ROTATE_SECRET=1
            ;;
        --rotate-session-key)
            ROTATE_SESSION_KEY=1
            ;;
        --app-dir)
            [ "$#" -ge 2 ] || die "--app-dir requires a path"
            APP_DIR=$2
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown option: $1"
            ;;
    esac
    shift
done

case "$APP_DIR" in
    /*) ;;
    *) die "--app-dir must be an absolute path" ;;
esac

BUNDLE_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
IMAGE_ARCHIVE="$BUNDLE_DIR/$IMAGE_ARCHIVE_REL"
MARKER_FILE="$APP_DIR/$MARKER_NAME"

compose() {
    (
        cd "$APP_DIR"
        docker compose --project-name "$PROJECT_NAME" \
            --env-file .env -f compose.yaml "$@"
    )
}

rollback_install() {
    [ "$MUTATED" -eq 1 ] || return 0
    warn "installation failed; attempting automatic rollback"

    if [ -n "$FIREWALL_BACKUP" ] && [ -f "$FIREWALL_BACKUP" ]; then
        uci -q revert firewall 2>/dev/null || true
        cp -p "$FIREWALL_BACKUP" /etc/config/firewall 2>/dev/null || true
        /etc/init.d/firewall reload >/dev/null 2>&1 || true
    fi

    if [ -n "$BACKUP_DIR" ] && [ -f "$BACKUP_DIR/image.id" ] && [ -f "$BACKUP_DIR/image.original_ref" ] && [ -f "$BACKUP_DIR/image.rollback_ref" ]; then
        image_id=$(cat "$BACKUP_DIR/image.id")
        original_ref=$(cat "$BACKUP_DIR/image.original_ref")
        rollback_ref=$(cat "$BACKUP_DIR/image.rollback_ref")
        rollback_id=$(docker image inspect -f '{{.Id}}' "$rollback_ref" 2>/dev/null || true)
        [ "$rollback_id" = "$image_id" ] && \
            docker image tag "$rollback_ref" "$original_ref" >/dev/null 2>&1 || true
    fi

    if [ "$EXISTING_INSTALL" -eq 1 ] && [ -n "$BACKUP_DIR" ]; then
        [ ! -f "$BACKUP_DIR/compose.yaml" ] || cp -p "$BACKUP_DIR/compose.yaml" "$APP_DIR/compose.yaml"
        [ ! -f "$BACKUP_DIR/.env" ] || cp -p "$BACKUP_DIR/.env" "$APP_DIR/.env"
        if [ -f "$APP_DIR/compose.yaml" ] && [ -f "$APP_DIR/.env" ]; then
            compose up -d --no-build --force-recreate >/dev/null 2>&1 || true
        fi
    elif [ -f "$APP_DIR/compose.yaml" ] && [ -f "$APP_DIR/.env" ]; then
        compose down >/dev/null 2>&1 || true
    fi

    if [ "$NETWORK_CREATED" -eq 1 ]; then
        attached=$(docker network inspect -f '{{len .Containers}}' "$NETWORK_NAME" 2>/dev/null || printf '1')
        [ "$attached" != "0" ] || docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
    fi

    [ "$CREATED_MARKER" -eq 0 ] || rm -f "$MARKER_FILE"

    fresh_can_cleanup=1
    if [ "$FRESH_INSTALL_DIR" -eq 1 ] && docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
        warn "fresh-install container still exists; preserving its configuration and secret for recovery"
        fresh_can_cleanup=0
    fi

    if [ "$FRESH_INSTALL_DIR" -eq 1 ] && [ "$fresh_can_cleanup" -eq 1 ]; then
        if [ "$PRELOAD_CHECKED" -eq 1 ]; then
            if [ -n "$PRELOAD_HOLD_REF" ] && [ -n "$PRELOAD_IMAGE_ID" ]; then
                hold_id=$(docker image inspect -f '{{.Id}}' "$PRELOAD_HOLD_REF" 2>/dev/null || true)
                [ "$hold_id" = "$PRELOAD_IMAGE_ID" ] && \
                    docker image tag "$PRELOAD_HOLD_REF" "$IMAGE_NAME" >/dev/null 2>&1 || true
                docker image rm "$PRELOAD_HOLD_REF" >/dev/null 2>&1 || true
            else
                docker image rm "$IMAGE_NAME" >/dev/null 2>&1 || true
            fi
        fi
        rm -f "$APP_DIR/compose.yaml" "$APP_DIR/compose.yaml.new" \
            "$APP_DIR/.env" "$APP_DIR/.env.$$" "$APP_DIR/.env.example" \
            "$APP_DIR/README.txt" "$APP_DIR/verify.sh" "$APP_DIR/rollback.sh" \
            "$APP_DIR/secrets/idrac_password" "$APP_DIR/secrets/.idrac_password.$$" \
            "$MARKER_FILE"
        if [ -n "$BACKUP_DIR" ]; then
            rm -f "$BACKUP_DIR/compose.yaml" "$BACKUP_DIR/.env" \
                "$BACKUP_DIR/firewall" "$BACKUP_DIR/image.id" "$BACKUP_DIR/image.original_ref" \
                "$BACKUP_DIR/image.rollback_ref"
            rmdir "$BACKUP_DIR" 2>/dev/null || true
        fi
        rmdir "$APP_DIR/backups" "$APP_DIR/secrets" "$APP_DIR" 2>/dev/null || true
    fi
}

cleanup() {
    rc=$?
    trap - 0 1 2 15
    if [ "$rc" -ne 0 ] && [ "$SUCCESS" -eq 0 ]; then
        warn "failed during stage: $STAGE (exit $rc)"
        rollback_install
    fi
    exit "$rc"
}

trap cleanup 0
trap 'exit 130' 1 2 15

command -v flock >/dev/null 2>&1 || die "flock is required"
exec 9>"$LOCK_FILE"
flock -n 9 || die "another installer instance is running"

for command_name in docker uci fw4 sha256sum gzip awk sed grep ip wget tar base64 head tr netstat id; do
    command -v "$command_name" >/dev/null 2>&1 || die "$command_name is required"
done

[ "$(id -u)" = "0" ] || die "run this installer as root"
[ -f /etc/openwrt_release ] || die "this installer only supports OpenWrt/ImmortalWrt"
case "$(uname -m)" in
    x86_64|amd64) ;;
    *) die "offline image is linux/amd64; current architecture is $(uname -m)" ;;
esac
docker info >/dev/null 2>&1 || die "Docker daemon is not available"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
[ -r /proc/1/net/arp ] || die "/proc/1/net/arp is not readable"
[ -f "$IMAGE_ARCHIVE" ] || die "missing image archive: $IMAGE_ARCHIVE_REL"
[ -f "$BUNDLE_DIR/SHA256SUMS" ] || die "missing SHA256SUMS"
(cd "$BUNDLE_DIR" && sha256sum -c SHA256SUMS >/dev/null) || die "bundle checksum verification failed"

available_kb=$(df -Pk /opt 2>/dev/null | awk 'NR == 2 { print $4 }')
case "$available_kb" in
    ''|*[!0-9]*) warn "unable to determine free space on /opt" ;;
    *) [ "$available_kb" -ge 204800 ] || die "at least 200 MiB free space is required on /opt" ;;
esac

env_value() {
    key=$1
    file=$2
    [ -f "$file" ] || return 0
    sed -n "s/^${key}=//p" "$file" | head -n 1
}

ask_value() {
    label=$1
    default_value=$2
    if [ "$NONINTERACTIVE" -eq 1 ]; then
        printf '%s' "$default_value"
        return 0
    fi
    printf '%s [%s]: ' "$label" "$default_value" >&2
    IFS= read -r entered
    if [ -n "$entered" ]; then
        printf '%s' "$entered"
    else
        printf '%s' "$default_value"
    fi
}

detect_lan_ip() {
    detected=$(uci -q get network.lan.ipaddr 2>/dev/null || true)
    if [ -z "$detected" ]; then
        detected=$(ip -4 -o addr show br-lan 2>/dev/null | awk 'NR == 1 { split($4, value, "/"); print value[1] }')
    fi
    printf '%s' "$detected"
}

is_ipv4() {
    printf '%s\n' "$1" | awk -F. '
        BEGIN { valid = 1 }
        NF != 4 { valid = 0 }
        { for (i = 1; i <= 4; i++) if ($i !~ /^[0-9]+$/ || $i > 255) valid = 0 }
        END { exit valid ? 0 : 1 }
    '
}

valid_port() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

OLD_ENV="$APP_DIR/.env"
default_bind=$(env_value WEB_BIND_ADDRESS "$OLD_ENV")
[ -n "$default_bind" ] || default_bind=$(detect_lan_ip)
[ -n "$default_bind" ] || default_bind="192.168.5.2"
default_port=$(env_value WEB_PORT "$OLD_ENV"); [ -n "$default_port" ] || default_port="8088"
default_host=$(env_value IDRAC_HOST "$OLD_ENV"); [ -n "$default_host" ] || default_host="192.168.5.111"
default_mac=$(env_value IDRAC_MAC "$OLD_ENV"); [ -n "$default_mac" ] || default_mac="00:00:00:00:00:00"
default_cidr=$(env_value IDRAC_DISCOVERY_CIDR "$OLD_ENV"); [ -n "$default_cidr" ] || default_cidr="192.168.5.0/24"
default_interface=$(env_value IDRAC_ARP_INTERFACE "$OLD_ENV"); [ -n "$default_interface" ] || default_interface="br-lan"
default_user=$(env_value IDRAC_USER "$OLD_ENV"); [ -n "$default_user" ] || default_user="root"
default_container_mac=$(env_value WEB_CONTAINER_MAC "$OLD_ENV"); [ -n "$default_container_mac" ] || default_container_mac="02:73:0d:73:00:01"

WEB_BIND_ADDRESS=${WEB_BIND_ADDRESS:-$(ask_value "WRT LAN address" "$default_bind")}
WEB_PORT=${WEB_PORT:-$(ask_value "Web port" "$default_port")}
IDRAC_HOST=${IDRAC_HOST:-$(ask_value "iDRAC last-known address" "$default_host")}
IDRAC_MAC=${IDRAC_MAC:-$(ask_value "iDRAC MAC" "$default_mac")}
IDRAC_DISCOVERY_CIDR=${IDRAC_DISCOVERY_CIDR:-$(ask_value "iDRAC discovery CIDR" "$default_cidr")}
IDRAC_ARP_INTERFACE=${IDRAC_ARP_INTERFACE:-$(ask_value "WRT LAN interface" "$default_interface")}
IDRAC_USER=${IDRAC_USER:-$(ask_value "iDRAC username" "$default_user")}
WEB_CONTAINER_MAC=${WEB_CONTAINER_MAC:-$default_container_mac}

is_ipv4 "$WEB_BIND_ADDRESS" || die "WEB_BIND_ADDRESS must be an IPv4 address"
is_ipv4 "$IDRAC_HOST" || die "IDRAC_HOST must be an IPv4 address"
valid_port "$WEB_PORT" || die "WEB_PORT must be between 1 and 65535"
printf '%s\n' "$IDRAC_MAC" | grep -Eq '^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$' || die "IDRAC_MAC is invalid"
[ "$(printf '%s' "$IDRAC_MAC" | tr 'A-F' 'a-f')" != "00:00:00:00:00:00" ] || die "IDRAC_MAC must be set to the physical iDRAC NIC MAC"
printf '%s\n' "$WEB_CONTAINER_MAC" | grep -Eq '^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$' || die "WEB_CONTAINER_MAC is invalid"
cidr_ip=${IDRAC_DISCOVERY_CIDR%/*}
cidr_prefix=${IDRAC_DISCOVERY_CIDR#*/}
[ "$cidr_ip" != "$IDRAC_DISCOVERY_CIDR" ] || die "discovery CIDR must include a prefix"
is_ipv4 "$cidr_ip" || die "discovery CIDR contains an invalid IPv4 address"
case "$cidr_prefix" in ''|*[!0-9]*) die "discovery CIDR prefix is invalid" ;; esac
[ "$cidr_prefix" -ge 24 ] && [ "$cidr_prefix" -le 32 ] || die "discovery CIDR prefix must be 24-32 (at most 256 addresses)"
printf '%s\n' "$cidr_ip" | grep -Eq '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' || die "discovery CIDR must be RFC1918 IPv4"
printf '%s\n' "$IDRAC_ARP_INTERFACE" | grep -Eq '^[A-Za-z0-9_.:-]{1,32}$' || die "IDRAC_ARP_INTERFACE is invalid"
printf '%s\n' "$IDRAC_USER" | grep -Eq '^[A-Za-z0-9_.@-]{1,64}$' || die "IDRAC_USER is invalid"
ip -4 addr show | grep -q "inet ${WEB_BIND_ADDRESS}/" || die "$WEB_BIND_ADDRESS is not assigned to this WRT"
[ -d "/sys/class/net/$IDRAC_ARP_INTERFACE" ] || die "network interface $IDRAC_ARP_INTERFACE does not exist"

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    CONTAINER_EXISTS=1
    project_label=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER_NAME")
    service_label=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$CONTAINER_NAME")
    working_label=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$CONTAINER_NAME")
    [ "$project_label" = "$PROJECT_NAME" ] || die "same-name container is not managed by this project"
    [ "$service_label" = "$CONTAINER_NAME" ] || die "same-name container has an unexpected Compose service"
    [ "$working_label" = "$APP_DIR" ] || die "same-name container belongs to $working_label, not $APP_DIR"
    EXISTING_INSTALL=1
fi

if [ "$EXISTING_INSTALL" -eq 0 ] && [ -f "$MARKER_FILE" ]; then
    [ -f "$APP_DIR/compose.yaml" ] || die "managed install is missing compose.yaml"
    [ -f "$APP_DIR/.env" ] || die "managed install is missing .env"
    [ -s "$APP_DIR/secrets/idrac_password" ] || die "managed install is missing the iDRAC secret"
    EXISTING_INSTALL=1
fi

if [ "$EXISTING_INSTALL" -eq 0 ]; then
    if [ -d "$APP_DIR" ]; then
        [ -z "$(ls -A "$APP_DIR" 2>/dev/null)" ] || die "$APP_DIR is not an installer-managed directory"
    fi
    FRESH_INSTALL_DIR=1
fi

if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    driver=$(docker network inspect -f '{{.Driver}}' "$NETWORK_NAME")
    subnet=$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "$NETWORK_NAME")
    gateway=$(docker network inspect -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}' "$NETWORK_NAME")
    bridge=$(docker network inspect -f '{{index .Options "com.docker.network.bridge.name"}}' "$NETWORK_NAME")
    [ "$driver" = "bridge" ] || die "$NETWORK_NAME uses unexpected driver $driver"
    [ "$subnet" = "$NETWORK_SUBNET" ] || die "$NETWORK_NAME uses unexpected subnet $subnet"
    [ "$gateway" = "$NETWORK_GATEWAY" ] || die "$NETWORK_NAME uses unexpected gateway $gateway"
    [ "$bridge" = "$NETWORK_BRIDGE" ] || die "$NETWORK_NAME uses unexpected bridge $bridge"
    attached=$(docker network inspect -f '{{range .Containers}}{{.Name}} {{end}}' "$NETWORK_NAME")
    for attached_name in $attached; do
        [ "$attached_name" = "$CONTAINER_NAME" ] || die "$NETWORK_NAME is shared with unexpected container $attached_name"
    done
fi

if [ "$CONTAINER_EXISTS" -eq 0 ] && netstat -lnt 2>/dev/null | awk '{print $4}' | grep -Eq ":${WEB_PORT}$"; then
    die "TCP port $WEB_PORT is already in use"
fi

existing_session_key=$(env_value FLASK_SECRET_KEY "$OLD_ENV")
if [ "$ROTATE_SESSION_KEY" -eq 1 ] || [ "${#existing_session_key}" -lt 32 ]; then
    FLASK_SECRET_KEY=$(head -c 48 /dev/urandom | base64 | tr -d '\n')
else
    FLASK_SECRET_KEY=$existing_session_key
fi

STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="$APP_DIR/backups/$STAMP"
STAGE="backup"
MUTATED=1
mkdir -p "$APP_DIR/backups" "$APP_DIR/secrets"
[ ! -e "$BACKUP_DIR" ] || die "backup path already exists; retry in one second"
mkdir "$BACKUP_DIR"
chmod 700 "$APP_DIR" "$APP_DIR/backups" "$BACKUP_DIR" "$APP_DIR/secrets"
if [ -f "$APP_DIR/compose.yaml" ]; then cp -p "$APP_DIR/compose.yaml" "$BACKUP_DIR/compose.yaml"; fi
if [ -f "$APP_DIR/.env" ]; then cp -p "$APP_DIR/.env" "$BACKUP_DIR/.env"; fi
cp -p /etc/config/firewall "$BACKUP_DIR/firewall"
FIREWALL_BACKUP="$BACKUP_DIR/firewall"

if [ "$FRESH_INSTALL_DIR" -eq 1 ]; then
    if docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
        PRELOAD_IMAGE_ID=$(docker image inspect -f '{{.Id}}' "$IMAGE_NAME")
        PRELOAD_HOLD_REF="r730xd-fan-web:preinstall-$STAMP"
        docker image tag "$PRELOAD_IMAGE_ID" "$PRELOAD_HOLD_REF"
    fi
    PRELOAD_CHECKED=1
fi

if [ "$CONTAINER_EXISTS" -eq 1 ]; then
    old_image_ref=$(docker inspect -f '{{.Config.Image}}' "$CONTAINER_NAME")
    old_image_id=$(docker inspect -f '{{.Image}}' "$CONTAINER_NAME")
    rollback_image_ref="r730xd-fan-web:rollback-$STAMP"
    docker image tag "$old_image_id" "$rollback_image_ref"
    printf '%s\n' "$old_image_id" > "$BACKUP_DIR/image.id"
    printf '%s\n' "$old_image_ref" > "$BACKUP_DIR/image.original_ref"
    printf '%s\n' "$rollback_image_ref" > "$BACKUP_DIR/image.rollback_ref"
    chmod 600 "$BACKUP_DIR/image.id" "$BACKUP_DIR/image.original_ref" "$BACKUP_DIR/image.rollback_ref"
elif [ "$EXISTING_INSTALL" -eq 1 ] && docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    old_image_ref="$IMAGE_NAME"
    old_image_id=$(docker image inspect -f '{{.Id}}' "$IMAGE_NAME")
    rollback_image_ref="r730xd-fan-web:rollback-$STAMP"
    docker image tag "$old_image_id" "$rollback_image_ref"
    printf '%s\n' "$old_image_id" > "$BACKUP_DIR/image.id"
    printf '%s\n' "$old_image_ref" > "$BACKUP_DIR/image.original_ref"
    printf '%s\n' "$rollback_image_ref" > "$BACKUP_DIR/image.rollback_ref"
    chmod 600 "$BACKUP_DIR/image.id" "$BACKUP_DIR/image.original_ref" "$BACKUP_DIR/image.rollback_ref"
fi

STAGE="image-load"
log "loading verified offline image"
gzip -dc "$IMAGE_ARCHIVE" | docker load >/dev/null
docker image inspect "$IMAGE_NAME" >/dev/null 2>&1 || die "loaded image $IMAGE_NAME is missing"
image_arch=$(docker image inspect -f '{{.Architecture}}' "$IMAGE_NAME")
image_os=$(docker image inspect -f '{{.Os}}' "$IMAGE_NAME")
image_user=$(docker image inspect -f '{{.Config.User}}' "$IMAGE_NAME")
[ "$image_arch" = "amd64" ] && [ "$image_os" = "linux" ] || die "loaded image platform is $image_os/$image_arch"
[ "$image_user" = "10001:10001" ] || die "loaded image has unexpected user $image_user"

if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    docker network create --driver bridge \
        --subnet "$NETWORK_SUBNET" --gateway "$NETWORK_GATEWAY" \
        --opt "com.docker.network.bridge.name=$NETWORK_BRIDGE" \
        "$NETWORK_NAME" >/dev/null
    NETWORK_CREATED=1
fi

STAGE="configuration"
cp -p "$BUNDLE_DIR/compose.offline.yaml" "$APP_DIR/compose.yaml.new"
mv "$APP_DIR/compose.yaml.new" "$APP_DIR/compose.yaml"
cp -p "$BUNDLE_DIR/.env.example" "$APP_DIR/.env.example"
cp -p "$BUNDLE_DIR/README.txt" "$APP_DIR/README.txt"
cp -p "$BUNDLE_DIR/verify.sh" "$APP_DIR/verify.sh"
cp -p "$BUNDLE_DIR/rollback.sh" "$APP_DIR/rollback.sh"
chmod 600 "$APP_DIR/compose.yaml" "$APP_DIR/.env.example" "$APP_DIR/README.txt"
chmod 700 "$APP_DIR/verify.sh" "$APP_DIR/rollback.sh"

ENV_TMP="$APP_DIR/.env.$$"
cat > "$ENV_TMP" <<EOF
WEB_PORT=$WEB_PORT
WEB_BIND_ADDRESS=$WEB_BIND_ADDRESS
WEB_CONTAINER_MAC=$WEB_CONTAINER_MAC
WEB_COOKIE_SECURE=false
FLASK_SECRET_KEY=$FLASK_SECRET_KEY

IDRAC_HOST=$IDRAC_HOST
IDRAC_MAC=$IDRAC_MAC
IDRAC_DISCOVERY_CIDR=$IDRAC_DISCOVERY_CIDR
IDRAC_ARP_INTERFACE=$IDRAC_ARP_INTERFACE
IDRAC_DISCOVERY_SCAN_INTERVAL=60
IDRAC_DISCOVERY_PROBE_TIMEOUT=0.8
IDRAC_USER=$IDRAC_USER
IDRAC_IPMI_PORT=623
IDRAC_REDFISH_PORT=443
REDFISH_VERIFY_TLS=false

TELEMETRY_CACHE_TTL=8
TELEMETRY_SAMPLE_INTERVAL=15
HISTORY_MAX_SAMPLES=90
TZ=Asia/Shanghai
EOF
chmod 600 "$ENV_TMP"
mv "$ENV_TMP" "$APP_DIR/.env"

SECRET_FILE="$APP_DIR/secrets/idrac_password"
if [ ! -s "$SECRET_FILE" ] || [ "$ROTATE_SECRET" -eq 1 ] || [ -n "$PASSWORD_FILE" ]; then
    if [ -n "$PASSWORD_FILE" ]; then
        [ -r "$PASSWORD_FILE" ] || die "password file is not readable"
        IDRAC_SECRET=$(cat "$PASSWORD_FILE")
    else
        [ "$NONINTERACTIVE" -eq 0 ] || die "fresh/rotated secret requires --password-file in non-interactive mode"
        printf 'iDRAC password (hidden): ' >&2
        IFS= read -r -s IDRAC_SECRET
        printf '\n' >&2
    fi
    secret_length=${#IDRAC_SECRET}
    [ "$secret_length" -ge 1 ] && [ "$secret_length" -le 256 ] || die "iDRAC password length must be 1-256 characters"
    SECRET_TMP="$APP_DIR/secrets/.idrac_password.$$"
    printf '%s' "$IDRAC_SECRET" > "$SECRET_TMP"
    chown 10001:10001 "$SECRET_TMP"
    chmod 400 "$SECRET_TMP"
    mv "$SECRET_TMP" "$SECRET_FILE"
    unset IDRAC_SECRET
fi
chown 10001:10001 "$SECRET_FILE"
chmod 400 "$SECRET_FILE"
chmod 700 "$APP_DIR/secrets"

STAGE="firewall"
uci -q del_list firewall.docker.device="$NETWORK_BRIDGE" 2>/dev/null || true

uci -q delete firewall.r730xd_fan_zone 2>/dev/null || true
uci set firewall.r730xd_fan_zone=zone
uci set firewall.r730xd_fan_zone.name='r730xd_fan'
uci set firewall.r730xd_fan_zone.device="$NETWORK_BRIDGE"
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
uci set firewall.r730xd_fan_web_lan.dest_ip="$CONTAINER_IP"
uci set firewall.r730xd_fan_web_lan.proto='tcp'
uci set firewall.r730xd_fan_web_lan.dest_port='8080'
uci set firewall.r730xd_fan_web_lan.target='ACCEPT'

uci -q delete firewall.r730xd_fan_redfish 2>/dev/null || true
uci set firewall.r730xd_fan_redfish=rule
uci set firewall.r730xd_fan_redfish.name='Allow-R730xd-Web-to-iDRAC-Redfish'
uci set firewall.r730xd_fan_redfish.src='r730xd_fan'
uci set firewall.r730xd_fan_redfish.dest='lan'
uci set firewall.r730xd_fan_redfish.src_ip="$CONTAINER_IP"
uci set firewall.r730xd_fan_redfish.src_mac="$WEB_CONTAINER_MAC"
uci set firewall.r730xd_fan_redfish.family='ipv4'
uci set firewall.r730xd_fan_redfish.dest_ip="$IDRAC_DISCOVERY_CIDR"
uci set firewall.r730xd_fan_redfish.proto='tcp'
uci set firewall.r730xd_fan_redfish.dest_port='443'
uci set firewall.r730xd_fan_redfish.target='ACCEPT'

uci -q delete firewall.r730xd_fan_ipmi 2>/dev/null || true
uci set firewall.r730xd_fan_ipmi=rule
uci set firewall.r730xd_fan_ipmi.name='Allow-R730xd-Web-to-iDRAC-IPMI'
uci set firewall.r730xd_fan_ipmi.src='r730xd_fan'
uci set firewall.r730xd_fan_ipmi.dest='lan'
uci set firewall.r730xd_fan_ipmi.src_ip="$CONTAINER_IP"
uci set firewall.r730xd_fan_ipmi.src_mac="$WEB_CONTAINER_MAC"
uci set firewall.r730xd_fan_ipmi.family='ipv4'
uci set firewall.r730xd_fan_ipmi.dest_ip="$IDRAC_DISCOVERY_CIDR"
uci set firewall.r730xd_fan_ipmi.proto='udp'
uci set firewall.r730xd_fan_ipmi.dest_port='623'
uci set firewall.r730xd_fan_ipmi.target='ACCEPT'

fw4 check >/dev/null
uci commit firewall

# `firewall reload` returns non-zero on hosts that carry stale third-party
# includes (miniupnpd, passwall, shadowsocksr ...) even when the ruleset itself
# loaded correctly -- fw4 reports the failing include, not a failure of ours.
# This host is one of them (E-019), so a bare reload made the installer
# unusable here. Ignoring the exit code outright is not acceptable either: a
# genuinely failed reload would leave the container without its zone. Tolerate
# it only when the result is verifiably correct -- the syntax check still
# passes and all three project rules are live in the kernel. If the ruleset
# cannot be read back, fail closed.
if /etc/init.d/firewall reload >/dev/null 2>&1; then
    :
else
    warn "firewall reload exited non-zero; verifying the live ruleset before continuing"
    fw4 check >/dev/null 2>&1 || die "firewall ruleset no longer validates after reload"
    command -v nft >/dev/null 2>&1 || die "firewall reload failed and nft is unavailable to verify the result"
    live_ruleset=$(nft list table inet fw4 2>/dev/null || true)
    [ -n "$live_ruleset" ] || die "firewall reload failed and the live ruleset could not be read"
    missing_rules=""
    for rule_name in \
        Allow-R730xd-Fan-Web-from-LAN \
        Allow-R730xd-Web-to-iDRAC-Redfish \
        Allow-R730xd-Web-to-iDRAC-IPMI
    do
        printf '%s\n' "$live_ruleset" | grep -qF "$rule_name" \
            || missing_rules="$missing_rules $rule_name"
    done
    [ -z "$missing_rules" ] \
        || die "firewall reload failed and these rules are not live:$missing_rules"
    warn "pre-existing unrelated firewall includes failed; all three project rules are live, continuing"
fi

STAGE="service-start"
compose config -q
log "starting $IMAGE_NAME without an online build"
compose up -d --no-build --force-recreate

health=""
attempt=0
while [ "$attempt" -lt 45 ]; do
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)
    [ "$health" != "healthy" ] || break
    case "$health" in
        exited|dead) break ;;
    esac
    sleep 2
    attempt=$((attempt + 1))
done
[ "$health" = "healthy" ] || {
    docker logs --tail 80 "$CONTAINER_NAME" >&2 2>/dev/null || true
    die "container health check failed: $health"
}

docker exec "$CONTAINER_NAME" sh -c 'test -r /run/secrets/idrac_password' || die "container cannot read the iDRAC secret"
health_body=$(wget -qO- "http://$WEB_BIND_ADDRESS:$WEB_PORT/healthz" 2>/dev/null || true)
printf '%s' "$health_body" | grep -q '"healthy"' || die "LAN health endpoint did not respond"

cat > "$MARKER_FILE" <<EOF
version=$APP_VERSION
installed_at=$STAMP
project=$PROJECT_NAME
network=$NETWORK_NAME
EOF
chmod 600 "$MARKER_FILE"
if [ "$EXISTING_INSTALL" -eq 0 ]; then CREATED_MARKER=1; fi

SUCCESS=1
log "installation complete"
log "Web: http://$WEB_BIND_ADDRESS:$WEB_PORT"
log "Public telemetry needs no password; fan control uses the iDRAC credentials."
log "No fan-control command was sent by this installer."
