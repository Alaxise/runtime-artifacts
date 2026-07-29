#!/usr/bin/env bash
set -euo pipefail

CONTROL_ROOT=/model/dockervolume/hku-custom-auth
CONTROL_ENTRYPOINT=hku-auth-entrypoint
POINTER="$CONTROL_ROOT/updates/authenticated-poc04-last-cutover"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

replace_control_entrypoint_config() {
  local source="$1"
  local target="$CONTROL_ROOT/runtime/auth-entrypoint.conf"

  chmod 600 "$target"
  if ! cp "$source" "$target"; then
    chmod 400 "$target"
    return 1
  fi
  chmod 400 "$target"
}

[[ -f "$POINTER" ]] || fail "No authenticated POC04 cutover record is available"
update_dir="$(cat "$POINTER")"
case "$update_dir" in
  "$CONTROL_ROOT"/updates/*-authenticated-poc04-cutover) ;;
  *) fail "The recorded rollback directory is outside the authorized boundary" ;;
esac
before="$update_dir/before/auth-entrypoint.conf"
[[ -f "$before" ]] || fail "The previous entrypoint configuration is unavailable"
[[ "$(docker inspect -f '{{.State.Running}}' "$CONTROL_ENTRYPOINT" 2>/dev/null)" == "true" ]] \
  || fail "The existing 443 entrypoint container is not running"

cp "$CONTROL_ROOT/runtime/auth-entrypoint.conf" \
  "$update_dir/after/auth-entrypoint.conf.before-rollback"
replace_control_entrypoint_config "$before"
docker exec "$CONTROL_ENTRYPOINT" nginx -t
docker exec "$CONTROL_ENTRYPOINT" nginx -s reload

for _ in $(seq 1 60); do
  if curl -fsS \
    --resolve "curr-planner.hku.hk:443:127.0.0.1" \
    --cacert "$CONTROL_ROOT/certs/tls.crt" \
    "https://curr-planner.hku.hk/__health" >/dev/null 2>&1; then
    echo "The previous authenticated 443 route has been restored."
    exit 0
  fi
  sleep 2
done

fail "The previous authenticated 443 route did not recover"
