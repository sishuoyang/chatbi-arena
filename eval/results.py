from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class EvalRunRow:
    run_id: str
    config_id: str
    model_name: str
    prompt_name: str
    question_id: str
    tier: int
    correctness: int
    cost_usd: float
    latency_ms: int
    retries: int
    outcome: str
    sql: str
    tags: str
    data_snapshot_ts: datetime  # passed straight to the DateTime column
    # LangFuse linkage + LLM-judge score
    judge_score: float = 0.0
    trace_id: str = ""
    session_id: str = ""
    trace_url: str = ""
    session_url: str = ""


_DDL = """
CREATE TABLE IF NOT EXISTS {db}.eval_runs (
  run_id String, config_id String, model_name String, prompt_name String,
  question_id String, tier UInt8, correctness UInt8, cost_usd Float64,
  latency_ms UInt32, retries UInt8, outcome String, sql String, tags String,
  data_snapshot_ts DateTime,
  judge_score Float64 DEFAULT 0, trace_id String DEFAULT '', session_id String DEFAULT '',
  trace_url String DEFAULT '', session_url String DEFAULT '',
  inserted_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY (run_id, config_id, question_id)
"""

# Idempotently add the newer columns to a pre-existing eval_runs table.
# Columns added after the original schema; applied only when actually missing.
_NEW_COLUMNS = {
    "judge_score": "Float64 DEFAULT 0",
    "trace_id": "String DEFAULT ''",
    "session_id": "String DEFAULT ''",
    "trace_url": "String DEFAULT ''",
    "session_url": "String DEFAULT ''",
}

_LEADERBOARD = """
CREATE OR REPLACE VIEW {db}.v_leaderboard AS
SELECT run_id, config_id, model_name, prompt_name,
       count() AS n_questions,
       round(avg(correctness), 4) AS accuracy,
       sum(correctness) AS n_correct,
       round(avg(judge_score), 3) AS avg_judge_score,
       round(sum(cost_usd), 6) AS total_cost_usd,
       round(avg(cost_usd), 6) AS avg_cost_usd,
       round(avg(latency_ms), 1) AS avg_latency_ms,
       round(sum(cost_usd) / nullIf(sum(correctness), 0), 6) AS cost_per_correct_answer
FROM (SELECT * FROM {db}.eval_runs FINAL)
GROUP BY run_id, config_id, model_name, prompt_name
"""


def _command_retry(admin, sql: str, tries: int = 6) -> None:
    """Run DDL, retrying ClickHouse Cloud's transient replica-metadata race
    (code 517 CANNOT_ASSIGN_ALTER — 'please retry')."""
    import time
    for i in range(tries):
        try:
            admin.command(sql)
            return
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if ("517" in msg or "CANNOT_ASSIGN_ALTER" in msg) and i < tries - 1:
                time.sleep(1.0 * (i + 1))
                continue
            raise


def ensure_results_tables(admin, db: str) -> None:
    _command_retry(admin, _DDL.format(db=db))
    # Only ALTER columns that are actually missing — so a migrated table issues
    # no ALTER at all (avoids re-triggering the replicated-DDL metadata race).
    existing = {r[0] for r in admin.query(
        f"SELECT name FROM system.columns WHERE database = '{db}' AND table = 'eval_runs'"
    ).result_rows}
    for col, typ in _NEW_COLUMNS.items():
        if col not in existing:
            _command_retry(admin, f"ALTER TABLE {db}.eval_runs ADD COLUMN IF NOT EXISTS {col} {typ}")
    _command_retry(admin, _LEADERBOARD.format(db=db))


def write_eval_run(admin, db: str, row: EvalRunRow) -> None:
    d = asdict(row)
    cols = list(d.keys())
    admin.insert(f"{db}.eval_runs", [[d[c] for c in cols]], column_names=cols)
