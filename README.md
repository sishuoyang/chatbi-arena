# ChatBI Arena

**A benchmark rig that *measures* natural-language-to-SQL agents — instead of trusting a demo.**

ChatBI Arena runs a grid of **{Bedrock model} × {prompt strategy}** against a
ground-truthed set of business questions over a live ClickHouse dataset, grades
every answer by **execution accuracy** (did the query return the right *result*,
not the right *SQL text*), and ranks the configurations by **cost per correct
answer**. It answers, with evidence:

> *Which model + prompt should we ship for NL→SQL, and what does correctness cost?*

The data arrives the way it would in production: synthetic e-commerce OLTP is
generated into **Aurora PostgreSQL** and replicated to **ClickHouse Cloud** via
**ClickPipes CDC**. The AI's behavior is traced in **LangFuse**; system/serving
telemetry flows to **ClickStack** — so one ClickHouse service holds the business
data, the operational telemetry, *and* the AI's behavior.

> **Status:** working POC. The full pipeline (Aurora → ClickPipes CDC → ClickHouse,
> Bedrock agents, grading, LangFuse, ClickStack, web UI) has been run end-to-end.

---

## What you get

A single web app (`web/`, http://localhost:5174) with two tabs:

- **Leaderboard** — every model×prompt config ranked by accuracy, **cost-per-correct-answer**
  (the headline), latency, per-tier accuracy, and an outcome breakdown. Click any
  config to **drill into its per-question results**, each linking to its **LangFuse
  trace** (prompt → generated SQL → error → tokens → span timings). A **"View
  conversation"** button replays the agent's session **live from the LangFuse API**,
  an **LLM-judge** column scores SQL quality, and links jump to LangFuse's native
  **Experiment** comparison and **Session** views.
- **Architecture** — an animated React Flow diagram of the whole system.

Plus a live **`/ask` API**: pose a question, pick a config, watch the SQL, cost, and
latency change.

(Screenshots: `web/preview-leaderboard.png`, `web/preview-conversation.png`, `web/preview.png`.)

---

## Architecture

```
  LOCAL / Docker (your machine)        AWS ap-southeast-1          ClickHouse Cloud (on AWS)
  ────────────────────────────         ──────────────────         ─────────────────────────
  data generator ───insert/update────► Aurora PostgreSQL ──┐
                                                            │ ClickPipes CDC (logical repl.)
                                                            ▼
  agent core  ───Converse (NL→SQL)───► Amazon Bedrock       arena_cdc tables (ReplacingMergeTree)
  (loop · P1–P5 · SQL guard)                                        │  FINAL + dedup
        │  read-only SELECT ───────────────────────────────►  v_* analytic views  ◄── the contract
        ▼                                                            ▲
  benchmark harness ──grade vs golden──► writes ──────────►  arena.eval_runs / v_leaderboard
        │                                                            ▲
        └──traces · scores · datasets/experiments · sessions──► LangFuse Cloud
  serving /ask · dashboard API · web UI                              │ reads
  OTel collector (ClickStack) ──telemetry──────────────────►  otel_* / ClickStack tables
```

**The key idea — two lenses, one engine:**
- **ClickHouse** answers *"which config wins / is the system healthy"* — it holds the
  business data, the `eval_runs` results table (the leaderboard's source of truth), and
  the ClickStack telemetry.
- **LangFuse** answers *"why did it fail / how did it converse / how do runs compare"* —
  full traces, scores, an LLM-judge, and Datasets/Experiments.

**The stable contract is the `v_*` views.** Agents and golden SQL only ever query
`v_orders`, `v_customers`, etc. — views that apply `FINAL` (dedup the CDC
`ReplacingMergeTree` tables) and hide soft-deleted rows. The views' *source* can swap
(direct seed tables ↔ ClickPipes CDC tables) without the agents noticing.

**Read-only safety (agents run model-generated SQL):** queries execute as a
ClickHouse `readonly=1` user with server-side resource limits, behind a SELECT-only
validator (`agents/sqlguard.py`) that rejects anything that isn't a single
`SELECT`/`WITH … SELECT`.

**How a config is scored:** the harness sends the question (built per prompt strategy)
to Bedrock, extracts the SQL, runs it against the `v_*` views, and compares the result
set to the cached golden result (by column position, with float rounding and row-set
normalization). It records correctness, cost (Bedrock tokens × configured prices),
latency, and an LLM-judge score — to ClickHouse `eval_runs` *and* LangFuse.

---

## Repository layout

| Path | What |
|------|------|
| `config.yaml` | models, prompt strategies, the grid to run, pricing, eval settings |
| `arena/config.py` | typed config loader (`config.yaml` + `${ENV}` expansion) |
| `agents/` | `bedrock` (Converse), `prompts` (P1–P5), `sqlguard`, `chclient` (RO/admin), `loop` (the agent) |
| `eval/` | `grading` (execution accuracy), `golden`, `results` (`eval_runs`), `langfuse_adapter`, `judge`, `harness` (grid runner) |
| `golden/questions.yaml` | the golden question set (5 difficulty tiers) |
| `datagen/generator.py` | synthetic e-commerce generator (→ Aurora or ClickHouse) |
| `schema/` | seed tables, `v_*` views, view-repoint + schema-context generators |
| `dashboard/app.py` | FastAPI JSON API (ClickHouse leaderboard + LangFuse-backed endpoints) |
| `web/` | React SPA — Architecture diagram + Leaderboard tabs (Vite + React Flow) |
| `serving/api.py` | live `/ask` Chat-BI endpoint (reuses the agent core) |
| `observability/` | OTel instrumentation + CDC-freshness gauge |
| `infra/terraform/` | Aurora + networking IaC · `infra/README_clickpipes.md` is the CDC runbook |
| `scripts/` | `arena.sh` lifecycle + setup/connectivity/ClickPipes helpers |
| `docs/superpowers/` | the original design spec + implementation plan |

---

## Getting started

### Prerequisites
- **Python 3.11**, **Node 18+** (for the web UI), and **Docker** (for the ClickStack collector; optional).
- A **ClickHouse Cloud** service, a **LangFuse Cloud** project, and **AWS Bedrock**
  access in `ap-southeast-1` with the desired models enabled (Bedrock → Model access).
- The full live pipeline also needs the **AWS** + **ClickHouse Cloud API** credentials
  to provision Aurora and the ClickPipe (see below). You can skip that and run the
  **measurement core** against ClickHouse directly.

### 1. Install
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
(cd web && npm install)        # only if you want the web UI
```

### 2. Create `.env` (not committed)
```bash
# ClickHouse Cloud (the analytic store)
export CLICKHOUSE_CLOUD_HOST=xxxx.ap-southeast-1.aws.clickhouse.cloud
export CLICKHOUSE_CLOUD_USER=default
export CLICKHOUSE_CLOUD_PASSWORD=...
export CLICKHOUSE_CLOUD_DATABASE=arena_house
export ARENA_RO_PASSWORD=...                 # password for the read-only agent user (created by setup)

# LangFuse Cloud (agent tracing/scores/experiments)
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
export LANGFUSE_BASE_URL=https://us.cloud.langfuse.com

# ClickHouse Cloud organization API key — only for creating the ClickPipe via REST
export CH_CLOUD_KEY_ID=...
export CH_CLOUD_KEY_SECRET=...
```
AWS credentials are **not** in `.env` — use the standard AWS chain:
```bash
aws sso login --profile <your-profile>       # a profile with Bedrock access in ap-southeast-1
export AWS_PROFILE=<your-profile>
```

### 3. Verify connectivity
```bash
source .env
python scripts/check_connectivity.py          # ClickHouse + LangFuse + Bedrock reachable?
```

### 4. Run it

**Option A — measurement core only (no AWS data plane).** Seeds ClickHouse directly,
starts the dashboard API + web UI:
```bash
scripts/arena.sh up --seed-only
AWS_PROFILE=<profile> python -m eval.harness --run-id demo   # runs the grid (needs Bedrock)
# open http://localhost:5174  → Leaderboard tab → select run "demo"
```

**Option B — full live pipeline (provisions billable AWS Aurora + ClickPipes CDC):**
```bash
scripts/arena.sh up                                          # Terraform Aurora → CDC → views → collector → servers
AWS_PROFILE=<profile> python -m eval.harness --run-id demo   # runs the grid
# open http://localhost:5174
scripts/arena.sh down                                        # ⚠ tear down to stop Aurora billing
```

`arena.sh up` is idempotent and also launches the dashboard API (`:8000`) and web UI
(`:5174`) in the background (logs in `.run/`). The full Aurora→ClickPipes setup is
documented step-by-step in [`infra/README_clickpipes.md`](infra/README_clickpipes.md).

### Lifecycle commands
```bash
scripts/arena.sh up [--seed-only]   # bring the stack up (full pipeline | ClickHouse-only)
scripts/arena.sh serve              # (re)start just the dashboard API + web UI
scripts/arena.sh stop               # stop just the local servers
scripts/arena.sh status             # Aurora / pipe / collector / servers / row counts
scripts/arena.sh down [--purge]     # tear down infra (+ optionally drop ClickHouse objects)
```

### Running the benchmark
```bash
AWS_PROFILE=<profile> python -m eval.harness --run-id <name> [--no-judge] [--limit N]
```
- Each invocation is a **run** (`run_id`); it writes one row per *(config × question)* to
  `arena.eval_runs` and a trace per run to LangFuse. The web UI's run selector picks which run to view.
- The grid (which models × which prompts) is defined in `config.yaml`.
- `--judge` (default on) adds a cheap LLM-judge score; `--limit N` runs the first N questions.

---

## Configuration

Everything tunable lives in [`config.yaml`](config.yaml):
- **`models`** — Bedrock model id, display name, and per-1M-token input/output prices (used for cost).
- **`prompts`** — the strategies `P1_zeroshot`, `P2_fewshot`, `P3_dialect`, `P4_cot`, `P5_selfcorrect`.
- **`grid`** — which models × prompts to actually run (`["*"]` = all).
- **`clickhouse.query_limits`** — the server-side caps enforced on agent SQL.

Adding a model or prompt is a config edit, not a code change.

---

## Cost to run the demo

Rough estimates for **ap-southeast-1** (~mid-2026 on-demand rates; verify against
current pricing). The dataset is tiny (~100k rows), so storage/I/O are negligible —
the costs that matter are Aurora compute and Bedrock tokens.

| Component | Cost | Notes |
|---|---|---|
| **Bedrock** (Converse) | **~$0.40 per full grid run** · <$0.01 per cheap-model run · ~$0.0001–0.005 per live `/ask` | Measured: 4 models × 5 prompts × 18 Qs = 360 calls. Claude Sonnet is ~$0.34 of it; Nova/Haiku are pennies. |
| **Aurora PostgreSQL Serverless v2** | **~$0.07–0.14 / hour** running | 0.5–2 ACU; mostly idle at min capacity. The main "left running" cost (~$1.5–3/day). |
| **ClickPipes CDC + ClickHouse Cloud** | small while running | Metered by ClickHouse Cloud; minor for this dataset. Your service has its own baseline (scales to zero when idle). |
| **LangFuse Cloud** | **$0** | Free tier covers a few hundred traces per run. |
| **ClickStack collector** | **$0** | Local Docker container; writes into your existing ClickHouse service. |
| **Local** (data-gen, harness, serving, web UI) | **$0** | Runs on your machine. |

**Summary.** A full end-to-end demo (`up` → one grid → browse → `down`) within ~2 hours
is about **$1–2**, dominated by one Bedrock grid (~$0.40) plus a couple of Aurora
compute-hours. The measurement-core path (`up --seed-only`, one cheap model) is
**under $0.05**. The biggest avoidable cost is leaving **Aurora + the ClickPipe running**
— always finish with `scripts/arena.sh down`.

---

## Troubleshooting

- **Bedrock `AccessDeniedException`** — the model isn't enabled, or the profile/role is
  denied `bedrock:InvokeModel`. Enable model access in the Bedrock console
  (ap-southeast-1) and use a profile/role that allows it. Some federated/SSO sessions
  are scoped down; pick one with Bedrock rights. Nova requires the **inference profile**
  id (e.g. `apac.amazon.nova-lite-v1:0`), not the bare model id.
- **Port 8000 in use** — the dashboard API uses `:8000`; `arena.sh up` warns if it can't
  bind. Free the port (or change it) and re-run `scripts/arena.sh serve`.
- **Leaderboard tab says "can't reach API"** — start the JSON API:
  `source .env && uvicorn dashboard.app:app --port 8000`.
- **LangFuse drill-down empty** — a run created *before* the LangFuse integration won't
  have trace links/sessions; run a fresh grid with the current harness.

---

## Docs
- Design spec: [`docs/superpowers/specs/2026-06-06-chatbi-arena-poc-design.md`](docs/superpowers/specs/2026-06-06-chatbi-arena-poc-design.md)
- Implementation plan: [`docs/superpowers/plans/2026-06-06-chatbi-arena-measurement-core.md`](docs/superpowers/plans/2026-06-06-chatbi-arena-measurement-core.md)
- CDC runbook: [`infra/README_clickpipes.md`](infra/README_clickpipes.md)
- Web UI details: [`web/README.md`](web/README.md)
