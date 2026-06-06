# ChatBI Arena (POC — Measurement Core)

A benchmark harness for **natural-language-to-SQL agents** over ClickHouse Cloud (AWS).
It runs a grid of `{Bedrock model} × {prompt strategy}` against a golden question set,
grades each answer by **execution accuracy** (result-set comparison, not SQL text), and
ranks configs by **cost-per-correct-answer**. Rich traces go to LangFuse Cloud; the
leaderboard is rendered from a ClickHouse results table (the single source of truth).

> **Status:** M0–M3 (measurement core) complete. M4 full grid · M5 Aurora+ClickPipes CDC ·
> M6 ClickStack/OTel · M7 serving `/ask` API are follow-on plans.

## Architecture (this POC)

- **Cloud:** ClickHouse Cloud on AWS `ap-southeast-1` · AWS Bedrock (Converse API) ·
  LangFuse Cloud · (later) Aurora + ClickPipes CDC + managed ClickStack.
- **Local:** data generator, benchmark harness, dashboard.
- **Stable contract:** the `v_*` views. Today they sit over seed tables loaded directly;
  later they repoint at ClickPipes CDC tables — agents never notice.
- **Read-only safety:** agent SQL runs as a `readonly=1` ClickHouse user with server-side
  resource limits, behind a SELECT-only validator (`agents/sqlguard.py`).

## Quickstart

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# .env already holds CLICKHOUSE_CLOUD_*, LANGFUSE_*, ARENA_RO_PASSWORD.
# For the real harness you also need AWS creds for ap-southeast-1 Bedrock:
#   export AWS_PROFILE=<your-profile-with-bedrock-access>
source .env

python scripts/check_connectivity.py     # CH + LangFuse + Bedrock reachability
python scripts/setup_clickhouse.py        # create db + read-only user
python -m datagen.generator --seed 42     # seed ~90 days of e-commerce data
python schema/gen_schema_context.py       # apply v_* views + write schema_context.md
pytest -q                                 # 32 unit tests (grading, sqlguard, config, ...)
python -m eval.harness                    # run the grid (needs Bedrock access)

uvicorn dashboard.app:app --port 8000     # → http://localhost:8000
```

If Bedrock access is not yet granted, populate the dashboard with a clearly-labeled
synthetic run to validate the full pipeline:

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
| `dashboard/` | FastAPI + static leaderboard (reads ClickHouse only) |
| `scripts/` | connectivity check, ClickHouse setup, mock results |
| `docs/superpowers/` | design spec + implementation plan |

## Docs
- Spec: `docs/superpowers/specs/2026-06-06-chatbi-arena-poc-design.md`
- Plan: `docs/superpowers/plans/2026-06-06-chatbi-arena-measurement-core.md`
