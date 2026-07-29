#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONTROL_ROOT=/model/dockervolume/hku-custom-auth
TARGET_ROOT=/model/dockervolume/hku-custom-sso-v4
CONTROL_ENTRYPOINT=hku-auth-entrypoint
CONTROL_UI=hku-auth-ui
TARGET_PROJECT=hku-sso-v4
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CONTROL_UPDATE="$CONTROL_ROOT/updates/${STAMP}-authenticated-poc04-cutover"
TARGET_EVIDENCE="$TARGET_ROOT/evidence/${STAMP}-authenticated-poc04-cutover"
TARGET_COMPOSE=(
  "$TARGET_ROOT/scripts/compose_no_sso.sh"
  -p "$TARGET_PROJECT"
  -f "$TARGET_ROOT/compose.sso-poc04.g5680a.yml"
)

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

wait_http() {
  local url="$1"
  shift
  for _ in $(seq 1 90); do
    if curl "$@" -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

stage_read_only_file() {
  local source="$1"
  local target="$2"
  local mode="$3"
  local temporary="${target}.staging-${STAMP}"

  cp "$source" "$temporary"
  chmod "$mode" "$temporary"
  mv -f "$temporary" "$target"
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

restore_entrypoint() {
  if [[ -f "$CONTROL_UPDATE/before/auth-entrypoint.conf" ]]; then
    replace_control_entrypoint_config \
      "$CONTROL_UPDATE/before/auth-entrypoint.conf"
    docker exec "$CONTROL_ENTRYPOINT" nginx -t >/dev/null
    docker exec "$CONTROL_ENTRYPOINT" nginx -s reload >/dev/null
  fi
}

[[ "$(pwd -P)" == "$TARGET_ROOT" ]] \
  || fail "Run this script only from $TARGET_ROOT"
[[ "$ROOT" == "$TARGET_ROOT" ]] || fail "Unexpected script location"
[[ -d "$CONTROL_ROOT" && ! -L "$CONTROL_ROOT" ]] \
  || fail "The existing authenticated root is unavailable"
[[ -f "$CONTROL_ROOT/.env" ]] || fail "Existing authenticated environment is unavailable"
[[ -f "$CONTROL_ROOT/runtime/auth-entrypoint.conf" ]] \
  || fail "Existing authenticated entrypoint configuration is unavailable"
[[ -f .env.g5680a.sso-poc04.example ]] || fail "Candidate environment template is unavailable"

for name in \
  hku-auth-entrypoint \
  hku-auth-gateway \
  hku-auth-session \
  hku-auth-ui \
  hku-auth-agent-runtime; do
  [[ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" == "true" ]] \
    || fail "Existing authenticated control is not running: $name"
done

mkdir -p \
  "$CONTROL_UPDATE/before" \
  "$CONTROL_UPDATE/after" \
  "$TARGET_EVIDENCE" \
  certs \
  secrets
chmod 700 "$CONTROL_UPDATE" "$CONTROL_UPDATE/before" "$CONTROL_UPDATE/after" "$TARGET_EVIDENCE"

docker ps -a --no-trunc \
  --format '{{.ID}}|{{.Image}}|{{.Names}}|{{.Status}}|{{.Ports}}|{{.Label "com.docker.compose.project"}}' \
  > "$CONTROL_UPDATE/before/containers.txt"
ss -ltn > "$CONTROL_UPDATE/before/listeners.txt"
control_entrypoint_id="$(docker inspect -f '{{.Id}}' "$CONTROL_ENTRYPOINT")"
printf '%s\n' "$control_entrypoint_id" > "$CONTROL_UPDATE/before/entrypoint-id.txt"
cp "$CONTROL_ROOT/runtime/auth-entrypoint.conf" \
  "$CONTROL_UPDATE/before/auth-entrypoint.conf"
sha256sum "$CONTROL_UPDATE/before/auth-entrypoint.conf" \
  > "$CONTROL_UPDATE/before/auth-entrypoint.conf.sha256"

if [[ ! -f .env ]]; then
  cp .env.g5680a.sso-poc04.example .env
fi
chmod 600 .env

python3 - "$CONTROL_ROOT/.env" "$TARGET_ROOT/.env" <<'PY'
from pathlib import Path
import sys

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])

def read_env(path):
    values = {}
    order = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key] = value
        order.append(key)
    return values, order

source, _ = read_env(source_path)
target, order = read_env(target_path)
required = ("AUTH_OIDC_CLIENT_ID", "DEEPSEEK_API_KEY")
for key in required:
    value = source.get(key, "")
    if not value or value.startswith("REPLACE_WITH_"):
        raise SystemExit(f"ERROR: Existing authenticated environment has no usable {key}")
    target[key] = value
for key in ("DEEPSEEK_BASE_URL",):
    if source.get(key):
        target[key] = source[key]

seen = set()
lines = []
for key in order:
    if key in seen:
        continue
    seen.add(key)
    lines.append(f"{key}={target[key]}")
for key, value in target.items():
    if key not in seen:
        lines.append(f"{key}={value}")
target_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

stage_read_only_file \
  "$CONTROL_ROOT/secrets/oidc-client-secret" \
  secrets/oidc-client-secret \
  444
stage_read_only_file "$CONTROL_ROOT/certs/tls.crt" certs/tls.crt 444
stage_read_only_file "$CONTROL_ROOT/certs/tls.key" certs/tls.key 400

./PrepareSsoPOC04G5680A.sh
./InstallSsoPOC04G5680A.sh
./StartSsoPOC04G5680A.sh

docker exec "$CONTROL_UI" python -c \
  "import sqlite3; s=sqlite3.connect('/data/hku_ui.sqlite3'); d=sqlite3.connect('/data/hku_ui.poc04-transfer.sqlite3'); s.backup(d); d.close(); s.close()"
"${TARGET_COMPOSE[@]}" stop web-ui
mkdir -p "$TARGET_EVIDENCE/candidate-ui-before-transfer"
find data/ui -mindepth 1 -maxdepth 1 -exec \
  mv -t "$TARGET_EVIDENCE/candidate-ui-before-transfer" -- {} +
docker cp \
  "$CONTROL_UI:/data/hku_ui.poc04-transfer.sqlite3" \
  data/ui/hku_ui.sqlite3
chmod 600 data/ui/hku_ui.sqlite3
if command -v setfacl >/dev/null 2>&1; then
  setfacl -m u:1000:rw- data/ui/hku_ui.sqlite3
fi
"${TARGET_COMPOSE[@]}" up -d --no-deps web-ui
wait_http "http://127.0.0.1:28180/healthz" \
  || fail "Candidate UI did not recover after the consistent history transfer"

cat > "$CONTROL_UPDATE/after/auth-entrypoint.conf.candidate" <<'EOF'
server {
    listen 443 ssl default_server;
    server_name _;

    ssl_certificate /run/tls/tls.crt;
    ssl_certificate_key /run/tls/tls.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    return 421;
}

server {
    listen 443 ssl;
    server_name curr-planner.hku.hk;

    ssl_certificate /run/tls/tls.crt;
    ssl_certificate_key /run/tls/tls.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    location / {
        proxy_pass https://127.0.0.1:28380;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Connection "";
        proxy_request_buffering on;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        proxy_ssl_server_name on;
        proxy_ssl_name curr-planner.hku.hk;
        proxy_ssl_trusted_certificate /run/tls/tls.crt;
        proxy_ssl_verify on;
        proxy_ssl_verify_depth 2;
    }
}
EOF
chmod 600 "$CONTROL_UPDATE/after/auth-entrypoint.conf.candidate"

trap restore_entrypoint ERR INT TERM
replace_control_entrypoint_config \
  "$CONTROL_UPDATE/after/auth-entrypoint.conf.candidate"
docker exec "$CONTROL_ENTRYPOINT" nginx -t
docker exec "$CONTROL_ENTRYPOINT" nginx -s reload

wait_http "https://curr-planner.hku.hk/__health" \
  --resolve "curr-planner.hku.hk:443:127.0.0.1" \
  --cacert "$CONTROL_ROOT/certs/tls.crt" \
  || fail "The refreshed 443 route did not become healthy"

login_status="$(
  curl -sS -o /dev/null -w '%{http_code}' \
    --resolve "curr-planner.hku.hk:443:127.0.0.1" \
    --cacert "$CONTROL_ROOT/certs/tls.crt" \
    "https://curr-planner.hku.hk/oauth2/start"
)"
[[ "$login_status" == "302" ]] || fail "OIDC login initiation returned HTTP $login_status"
[[ "$(docker inspect -f '{{.Id}}' "$CONTROL_ENTRYPOINT")" == "$control_entrypoint_id" ]] \
  || fail "The existing 443 entrypoint container identity changed"

for check in \
  "18380 http://127.0.0.1:18380/healthz" \
  "20380 http://127.0.0.1:20380/healthz" \
  "18890 http://127.0.0.1:18890/health"; do
  label="${check%% *}"
  url="${check#* }"
  wait_http "$url" || fail "Protected control failed after cutover: $label"
done

docker ps -a --no-trunc \
  --format '{{.ID}}|{{.Image}}|{{.Names}}|{{.Status}}|{{.Ports}}|{{.Label "com.docker.compose.project"}}' \
  > "$CONTROL_UPDATE/after/containers.txt"
ss -ltn > "$CONTROL_UPDATE/after/listeners.txt"
sha256sum "$CONTROL_ROOT/runtime/auth-entrypoint.conf" \
  > "$CONTROL_UPDATE/after/auth-entrypoint.conf.sha256"
printf '%s\n' "$CONTROL_UPDATE" > "$CONTROL_ROOT/updates/authenticated-poc04-last-cutover"
chmod 600 "$CONTROL_ROOT/updates/authenticated-poc04-last-cutover"
trap - ERR INT TERM

echo "Authenticated POC04 is active through the existing 443 listener."
echo "Rollback evidence: $CONTROL_UPDATE"
