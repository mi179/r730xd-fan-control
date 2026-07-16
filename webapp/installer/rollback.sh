#!/bin/sh
set -eu
umask 077

APP_DIR="${R730XD_APP_DIR:-/opt/r730xd-fan-web}"
CONTAINER_NAME="r730xd-fan-web"
PROJECT_NAME="r730xd-fan-web"
LOCK_FILE="/tmp/r730xd-fan-install.lock"
SUCCESS=0
MUTATED=0
PRE_DIR=""

log() {
    printf '%s\n' "[R730XD] $*"
}

warn() {
    printf '%s\n' "[R730XD] WARNING: $*" >&2
}

die() {
    printf '%s\n' "[R730XD] ROLLBACK ERROR: $*" >&2
    exit 1
}

compose_current() {
    (
        cd "$APP_DIR"
        docker compose --project-name "$PROJECT_NAME" --env-file .env \
            -f compose.yaml "$@"
    )
}

atomic_replace() {
    source_path=$1
    target_path=$2
    temporary_path="${target_path}.r730xd-txn.$$"
    if cp -p "$source_path" "$temporary_path" && mv "$temporary_path" "$target_path"; then
        return 0
    fi
    rm -f "$temporary_path"
    return 1
}

restore_pre_rollback() {
    [ "$MUTATED" -eq 1 ] || return 0
    warn "rollback failed; restoring the pre-rollback configuration"
    failed=0
    set +e

    if [ -f "$PRE_DIR/image.id" ] && [ -f "$PRE_DIR/image.original_ref" ] && [ -f "$PRE_DIR/image.rollback_ref" ]; then
        image_id=$(cat "$PRE_DIR/image.id")
        original_ref=$(cat "$PRE_DIR/image.original_ref")
        rollback_ref=$(cat "$PRE_DIR/image.rollback_ref")
        rollback_id=$(docker image inspect -f '{{.Id}}' "$rollback_ref" 2>/dev/null)
        [ "$rollback_id" = "$image_id" ] || failed=1
        docker image tag "$rollback_ref" "$original_ref" >/dev/null 2>&1 || failed=1
    fi

    atomic_replace "$PRE_DIR/compose.yaml" "$APP_DIR/compose.yaml" || failed=1
    atomic_replace "$PRE_DIR/.env" "$APP_DIR/.env" || failed=1
    uci -q revert firewall >/dev/null 2>&1 || true
    atomic_replace "$PRE_DIR/firewall" /etc/config/firewall || failed=1
    chmod 600 "$APP_DIR/compose.yaml" "$APP_DIR/.env"
    if fw4 check >/dev/null 2>&1; then
        /etc/init.d/firewall reload >/dev/null 2>&1 || failed=1
    else
        warn "pre-rollback firewall backup did not pass fw4 validation"
        failed=1
    fi
    compose_current up -d --no-build --force-recreate >/dev/null 2>&1 || failed=1

    attempt=0
    health=""
    while [ "$attempt" -lt 45 ]; do
        health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER_NAME" 2>/dev/null)
        [ "$health" != "healthy" ] || break
        case "$health" in exited|dead) break ;; esac
        sleep 2
        attempt=$((attempt + 1))
    done
    [ "$health" = "healthy" ] || failed=1
    set -e
    [ "$failed" -eq 0 ]
}

cleanup() {
    rc=$?
    trap - 0 1 2 15
    if [ "$rc" -ne 0 ] && [ "$SUCCESS" -eq 0 ]; then
        if restore_pre_rollback; then
            warn "rollback attempt failed; previous state was restored"
        else
            warn "CRITICAL: rollback and automatic recovery both failed"
            rc=70
        fi
    fi
    exit "$rc"
}

trap cleanup 0
trap 'exit 130' 1 2 15

command -v flock >/dev/null 2>&1 || die "flock is required"
exec 9>"$LOCK_FILE"
flock -n 9 || die "an installer or rollback is already running"

[ "$(id -u)" = "0" ] || die "run as root"
[ -f "$APP_DIR/.r730xd-fan-managed" ] || die "managed-install marker is missing"
[ -f "$APP_DIR/compose.yaml" ] || die "current compose.yaml is missing"
[ -f "$APP_DIR/.env" ] || die "current .env is missing"

if [ "$#" -gt 1 ]; then
    die "usage: sh rollback.sh [backup-directory]"
fi
if [ "$#" -eq 1 ]; then
    BACKUP_DIR=$1
