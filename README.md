# ChatBI Arena (POC — Measurement Core)

A benchmark harness for **natural-language-to-SQL agents** over ClickHouse Cloud (AWS).
It runs a grid of `{Bedrock model} × {prompt strategy}` against a golden question set,
grades each answer by **execution accuracy** (result-set comparison, not SQL text), and
ranks configs by **cost-per-correct-answer**. Rich traces go to LangFuse Cloud; the
leaderboard is rendered from a ClickHouse results table (the single source of truth).

> **Status:** M0–M7 complete and live. M5 (Aurora + ClickPipes CDC) is provisioned end
> to end: Aurora Serverless v2 → ClickPipes Postgres CDC → `arena_cdc` tables, with the
> `v_*` views repointed onto them (verified: a status UPDATE in Aurora propagates to the
> views while the row count stays constant — ReplacingMergeTree dedup). See
> `infra/README_clickpipes.md`. Tear down with `terraform destroy` to stop billing.

## Architecture (this POC)

- **Cloud:** ClickHouse Cloud on AWS `ap-southeast-1` · AWS Bedrock (Converse API) ·
  LangFuse Cloud · (later) Aurora + ClickPipes CDC + managed ClickStack.
- **Local:** data generator, benchmark harness, dashboard API, web UI.
- **Stable contract:** the `v_*` views. Today they sit over seed tables loaded directly;
  later they repoint at ClickPipes CDC tables — agents never notice.
- **Read-only safety:** agent SQL runs as a `readonly=1` ClickHouse user with server-side
  resource limits, behind a SELECT-only validator (`agents/sqlguard.py`).

## One-command lifecycle

```bash
scripts/arena.sh up               # full stack: Aurora + ClickPipes CDC + ClickStack collector
scripts/arena.sh up --seed-only   # measurement core only (ClickHouse seed, no AWS/Aurora)
scripts/arena.sh status           # show Aurora / pipe / collector / view counts
scripts/arena.sh down             # tear down billable infra (pipe + Aurora) + collector
scripts/arena.sh down --purge     # also drop ClickHouse arena_cdc/arena_house + RO user + otel_*
```
`up`/`down` are idempotent and use `AWS_PROFILE` (default `sa`); they need a valid
SSO session (`aws sso login --profile sa`). The manual steps below are what
`arena.sh` automates, for reference.

## Quickstart

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# .env already holds CLICKHOUSE_CLOUD_*, LANGFUSE_*, ARENA_RO_PASSWORD.
# For the real harness you also need AWS creds for ap-southeast-1 Bedrock.
# Verified working: the `sa` SSO profile (account 959934561610, SolutionArchitect).
#   aws sso login --profile sa
#   export AWS_PROFILE=sa
# NOTE: the 164313782301_AccountAdministrators federated-user (acct 888961321587)
# is hard-denied Bedrock by an identity-based policy — do not use it.
source .env

python scripts/check_connectivity.py     # CH + LangFuse + Bedrock reachability
python scripts/setup_clickhouse.py        # create db + read-only user
python -m datagen.generator --seed 42     # seed ~90 days of e-commerce data
python schema/gen_schema_context.py       # apply v_* views + write schema_context.md
pytest -q                                 # 32 unit tests (grading, sqlguard, config, ...)
AWS_PROFILE=sa python -m eval.harness     # run the grid against real Bedrock
```

### Web UI (Architecture diagram + Leaderboard)
A single React SPA in `web/` with two tabs. The Leaderboard tab reads from the
FastAPI JSON API (`dashboard/app.py`); the Architecture tab needs nothing.
```bash
uvicorn dashboard.app:app --port 8000     # JSON API for the Leaderboard tab
cd web && npm install && npm run dev      # → http://localhost:5174  (both tabs)
```

### Observability (ClickStack) + live serving
```bash
docker compose up -d                                   # ClickStack OTel collector
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
  python -m observability.cdc_freshness --watch 15     # CDC freshness gauge → ClickStack
AWS_PROFILE=sa OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
  uvicorn serving.api:app --port 8100                  # live /ask endpoint
curl -s localhost:8100/ask -H 'content-type: application/json' \
  -d '{"question":"How many customers are there?","config_id":"nova-lite__P1_zeroshot"}'
```
Telemetry lands in the same ClickHouse service (`default.otel_*` tables) — one
engine for the data, the telemetry, and the AI's behavior.

### Live data pipeline (M5)
See `infra/README_clickpipes.md`: `terraform apply` Aurora, seed it
(`--target aurora`), create the ClickPipes pipe, then `schema/repoint_views.py`
swaps `v_*` onto the CDC tables — agents/leaderboard unchanged.

If Bedrock access is unavailable, populate the leaderboard with a clearly-labeled
synthetic run to validate the full pipeline (the UI badges it as synthetic):

```bash
python scripts/seed_mock_results.py       # writes a mock-* run to eval_runs
```

## Layout

| Path | What |
|------|------|
| `arena/config.py` | typed config loader (`config.yaml` + `${ENV}`) |
| `agents/` | `chclient` (RO/admin), `sqlguard`, `bedrock`, `prompts` (P1–P5), `loop` |
| `eval/` | `grading` (execution accuracy), `golden`, `results` (eval_runs + leaderboard), `langfuse_adapter`, `harness` |
| `datagen/generator.py` | synthetic e-commerce → ClickHouse seed tables |
| `schema/` | seed tables, `v_*` views, generated schema context |
| `golden/questions.yaml` | 20 golden questions across 5 difficulty tiers |
| `dashboard/app.py` | FastAPI JSON API for the leaderboard (reads ClickHouse only) |
| `web/` | React SPA — Architecture diagram + Leaderboard tabs (Vite + React Flow) |
| `scripts/` | connectivity check, ClickHouse setup, mock results, `arena.sh` lifecycle |
| `infra/terraform/` | Aurora + CDC IaC; `infra/README_clickpipes.md` runbook |
| `docs/superpowers/` | design spec + implementation plan |

## Docs
- Spec: `docs/superpowers/specs/2026-06-06-chatbi-arena-poc-design.md`
- Plan: `docs/superpowers/plans/2026-06-06-chatbi-arena-measurement-core.md`
