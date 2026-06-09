#!/usr/bin/env bash
# ChatBI Arena lifecycle orchestrator.
#
#   scripts/arena.sh up               full stack: Aurora + ClickPipes CDC + ClickStack
#   scripts/arena.sh up --seed-only   measurement core only (ClickHouse seed, no AWS)
#   scripts/arena.sh down             tear down billable infra (pipe + Aurora) + collector
#   scripts/arena.sh down --purge     also drop ClickHouse arena_cdc/arena_house + RO user
#   scripts/arena.sh serve            (re)start the dashboard API + web UI (no Terraform)
#   scripts/arena.sh serve --api-only (re)start ONLY the backend dashboard API — fastest
#   scripts/arena.sh stop             stop just the local servers (leave infra up)
#   scripts/arena.sh status           show what's running
#
# Requires: .venv (deps installed), .env (CLICKHOUSE_*, LANGFUSE_*, CH_CLOUD_*,
# ARENA_RO_PASSWORD). For AWS steps: a valid SSO session (aws sso login --profile sa).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export AWS_PROFILE="${AWS_PROFILE:-sa}"
# Put the repo root on sys.path so file-path scripts (scripts/*.py, schema/*.py)
# can `import arena` / `agents` / `eval`, not just the `-m` / `-c` invocations.
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
PY="$ROOT/.venv/bin/python"
TF="terraform -chdir=$ROOT/infra/terraform"
CP_IPS='["13.215.65.134/32","18.139.118.108/32","47.130.197.47/32","54.251.134.219/32","54.254.98.29/32","54.255.153.106/32"]'
RUN_DIR="$ROOT/.run"

log() { printf '\n\033[1;33m▶ %s\033[0m\n' "$*"; }
load_env() { set -a; . ./.env; set +a; }

# Start the dashboard JSON API (:$API_PORT, default 8000) + the React web UI (:5174) in the
# background; logs in .run/. Idempotent (kills prior instances first).
start_servers() {
  local api_only="${1:-}" ok_api=0 ok_web=0 i
  # Backend port is configurable: API_PORT=9000 scripts/arena.sh serve
  local api_port="${API_PORT:-8000}"
  mkdir -p "$RUN_DIR"
  log "Starting dashboard API (:$api_port)$([ "$api_only" = "--api-only" ] || echo ' + web UI (:5174)')"
  pkill -f "uvicorn dashboard.app" 2>/dev/null || true
  ( cd "$ROOT" && PYTHONPATH="$ROOT" nohup "$PY" -m uvicorn dashboard.app:app \
      --port "$api_port" --log-level warning >"$RUN_DIR/dashboard-api.log" 2>&1 & )
  # -fs => only a real 2xx counts (a foreign app 404ing on the port is NOT "up").
  for i in $(seq 1 30); do curl -fs -o /dev/null "localhost:$api_port/api/runs" && { ok_api=1; break; }; sleep 1; done
  echo "  dashboard API : http://localhost:$api_port  [$([ $ok_api = 1 ] && echo ready || echo 'NOT up')]  (.run/dashboard-api.log)"
  [ $ok_api = 1 ] || echo "  ⚠ dashboard API didn't come up — is port $api_port already in use? check .run/dashboard-api.log"

  if [ "$api_only" = "--api-only" ]; then return; fi

  pkill -f "vite" 2>/dev/null || true
  [ -d "$ROOT/web/node_modules" ] || ( cd "$ROOT/web" && npm install )
  # point the web UI at whatever port the API bound to
  ( cd "$ROOT/web" && VITE_API_BASE="http://localhost:$api_port" nohup npm run dev -- --port 5174 >"$RUN_DIR/web.log" 2>&1 & )
  for i in $(seq 1 45); do curl -fs -o /dev/null localhost:5174 && { ok_web=1; break; }; sleep 1; done
  echo "  web UI        : http://localhost:5174  [$([ $ok_web = 1 ] && echo ready || echo 'NOT up')]  (.run/web.log)"
}

