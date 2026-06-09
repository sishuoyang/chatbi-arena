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

A single web app (`web/`, http://localhost:5174) with three tabs:

- **Countdown** — a live-event "stage" screen for demos: a presenter countdown timer,
  the model families as contenders, and the current best accuracy per family (read live
  from the latest run).
- **Leaderboard** — every model×prompt config ranked by accuracy, **cost-per-correct-answer**
  (the headline), latency, per-tier accuracy, and an outcome breakdown, with a
  **cost × accuracy** chart and a **best-value** ranking up top. Click any config to
  **drill into its per-question results**, each linking to its **LangFuse trace**
  (prompt → generated SQL → error → tokens → span timings). A **"View conversation"**
  button replays the agent's session **live from the LangFuse API**, an **LLM-judge**
  column scores SQL quality, and links jump to LangFuse's native **Experiment** and
  **Session** views. A **Guided walkthrough** narrates the open-vs-proprietary story by
  driving the UI itself.
- **Architecture** — an animated React Flow diagram of the whole system.

Plus a live **`/ask` API**: pose a question, pick a config, watch the SQL, cost, and
latency change.

### Screenshots

**Leaderboard** — the analyst surface: cost-per-correct ranking, a cost × accuracy chart, and per-question drill-downs into LangFuse traces.

![ChatBI Arena leaderboard](docs/images/leaderboard.png)

**Countdown** — the live-event stage shown before a demo.

![ChatBI Arena countdown stage](docs/images/arena.png)

---

## Architecture

![ChatBI Arena architecture diagram](docs/images/architecture.png)

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

## Getting started

### Prerequisites
- **Python 3.11**, **Node 18+** (for the web UI), and **Docker** (for the ClickStack collector; optional).
- A **ClickHouse Cloud** service, a **LangFuse Cloud** project, and **AWS Bedrock**
  access in **`us-east-1`** with the models enabled (Bedrock → Model access). The
  model grid spans **Claude, Qwen, DeepSeek, OpenAI gpt-oss, and Moonshot Kimi** —
  these non-Amazon models live in us-east-1 (not ap-southeast-1), so the agent calls
  Bedrock there while ClickHouse stays in Singapore. (Note: hosted ChatGPT/GPT-4o is
  not on Bedrock — `gpt-oss` is OpenAI's open-weight model.)
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
aws sso login --profile <your-profile>       # a profile with Bedrock access in us-east-1
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

**From the web UI (easiest):** Leaderboard tab → **▶ Run benchmark** → either click a
**preset** (e.g. *Open-weight showdown*, *Open vs proprietary*, *Budget tier*,
*Frontier* — defined in `config.yaml` `profiles`, hover for what each contains) or
**browse the live Bedrock catalog** (every text model in the region, filterable by
family) and tick models to put in the arena (the `config.yaml` set is pre-selected),
pick **prompts**, **name the run**, **Run**. Models you add that aren't in
`config.yaml` have no price, so their cost shows as $0 (accuracy/latency/judge still work). The dashboard API
spawns the harness server-side, streams progress, and auto-selects the new run when
done. The **Run dropdown** lists past runs (each is one named execution) — pick one to
view its results. (The API process must have AWS Bedrock creds — start it with
`AWS_PROFILE=<profile>`, which `arena.sh` does for you.)

**From the CLI:**
```bash
AWS_PROFILE=<profile> python -m eval.harness --run-id <name> [--models a,b] [--prompts x,y] [--no-judge] [--limit N]
```
- Each invocation is a **run** (`run_id`); it writes one row per *(config × question)* to
  `arena_house.eval_runs` and a trace per run to LangFuse. The web UI's run selector picks which run to view.
- The grid (which models × which prompts) defaults to `config.yaml`; `--models`/`--prompts` override it.
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

Rough estimates (~mid-2026 on-demand rates; verify against current pricing) — Aurora and
ClickHouse Cloud run in **ap-southeast-1**, Bedrock in **us-east-1**. The dataset is tiny
(~100k rows), so storage/I/O are negligible — the costs that matter are Aurora compute and
Bedrock tokens.

| Component | Cost | Notes |
|---|---|---|
| **Bedrock** (Converse) | **~$0.40 per grid run** · <$0.01 per cheap-model run · ~$0.0001–0.005 per live `/ask` | e.g. 6 models × 3 prompts × 18 Qs = 324 calls. The Claude models dominate the cost; the open models (Qwen, gpt-oss, DeepSeek) are pennies. |
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
  (**us-east-1**) and use a profile/role that allows it. Some federated/SSO sessions
  are scoped down; pick one with Bedrock rights. Many models must be invoked via an
  **inference-profile id** (e.g. `us.anthropic.…`) rather than the bare model id — the
  live catalog resolves this for you.
- **Port 8000 in use** — the dashboard API uses `:8000`; `arena.sh up` warns if it can't
  bind. Free the port (or change it) and re-run `scripts/arena.sh serve`.
- **Leaderboard tab says "can't reach API"** — start the JSON API:
  `source .env && uvicorn dashboard.app:app --port 8000`.
- **LangFuse drill-down empty** — a run created *before* the LangFuse integration won't
  have trace links/sessions; run a fresh grid with the current harness.

---

## Docs
- CDC runbook: [`infra/README_clickpipes.md`](infra/README_clickpipes.md)
- Web UI details: [`web/README.md`](web/README.md)
