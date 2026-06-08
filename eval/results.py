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
_MIGRATIONS = [
    "ALTER TABLE {db}.eval_runs ADD COLUMN IF NOT EXISTS judge_score Float64 DEFAULT 0",
    "ALTER TABLE {db}.eval_runs ADD COLUMN IF NOT EXISTS trace_id String DEFAULT ''",
    "ALTER TABLE {db}.eval_runs ADD COLUMN IF NOT EXISTS session_id String DEFAULT ''",
    "ALTER TABLE {db}.eval_runs ADD COLUMN IF NOT EXISTS trace_url String DEFAULT ''",
    "ALTER TABLE {db}.eval_runs ADD COLUMN IF NOT EXISTS session_url String DEFAULT ''",
]

_LEADERBOARD = """
CREATE VIEW IF NOT EXISTS {db}.v_leaderboard AS
SELECT run_id, config_id, model_name, prompt_name,
       count() AS n_questions,
       round(avg(correctness), 4) AS accuracy,
       sum(correctness) AS n_correct,
       round(sum(cost_usd), 6) AS total_cost_usd,
       round(avg(cost_usd), 6) AS avg_cost_usd,
       round(avg(latency_ms), 1) AS avg_latency_ms,
       round(sum(cost_usd) / nullIf(sum(correctness), 0), 6) AS cost_per_correct_answer
FROM (SELECT * FROM {db}.eval_runs FINAL)
GROUP BY run_id, config_id, model_name, prompt_name
"""


def ensure_results_tables(admin, db: str) -> None:
    admin.command(_DDL.format(db=db))
    for m in _MIGRATIONS:
        admin.command(m.format(db=db))
    admin.command(_LEADERBOARD.format(db=db))


def write_eval_run(admin, db: str, row: EvalRunRow) -> None:
    d = asdict(row)
    cols = list(d.keys())
    admin.insert(f"{db}.eval_runs", [[d[c] for c in cols]], column_names=cols)
