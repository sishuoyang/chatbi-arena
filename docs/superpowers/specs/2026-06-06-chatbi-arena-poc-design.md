# ChatBI Arena — POC Implementation Spec

**Date:** 2026-06-06
**Owner:** Si Shuo (ClickHouse PSE, APJ)
**Audience:** AWS team (demo), plus implementers
**Status:** Spec — approved scoping, ready for implementation plan
**Parent design:** `CLAUDE.md` (full component specs §1–§17). This spec records the **POC scoping decisions and deltas** on top of it. Where this spec and `CLAUDE.md` disagree, **this spec wins** for the POC.

---

## 1. Purpose

Build a working POC of ChatBI Arena: a **benchmark harness for natural-language-to-SQL agents** over a live ClickHouse Cloud (on AWS) dataset, fed from AWS Aurora via ClickPipes CDC. It runs a grid of `{Bedrock model} × {prompt strategy}` against a golden question set, grades by **execution accuracy**, and tracks **cost / latency / error rate** per config. The headline artifact is a **leaderboard keyed on cost-per-correct-answer**.

Target audience is the AWS team, so the data plane is **AWS-native and provisioned via Terraform**.

---

## 2. Scoping decisions (the deltas confirmed in brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| **Build sequence** | **Approach B — measurement core first**, then layer the live CDC pipeline | Front-loads the riskiest *logic* (grading, §16 of CLAUDE.md) and reaches the headline leaderboard fast; if ClickPipes throws surprises, the star demo still stands |
| **ClickHouse Cloud** | **Already exists** on AWS ap-southeast-1 (Singapore). Not in Terraform scope | User pre-provisioned it; sets region for all other infra |
| **Region** | **ap-southeast-1** for Aurora + Bedrock, co-located with ClickHouse | Clean AWS-native story, low latency, APJ data residency |
| **Compute for live components** | **Local / Docker** (data-gen, harness, serving API, dashboard, OTel collector). Only Aurora + Bedrock + ClickHouse are cloud infra | User choice; faster iteration, lower cost; still demos the full data path |
| **Dashboard source of truth** | **A ClickHouse results table** (`arena.eval_runs` + aggregate view). The dashboard reads ClickHouse only | User choice; decouples UI from LangFuse internals |
| **LangFuse** | **LangFuse Cloud** (US region) for rich per-trace tracing/scores, in parallel | Per requirement; not the dashboard's source of truth |
| **ClickStack** | **Managed ClickStack** via the `clickhouse/clickstack-otel-collector` container (run locally), writing OTel into the same ClickHouse Cloud service | Reinforces "one engine" narrative |
| **Grading** | Built **test-first (TDD)** with a should-match / should-not-match suite before scaling the grid | §16 names grading make-or-break |

---

## 3. Architecture

```
                         AWS ap-southeast-1
  ┌──────────────────────────────────────────────────────────┐
  │  Aurora PostgreSQL (Serverless v2)   ◀── datagen (local)   │
  │      │  logical replication                                │
  │      └──────────── ClickPipes CDC ──────────┐             │
  │  Bedrock Converse API  ◀── agent core (local)│             │
  └──────────────────────────────────────────────┼───────────┘
                                                   ▼
                         ClickHouse Cloud (AWS ap-southeast-1, db: arena_house)
                         ┌────────────────────────────────────────────┐
                         │  CDC-landed RMT tables  ─┐                  │
                         │  seed tables (phase 1)  ─┴► v_* views        │
                         │  arena.eval_runs (results, dashboard SoT)    │
                         │  otel_* / ClickStack tables (telemetry)      │
                         └────────────────────────────────────────────┘
                            ▲                  ▲                 ▲
        local: harness ─────┘                  │                 │
        local: serving /ask ──────────────────-┘                 │
        local: OTel collector container ────────────────────────-┘
                            │
                            ▼
        LangFuse Cloud (US)  ◀── harness + serving (traces, scores)
        Dashboard (local) ──► reads arena.eval_runs from ClickHouse
```

**The stable contract is the `v_*` views.** Phase 1 (M1–M4): views sit over plain seed tables loaded directly into ClickHouse. Phase 2 (M5): views are repointed at the ClickPipes-landed `ReplacingMergeTree` tables, verifying the real `_peerdb_*` soft-delete/version column names at that point. Agents and golden SQL only ever reference the views, so the source swap is invisible to them.

---

## 4. Components (POC scope)