else
    BACKUP_DIR=$(ls -1dt "$APP_DIR"/backups/* 2>/dev/null | head -n 1)
fi

[ -n "$BACKUP_DIR" ] && [ -d "$BACKUP_DIR" ] || die "no backup is available"
BACKUPS_ROOT=$(CDPATH= cd "$APP_DIR/backups" && pwd) || die "cannot resolve backups directory"
BACKUP_DIR=$(CDPATH= cd "$BACKUP_DIR" && pwd) || die "cannot resolve backup directory"
case "$BACKUP_DIR" in
    "$BACKUPS_ROOT"/*) ;;
    *) die "backup directory must be under $APP_DIR/backups" ;;
esac
[ -f "$BACKUP_DIR/compose.yaml" ] || die "$BACKUP_DIR/compose.yaml is missing"
[ -f "$BACKUP_DIR/.env" ] || die "$BACKUP_DIR/.env is missing"
[ -f "$BACKUP_DIR/firewall" ] || die "$BACKUP_DIR/firewall is missing"

docker compose --project-name "$PROJECT_NAME" --env-file "$BACKUP_DIR/.env" \
    -f "$BACKUP_DIR/compose.yaml" config -q || die "backup Compose configuration is invalid"

TARGET_HAS_IMAGE=0
if [ -f "$BACKUP_DIR/image.id" ] || [ -f "$BACKUP_DIR/image.original_ref" ] || [ -f "$BACKUP_DIR/image.rollback_ref" ]; then
    [ -f "$BACKUP_DIR/image.id" ] && [ -f "$BACKUP_DIR/image.original_ref" ] && [ -f "$BACKUP_DIR/image.rollback_ref" ] || \
        die "backup image metadata is incomplete"
    target_image_id=$(cat "$BACKUP_DIR/image.id")
    target_original_ref=$(cat "$BACKUP_DIR/image.original_ref")
    target_rollback_ref=$(cat "$BACKUP_DIR/image.rollback_ref")
    printf '%s\n' "$target_image_id" | grep -Eq '^sha256:[0-9a-f]{64}$' || die "backup image ID is invalid"
    printf '%s\n' "$target_original_ref" | grep -Eq '^r730xd-fan-web:[A-Za-z0-9][A-Za-z0-9._-]*$' || die "backup image reference is invalid"
    printf '%s\n' "$target_rollback_ref" | grep -Eq '^r730xd-fan-web:rollback-[0-9]{8}-[0-9]{6}$|^r730xd-fan-web:pre-rollback-[0-9]{8}-[0-9]{6}$' || die "backup rollback image reference is invalid"
    actual_target_id=$(docker image inspect -f '{{.Id}}' "$target_rollback_ref" 2>/dev/null || true)
    [ "$actual_target_id" = "$target_image_id" ] || \
        die "backup image $target_rollback_ref is missing"
    TARGET_HAS_IMAGE=1
fi

STAMP=$(date +%Y%m%d-%H%M%S)
PRE_DIR="$APP_DIR/backups/pre-rollback-$STAMP"
[ ! -e "$PRE_DIR" ] || die "pre-rollback path already exists; retry in one second"
mkdir "$PRE_DIR"
chmod 700 "$PRE_DIR"
cp -p "$APP_DIR/compose.yaml" "$PRE_DIR/compose.yaml"
cp -p "$APP_DIR/.env" "$PRE_DIR/.env"
cp -p /etc/config/firewall "$PRE_DIR/firewall"

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    current_image_ref=$(docker inspect -f '{{.Config.Image}}' "$CONTAINER_NAME")
    current_image_id=$(docker inspect -f '{{.Image}}' "$CONTAINER_NAME")
    current_rollback_ref="r730xd-fan-web:pre-rollback-$STAMP"
    docker image tag "$current_image_id" "$current_rollback_ref"
    printf '%s\n' "$current_image_id" > "$PRE_DIR/image.id"
    printf '%s\n' "$current_image_ref" > "$PRE_DIR/image.original_ref"
    printf '%s\n' "$current_rollback_ref" > "$PRE_DIR/image.rollback_ref"
fi
chmod 600 "$PRE_DIR"/*

MUTATED=1
if [ "$TARGET_HAS_IMAGE" -eq 1 ]; then
    docker image tag "$target_rollback_ref" "$target_original_ref"
else
    warn "selected legacy backup has no image metadata; restoring configuration only"
fi

atomic_replace "$BACKUP_DIR/compose.yaml" "$APP_DIR/compose.yaml" || die "unable to restore compose.yaml"
atomic_replace "$BACKUP_DIR/.env" "$APP_DIR/.env" || die "unable to restore .env"
uci -q revert firewall >/dev/null 2>&1 || true
atomic_replace "$BACKUP_DIR/firewall" /etc/config/firewall || die "unable to restore firewall"
chmod 600 "$APP_DIR/compose.yaml" "$APP_DIR/.env"

fw4 check >/dev/null || die "backup firewall configuration failed validation"
/etc/init.d/firewall reload >/dev/null
compose_current config -q || die "restored Compose configuration is invalid"
compose_current up -d --no-build --force-recreate || die "unable to start the backed-up Compose configuration"

attempt=0
health=""
while [ "$attempt" -lt 45 ]; do
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)
    [ "$health" != "healthy" ] || break
    case "$health" in exited|dead) break ;; esac
    sleep 2
    attempt=$((attempt + 1))
done
[ "$health" = "healthy" ] || die "rolled-back container health is $health"

SUCCESS=1
log "rollback complete: $BACKUP_DIR"
log "pre-rollback recovery point: $PRE_DIR"
log "No fan-control command was sent."
