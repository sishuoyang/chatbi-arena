"""Leaderboard JSON API. Reads ONLY from ClickHouse (single source of truth).
Consumed by the React web UI (../web, Leaderboard tab).
Usage: source .env && uvicorn dashboard.app:app --reload --port 8000
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from arena.config import load_config
from agents.chclient import make_admin_client

app = FastAPI(title="ChatBI Arena Leaderboard API")

# Lazy LangFuse client (only built when an /api/lf/* endpoint is hit).
_lf = None
_lf_base = None


def _langfuse():
    global _lf, _lf_base
    if _lf is None:
        from langfuse import Langfuse
        from eval.langfuse_adapter import _project_base
        lc = _cfg.langfuse
        _lf = Langfuse(public_key=lc.public_key, secret_key=lc.secret_key, host=lc.host)
        _lf_base = _project_base(lc)
    return _lf
# Allow the React SPA (Vite dev server / static build) to call this JSON API.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"],
)
_cfg = load_config()
_db = _cfg.clickhouse.database


def _query(sql: str):
    # A fresh client per request: clickhouse-connect rejects concurrent queries
    # on one shared session, and the dashboard fans out requests in parallel.
    client = make_admin_client(_cfg.clickhouse)
    try:
        return client.query(sql)
    finally:
        client.close()


def _rows_to_dicts(res):
    return [dict(zip(res.column_names, r)) for r in res.result_rows]


@app.get("/api/runs")
def runs():
    res = _query(
        f"SELECT DISTINCT run_id FROM {_db}.eval_runs ORDER BY run_id DESC")
    return [r[0] for r in res.result_rows]


@app.get("/api/leaderboard")
def leaderboard(run_id: str | None = None):
    where = f"WHERE run_id = '{run_id}'" if run_id else ""
    res = _query(
        f"SELECT config_id, model_name, prompt_name, n_questions, accuracy, "
        f"n_correct, avg_judge_score, total_cost_usd, avg_latency_ms, cost_per_correct_answer "
        f"FROM {_db}.v_leaderboard {where} ORDER BY accuracy DESC, cost_per_correct_answer ASC")
    return _rows_to_dicts(res)


@app.get("/api/tiers")
def tiers(run_id: str | None = None):
    where = f"WHERE run_id = '{run_id}'" if run_id else ""
    res = _query(
        f"SELECT config_id, tier, round(avg(correctness),3) AS accuracy "
        f"FROM (SELECT * FROM {_db}.eval_runs FINAL) {where} "
        f"GROUP BY config_id, tier ORDER BY config_id, tier")
    return _rows_to_dicts(res)


@app.get("/api/outcomes")
def outcomes(run_id: str | None = None):
    where = f"WHERE run_id = '{run_id}'" if run_id else ""
    res = _query(
        f"SELECT config_id, outcome, count() AS n "
        f"FROM (SELECT * FROM {_db}.eval_runs FINAL) {where} "
        f"GROUP BY config_id, outcome ORDER BY config_id")
    return _rows_to_dicts(res)


@app.get("/api/questions")
def questions(run_id: str, config_id: str):
    """Per-question drill-down for one config (ClickHouse), with LangFuse deep links."""
    res = _query(
        f"SELECT question_id, tier, correctness, judge_score, cost_usd, latency_ms, "
        f"outcome, sql, trace_url, session_url "
        f"FROM (SELECT * FROM {_db}.eval_runs FINAL) "
        f"WHERE run_id = '{run_id}' AND config_id = '{config_id}' "
        f"ORDER BY question_id")
    return _rows_to_dicts(res)


@app.get("/api/lf/session")
def lf_session(session_id: str):
    """Conversation history for a session, read live from the LangFuse API:
    each trace's question, generated SQL, outcome, and the full message turns."""
    lf = _langfuse()
    traces = lf.fetch_traces(session_id=session_id, limit=50).data
    out = []
    for t in sorted(traces, key=lambda x: (x.metadata or {}).get("question_id", "")):
        turns = []
        obs = lf.fetch_observations(trace_id=t.id, type="GENERATION").data
        if obs and isinstance(obs[0].input, list):
            turns = [{"role": m.get("role"), "content": m.get("content")}
                     for m in obs[0].input]
        out.append({
            "question_id": (t.metadata or {}).get("question_id"),
            "question": (t.input or {}).get("question"),
            "sql": (t.output or {}).get("sql"),
            "outcome": (t.metadata or {}).get("outcome"),
            "trace_url": f"{_lf_base}/traces/{t.id}",
            "turns": turns,
        })
    return {"session_id": session_id, "source": "langfuse-api", "exchanges": out}


@app.get("/api/meta")
def meta():
    """Surface the LangFuse project base + dataset for experiment deep links."""
    _langfuse()
    from eval.langfuse_adapter import DATASET_NAME
    return {"langfuse_base": _lf_base, "dataset": DATASET_NAME,
            "datasets_url": f"{_lf_base}/datasets" if _lf_base else None}