Component specs are in `CLAUDE.md` §5–§11. POC-specific notes:

| # | Component | POC scope |
|---|---|---|
| C1 | Data generator | Python + Faker. Phase 1: writes seed + continuous data **directly to ClickHouse** seed tables. Phase 2: writes to **Aurora**; CDC carries it to ClickHouse. `--seed` for reproducibility. ~90d backfill + continuous loop with diurnal/long-tail patterns and status-transition UPDATEs. |
| C2 | Aurora Postgres | Terraform: Serverless v2, `rds.logical_replication=1`, replication role, publication, security group. M5. |
| C3 | ClickPipes CDC | Created via ClickHouse Terraform provider `clickhouse_clickpipe` if available in installed provider version, else documented API/console runbook. Replicates the 5 tables. M5. |
| C4 | ClickHouse Cloud | **Exists.** db `arena_house`. Holds seed/CDC tables, `v_*` views, `arena.eval_runs`, and ClickStack telemetry tables. |
| C5 | Agent core | Python + boto3 Bedrock Converse + clickhouse-connect. Parameterized loop (model × prompt). Read-only sandbox + SQL guard (§7.2). |
| C6 | Benchmark harness | Python. Grid runner; caches golden results per run; writes `eval_runs`; emits LangFuse traces/scores. |
| C7 | Golden set | YAML, ~20 questions across 5 tiers to start; expand toward 40–60. A few held out for P2 few-shot. |
| C8 | LangFuse | LangFuse Cloud (US). Traces (spans: build_prompt, bedrock_call, sql_exec, grade), Datasets, Experiments/dataset-runs, scores. SDK calls isolated behind `langfuse_adapter.py`. |
| C9 | ClickStack/OTel | OTel SDK in datagen + serving → OTLP → local `clickstack-otel-collector` container → ClickHouse. CDC replication-lag panel. |
| C10 | Dashboard | Reads `arena.eval_runs` + aggregate view from ClickHouse. Leaderboard: config × {accuracy overall + per tier, cost_per_correct_answer, avg_latency, error breakdown}, sortable; winner-per-tier callout. |
| C11 | Serving API | FastAPI `POST /ask {question, config_id}` → live agent → `{sql, columns, rows, cost_usd, latency_ms}`. Reuses C5 sandbox. |

---

## 5. Data model & grading

- **Schema:** per `CLAUDE.md` §5 (customers, products, orders, order_items, events).
- **Views:** per §6.2 — `SELECT * EXCEPT(<soft-delete>,<version>) FROM <t> FINAL WHERE <soft-delete>=0`. Soft-delete/version column names are **placeholders until verified against real ClickPipes output** (M5). In phase 1 the seed tables are `ReplacingMergeTree` with the same conventions so the views are identical in both phases.
- **schema_context.md:** generated from `system.columns` over the views; injected into prompts. Regeneratable so it never drifts.
- **Grading:** per §8.2 — compare result sets by column position, round floats to `float_dp=4`, NULL→`∅`, sort rows unless `ordered:true`, multiset (keep dups). Score 0/1. Outcome taxonomy per §8.3. **Built test-first** with a should-match / should-not-match pair suite.

---

## 6. Results table (dashboard source of truth)

`arena.eval_runs` — `MergeTree ORDER BY (run_id, config_id, question_id)`. One row per `(run_id, question_id, config_id)` with: `correctness` (0/1), `cost_usd`, `latency_ms`, `retries`, `outcome`, generated `sql`, `tier`, `tags`, plus reproducibility fields (model_id, prompt_version, config_hash, golden_set_version, data_snapshot_ts). Re-running a config for the same `run_id` **upserts** (ReplacingMergeTree or dedup-on-read) rather than duplicates.

A `v_leaderboard` view (or query) derives per-config: `accuracy` (overall + per tier), `avg_cost_usd`, `avg_latency_ms`, **`cost_per_correct_answer = Σcost / Σcorrect`**, `error_rate` by outcome.

---

## 7. Configuration & secrets

