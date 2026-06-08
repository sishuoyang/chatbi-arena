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
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"],
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
    """List a session's exchanges live from the LangFuse API (one call, fast).
    Conversation turns are loaded per-exchange via /api/lf/trace on demand."""
    lf = _langfuse()
    traces = lf.fetch_traces(session_id=session_id, limit=50).data
    out = []
    for t in sorted(traces, key=lambda x: (x.metadata or {}).get("question_id", "")):
        out.append({
            "question_id": (t.metadata or {}).get("question_id"),
            "question": (t.input or {}).get("question"),
            "sql": (t.output or {}).get("sql"),
            "outcome": (t.metadata or {}).get("outcome"),
            "trace_id": t.id,
            "trace_url": f"{_lf_base}/traces/{t.id}",
        })
    return {"session_id": session_id, "source": "langfuse-api", "exchanges": out}


@app.get("/api/lf/trace")
def lf_trace(trace_id: str):
    """The conversation turns of one trace, from the LangFuse API."""
    lf = _langfuse()
    obs = lf.fetch_observations(trace_id=trace_id, type="GENERATION").data
    turns = []
    if obs and isinstance(obs[0].input, list):
        turns = [{"role": m.get("role"), "content": m.get("content")} for m in obs[0].input]
    return {"trace_id": trace_id, "turns": turns}


@app.get("/api/meta")
def meta():
    """Surface the LangFuse project base + dataset for experiment deep links."""
    _langfuse()
    from eval.langfuse_adapter import DATASET_NAME
    return {"langfuse_base": _lf_base, "dataset": DATASET_NAME,
            "datasets_url": f"{_lf_base}/datasets" if _lf_base else None}


# --- Run the benchmark from the UI (spawns the harness as a subprocess) -------
# Local/demo use only: this lets the web app trigger `python -m eval.harness`.
# The API process must have AWS Bedrock creds in its environment.
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_run_state = {"running": False, "run_id": None, "started_at": None,
              "lines": [], "returncode": None}
_run_lock = threading.Lock()


@app.get("/api/grid-options")
def grid_options():
    """Prompts available to run (from config.yaml), with descriptions for hovers.
    Models now come from the live catalog endpoint /api/bedrock-models."""
    return {
        "prompts": [{"name": p.name, "desc": p.desc or p.name} for p in _cfg.prompts],
    }


# --- Live Bedrock model catalog -------------------------------------------------
import re as _re

_catalog_cache = None
_SKIP = _re.compile(r"embed|rerank|guard|image|video|canvas|reel|stable|stability|"
                    r"titan-(image|text)|nova-(canvas|reel)|voxtral|whisper", _re.I)


def _size_hint(model_id: str) -> str:
    m = _re.search(r"(\d+(?:\.\d+)?)\s*b\b", model_id, _re.I)
    if m:
        return f"{m.group(1)}B"
    for k, v in [("haiku", "small"), ("micro", "small"), ("mini", "small"), ("lite", "small"),
                 ("sonnet", "medium"), ("pro", "large"), ("opus", "large"), ("large", "large"),
                 ("small", "small"), ("medium", "medium")]:
        if k in model_id.lower():
            return v
    return ""


def _invocable_id(model_id: str, infer_types: list[str], profiles: list[str]) -> str | None:
    if "ON_DEMAND" in infer_types:
        return model_id
    # else needs an inference profile: prefer us. then global. then apac.
    for pref in ("us.", "global.", "apac."):
        cand = pref + model_id
        if cand in profiles:
            return cand
    # some profiles end with the model id but carry a version suffix; match by suffix
    for p in profiles:
        if p.split(".", 1)[-1] == model_id:
            return p
    return None


