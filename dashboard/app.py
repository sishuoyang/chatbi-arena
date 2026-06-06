"""Leaderboard dashboard. Reads ONLY from ClickHouse (single source of truth).
Usage: source .env && uvicorn dashboard.app:app --reload --port 8000
"""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from arena.config import load_config
from agents.chclient import make_admin_client

app = FastAPI(title="ChatBI Arena Leaderboard")
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
        f"n_correct, total_cost_usd, avg_latency_ms, cost_per_correct_answer "
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


@app.get("/")
def index():
    return FileResponse("dashboard/static/index.html")