- `config.yaml` per `CLAUDE.md` §13 — models, prompts (P1–P5), pricing, eval settings, grid selection. Pricing set from **real ap-southeast-1 Bedrock prices** before trusting cost numbers.
- Secrets via `.env` (already populated): `CLICKHOUSE_CLOUD_*`, `LANGFUSE_*`. ClickStack via the collector container env. **AWS credentials** via standard AWS chain (env/profile/SSO) — required to run M1+ (Bedrock) and M5 (Aurora/ClickPipes). Never commit secrets.
- **Bedrock model access** must be enabled in the ap-southeast-1 account console (Terraform can't toggle it). Document + verify with a connectivity check. Start with **one cheap model** (M1–M4); expand the grid at M4. Use APAC inference profiles where required; verify exact model IDs at implementation.

---

## 8. Build plan (milestones + acceptance)

| M | Milestone | Done when |
|---|---|---|
| **M0** | Terraform skeleton + connectivity | TF providers/region/state scaffolded; a connectivity script confirms ClickHouse-on-AWS, Bedrock (region+model access), LangFuse, and ClickStack collector all reachable |
| **M1** | Measurement core | Seed data in ClickHouse; `v_*` views + `schema_context.md`; one cheap-model agent answers a question end-to-end returning SQL+rows+usage; read-only user blocks a destructive generated statement |
| **M2** | Grading (TDD) | `normalize`/`grade` pass the should-match/should-not-match suite; ~20 golden questions authored; equivalent-correct SQL scores 1, wrong scores 0, order-sensitivity respected |
| **M3** | Results + LangFuse + dashboard | `arena.eval_runs` populated; `v_leaderboard` derives cost_per_correct_answer; dashboard renders from ClickHouse; LangFuse shows full NL→SQL→result→grade traces with scores |
| **M4** | Full grid | All configured model × prompt combos scored in `eval_runs` + LangFuse; real ap-southeast-1 pricing in cost numbers |
| **M5** | Live pipeline | Terraform Aurora (logical replication, repl role, publication); ClickPipes pipe replicating the 5 tables; datagen writes Aurora; `v_*` repointed at verified CDC tables; views return deduplicated current-state rows matching Aurora after lag |
| **M6** | ClickStack/OTel | Datagen + serving telemetry visible in ClickStack; CDC replication-lag signal charted |
| **M7** | Serving `/ask` API | Live endpoint answers a question for a chosen config; switching config changes SQL/cost/latency; same read-only sandbox |

Stretch (out of v1): `/route` router bridge, Aurora cross-engine comparison (`CLAUDE.md` §11).

---

## 9. Repository layout

Per `CLAUDE.md` §12, plus:
```
chatbi-arena/
├── infra/terraform/        # Aurora, ClickPipes, IAM, security groups, Bedrock checks
├── infra/README_clickpipes.md
├── docker-compose.yml      # OTel collector (clickstack-otel-collector) + optional datagen
├── config.yaml
├── datagen/  schema/  agents/  eval/  golden/  serving/  dashboard/  observability/
└── docs/superpowers/specs/ # this spec + plan
```

---

## 10. Non-functional requirements

Per `CLAUDE.md` §15: cost control (cheap model + small data during iteration, temperature 0, cache golden per run), Bedrock throttling backoff + bounded concurrency, reproducibility (`run_id` + recorded model/prompt/config-hash/golden-version/snapshot-ts), grading determinism (snapshot expected results at run start), security (read-only sandbox + SELECT-only validator + query limits + least-privilege Aurora repl role + secrets via env), idempotent re-runs.

---

## 11. Key risks (and mitigations)

1. **Grading correctness** — mitigate with the TDD pair suite before scaling the grid (M2 gates M4).
2. **CDC internal column names** for `v_*` — verify against real ClickPipes output at M5; phase-1 views mirror the convention.
3. **ReplacingMergeTree duplicates** if a query bypasses `FINAL` views — enforce agents/goldens use `v_*` only via the read-only grants.
4. **LangFuse version sensitivity** — isolate behind `langfuse_adapter.py`; pin the version.
5. **ClickPipes Terraform-provider maturity** — fall back to documented API/console runbook if the `clickhouse_clickpipe` resource is unavailable in the pinned provider version.
6. **Bedrock model access / region availability** — verify enabled model IDs in ap-southeast-1 at M0; start with one confirmed cheap model.
7. **AWS credentials** — required for M1+; flag early if not yet provided.

---

## 12. Open items to confirm during implementation (flag, don't block)

- AWS credentials/profile for ap-southeast-1.
- Exact Bedrock model IDs enabled in the account (cheap + strong), and their current per-1M-token prices.
- Whether the installed ClickHouse Terraform provider version supports `clickhouse_clickpipe`; else use the API runbook.
- Confirm the real ClickPipes `_peerdb_*` soft-delete/version column names at M5.
```