stop_servers() {
  pkill -f "uvicorn dashboard.app" 2>/dev/null || true
  pkill -f "uvicorn serving.api" 2>/dev/null || true
  pkill -f "vite" 2>/dev/null || true
}

require_venv() { [ -x "$PY" ] || { echo "missing .venv — python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }; }
require_aws() {
  aws sts get-caller-identity >/dev/null 2>&1 || {
    echo "AWS creds invalid for profile '$AWS_PROFILE' — run: aws sso login --profile $AWS_PROFILE"; exit 1; }
}

up_full() {
  require_venv; load_env; require_aws
  log "Provisioning Aurora (Terraform)"
  $TF init -input=false >/dev/null
  $TF apply -input=false -auto-approve \
    -var "admin_ingress_cidr=$(curl -s ifconfig.me)/32" \
    -var "clickpipes_ingress_cidrs=$CP_IPS"
  export AURORA_DSN="$($TF output -raw aurora_dsn)"

  log "ClickHouse: database + read-only user + results tables + arena_cdc db"
  $PY scripts/setup_clickhouse.py
  $PY -c "from arena.config import load_config; from agents.chclient import make_admin_client; from eval.results import ensure_results_tables; c=load_config(); a=make_admin_client(c.clickhouse, database='default'); a.command('CREATE DATABASE IF NOT EXISTS arena_cdc'); ensure_results_tables(make_admin_client(c.clickhouse), c.clickhouse.database); print('clickhouse objects ready')"

  log "Seeding Aurora + CDC role/publication"
  $PY -m datagen.generator --target aurora --seed "${SEED:-42}"
  $PY scripts/setup_aurora_cdc.py

  log "Creating ClickPipes CDC pipe + waiting for Running"
  # ClickPipes won't target existing non-empty tables. A deleted pipe leaves its
  # landed tables behind, so reset the arena_cdc landing DB unless a pipe is live.
  if $PY scripts/create_clickpipe.py --action status 2>/dev/null | grep -q 'state='; then
    echo "  pipe 'arena-cdc' already exists — reusing"
  else
    $PY -c "from arena.config import load_config; from agents.chclient import make_admin_client; a=make_admin_client(load_config().clickhouse, database='default'); a.command('DROP DATABASE IF EXISTS arena_cdc'); a.command('CREATE DATABASE arena_cdc'); print('reset arena_cdc landing database')"
    $PY scripts/create_clickpipe.py --action create
  fi
  $PY scripts/create_clickpipe.py --action wait

  log "Repointing v_* views onto CDC tables + granting read-only"
  $PY schema/repoint_views.py --source-db arena_cdc
  $PY -c "from arena.config import load_config; from agents.chclient import make_admin_client; make_admin_client(load_config().clickhouse, database='default').command('GRANT SELECT ON arena_cdc.* TO arena_ro'); print('granted RO on arena_cdc')"
  $PY schema/gen_schema_context.py --no-apply

  log "Starting ClickStack OTel collector"
  docker compose up -d

  start_servers

  log "UP complete (full CDC stack)."
  echo "  Open:    http://localhost:5174  (Architecture + Leaderboard tabs)"
  echo "  Grid:    AWS_PROFILE=$AWS_PROFILE $PY -m eval.harness --run-id grid-cdc   # populates the Leaderboard"
  echo "  Serving: AWS_PROFILE=$AWS_PROFILE $PY -m uvicorn serving.api:app --port 8100   # optional /ask"
}

up_seed_only() {
  require_venv; load_env
  log "ClickHouse: database + read-only user + results tables"
  $PY scripts/setup_clickhouse.py
  $PY -c "from arena.config import load_config; from agents.chclient import make_admin_client; from eval.results import ensure_results_tables; c=load_config(); ensure_results_tables(make_admin_client(c.clickhouse), c.clickhouse.database); print('results tables ready')"
  log "Seeding ClickHouse directly + views + schema context"
  $PY -m datagen.generator --seed "${SEED:-42}"
  $PY schema/gen_schema_context.py
  log "Starting ClickStack OTel collector"
  docker compose up -d
  start_servers
  log "UP complete (seed-only measurement core)."
  echo "  Open: http://localhost:5174  (run the harness to populate the Leaderboard)"
}

down() {
  require_venv; load_env
  log "Stopping local servers (web UI + dashboard/serving API)"
  stop_servers

  log "Stopping ClickStack collector"
  docker compose down 2>/dev/null || true

  if aws sts get-caller-identity >/dev/null 2>&1; then
    log "Deleting ClickPipes pipe"
    $PY scripts/create_clickpipe.py --action delete || true
    log "Destroying Aurora (Terraform)"
    $TF destroy -input=false -auto-approve \
      -var "admin_ingress_cidr=0.0.0.0/32" -var "clickpipes_ingress_cidrs=$CP_IPS" || true
  else
    echo "  (AWS session invalid — skipping pipe delete + Aurora destroy; run 'aws sso login --profile $AWS_PROFILE' then re-run)"
  fi

  if [ "${1:-}" = "--purge" ]; then
    log "Purging ClickHouse objects (arena_cdc, arena_house, arena_ro, otel_*)"
    $PY -c "
from arena.config import load_config; from agents.chclient import make_admin_client
a=make_admin_client(load_config().clickhouse, database='default')
for db in ['arena_cdc','arena_house']: a.command(f'DROP DATABASE IF EXISTS {db}')
a.command('DROP USER IF EXISTS arena_ro')
for t in [r[0] for r in a.query(\"SELECT name FROM system.tables WHERE database='default' AND (name LIKE 'otel_%' OR name LIKE 'hyperdx_%')\").result_rows]:
    a.command(f'DROP TABLE IF EXISTS default.{t}')
print('purged')
"
  fi
  log "DOWN complete."
}

status() {
  require_venv; load_env
  log "Aurora (Terraform)"
  $TF output -raw aurora_endpoint 2>/dev/null && echo "" || echo "  not provisioned"
  log "ClickPipes pipe"
  aws sts get-caller-identity >/dev/null 2>&1 && $PY scripts/create_clickpipe.py --action status || echo "  (AWS session invalid)"
  log "ClickStack collector"
  docker ps --filter name=arena-clickstack-collector --format '  {{.Names}} {{.Status}}' || true
  log "Web / API servers"
  curl -fs -o /dev/null "localhost:${API_PORT:-8000}/api/runs" && echo "  dashboard API :${API_PORT:-8000}  up" || echo "  dashboard API :${API_PORT:-8000}  down"
  curl -fs -o /dev/null localhost:5174 && echo "  web UI        :5174  up" || echo "  web UI        :5174  down"
  log "ClickHouse view counts"
  $PY -c "
from arena.config import load_config; from agents.chclient import make_admin_client
a=make_admin_client(load_config().clickhouse)
for v in ['v_customers','v_products','v_orders','v_order_items','v_events']:
    try: print(f'  {v:16} {a.query(f\"SELECT count() FROM {v}\").result_rows[0][0]}')
    except Exception as e: print(f'  {v:16} (n/a)')
try:
    runs=[r[0] for r in a.query('SELECT DISTINCT run_id FROM eval_runs').result_rows]
    print('  eval_runs:', runs or '(none)')
except Exception: print('  eval_runs: (none)')
"
}

case "${1:-}" in
  up)     [ "${2:-}" = "--seed-only" ] && up_seed_only || up_full ;;
  down)   down "${2:-}" ;;
  serve)  require_venv; load_env; start_servers "${2:-}" ;;
  stop)   stop_servers; echo "local servers stopped" ;;
  status) status ;;
  *) echo "usage: scripts/arena.sh {up [--seed-only] | down [--purge] | serve [--api-only] | stop | status}"; exit 1 ;;
esac