@app.get("/api/bedrock-models")
def bedrock_models():
    """All text models available in the configured Bedrock region, grouped by
    provider/family, with the invocable id, a size hint, and whether they're in
    the priced config (the defaults)."""
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache
    import boto3
    bc = boto3.client("bedrock", region_name=_cfg.bedrock.region)
    profiles = [p["inferenceProfileId"]
                for p in bc.list_inference_profiles().get("inferenceProfileSummaries", [])]
    priced = {m.id: m for m in _cfg.models}
    default_ids = set(priced)

    fams = {}
    for m in bc.list_foundation_models(byOutputModality="TEXT").get("modelSummaries", []):
        mid = m["modelId"]
        if _SKIP.search(mid):
            continue
        if "TEXT" not in m.get("inputModalities", []) or "TEXT" not in m.get("outputModalities", []):
            continue
        inv = _invocable_id(mid, m.get("inferenceTypesSupported", []) or [], profiles)
        if not inv:
            continue
        fam = m.get("providerName", "Other")
        cfg_m = priced.get(inv)
        # friendly, collision-free name: drop provider prefix + version, dots→hyphens
        nm = mid.split(":")[0]
        parts = nm.split(".")
        nm = ".".join(parts[1:]) if len(parts) > 1 else nm
        fams.setdefault(fam, []).append({
            "id": inv,
            "name": cfg_m.name if cfg_m else nm.replace(".", "-"),
            "family": fam,
            "size": _size_hint(mid),
            "in_default": inv in default_ids,
            "price_in": cfg_m.price_per_1m_in if cfg_m else 0.0,
            "price_out": cfg_m.price_per_1m_out if cfg_m else 0.0,
        })
    # de-dup by invocable id within a family; sort families with our defaults first
    default_fams = [m.family for m in _cfg.models]
    order = {f: i for i, f in enumerate(dict.fromkeys(default_fams))}
    out = []
    for fam in sorted(fams, key=lambda f: (order.get(_family_key(f, _cfg), 99), f)):
        seen, models = set(), []
        for mm in sorted(fams[fam], key=lambda x: x["id"]):
            if mm["id"] in seen:
                continue
            seen.add(mm["id"]); models.append(mm)
        out.append({"family": fam, "models": models})
    _catalog_cache = {"region": _cfg.bedrock.region, "families": out,
                      "default_ids": sorted(default_ids)}
    return _catalog_cache


def _family_key(provider_name: str, cfg) -> str:
    # map provider display name -> config family tag so defaults sort first
    pl = provider_name.lower()
    for fam in {m.family for m in cfg.models}:
        if fam in pl:
            return fam
    return provider_name


def _stream_harness(cmd: list[str]):
    proc = subprocess.Popen(cmd, cwd=str(_ROOT), env=os.environ.copy(),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    for line in proc.stdout:
        line = line.rstrip()
        with _run_lock:
            _run_state["lines"].append(line)
            _run_state["lines"] = _run_state["lines"][-400:]  # keep tail
    proc.wait()
    with _run_lock:
        _run_state["running"] = False
        _run_state["returncode"] = proc.returncode


@app.post("/api/run")
def start_run(body: dict):
    with _run_lock:
        if _run_state["running"]:
            return {"ok": False, "error": "a run is already in progress",
                    "run_id": _run_state["run_id"]}
        run_id = body.get("run_id") or f"ui-{int(time.time())}"
        cmd = [sys.executable, "-m", "eval.harness", "--run-id", run_id]
        models = body.get("models") or []
        if models and isinstance(models[0], dict):
            # full specs from the live-catalog browser → temp models-file
            import json
            specs = [{"id": m["id"], "name": m["name"], "family": m.get("family", "other"),
                      "price_per_1m_in": float(m.get("price_in") or 0),
                      "price_per_1m_out": float(m.get("price_out") or 0)} for m in models]
            (_ROOT / ".run").mkdir(exist_ok=True)
            mf = _ROOT / ".run" / f"models-{run_id}.json"
            mf.write_text(json.dumps(specs))
            cmd += ["--models-file", str(mf)]
        elif models:
            cmd += ["--models", ",".join(models)]
        if body.get("prompts"):
            cmd += ["--prompts", ",".join(body["prompts"])]
        if body.get("limit"):
            cmd += ["--limit", str(int(body["limit"]))]
        if body.get("judge") is False:
            cmd += ["--no-judge"]
        _run_state.update(running=True, run_id=run_id, started_at=time.time(),
                          lines=[f"$ {' '.join(cmd[2:])}"], returncode=None)
    threading.Thread(target=_stream_harness, args=(cmd,), daemon=True).start()
    return {"ok": True, "run_id": run_id}


@app.get("/api/run/status")
def run_status():
    with _run_lock:
        return dict(_run_state)
