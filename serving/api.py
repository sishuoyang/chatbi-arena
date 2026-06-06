"""Live Chat BI serving API (design §11, C11).

POST /ask {question, config_id} -> runs the agent live and returns
{sql, columns, rows, cost_usd, latency_ms, outcome}. Reuses the same agent core
and read-only sandbox as the benchmark harness, so the demo and the benchmark
share one code path. Instrumented with OTel -> ClickStack.

  source .env && AWS_PROFILE=sa uvicorn serving.api:app --port 8100
  curl -s localhost:8100/ask -H 'content-type: application/json' \
    -d '{"question":"How many customers are there?","config_id":"nova-lite__P1_zeroshot"}'
"""
import time
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from arena.config import load_config
from agents.chclient import ROClickHouseClient
from agents.bedrock import BedrockClient, cost_usd
from agents.loop import run_agent
from agents.sqlguard import validate_select_only  # noqa: F401  (exercised via loop)
from observability.instrumentation import init_telemetry

app = FastAPI(title="ChatBI Arena — Serving API")
_cfg = load_config()
_ro = ROClickHouseClient(_cfg.clickhouse)
_bedrock = BedrockClient(_cfg.bedrock.region)
_tracer, _meter = init_telemetry("arena-serving")
_req_latency = _meter.create_histogram("arena.ask_latency_ms", unit="ms")
_req_cost = _meter.create_counter("arena.ask_cost_usd", unit="usd")

with open("schema/schema_context.md") as f:
    _schema_ctx = f.read()


class AskRequest(BaseModel):
    question: str
    config_id: str  # "<model_name>__<prompt_name>", e.g. nova-lite__P1_zeroshot


class AskResponse(BaseModel):
    config_id: str
    sql: str | None
    columns: list[str] | None
    rows: list | None
    cost_usd: float
    latency_ms: int
    outcome: str
    error: str | None


def _split_config(config_id: str):
    try:
        model_name, prompt_name = config_id.split("__", 1)
        return _cfg.model_by_name(model_name), _cfg.prompt_by_name(prompt_name)
    except (ValueError, StopIteration):
        raise HTTPException(400, f"unknown config_id '{config_id}'")


@app.get("/configs")
def configs():
    models, prompts = _cfg.resolved_grid()
    return {"config_ids": [f"{m}__{p}" for m in models for p in prompts]}


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    mcfg, pcfg = _split_config(req.config_id)
    with _tracer.start_as_current_span("ask") as span:
        span.set_attribute("arena.config_id", req.config_id)
        span.set_attribute("arena.question", req.question)
        t0 = time.time()
        ar = run_agent(req.question, mcfg, pcfg, _schema_ctx, _ro, _bedrock,
                       dict(_cfg.bedrock.inference),
                       max_retries=_cfg.eval.default_max_retries)
        latency_ms = int((time.time() - t0) * 1000)
        c = cost_usd(ar.usage, mcfg.price_per_1m_in, mcfg.price_per_1m_out)
        span.set_attribute("arena.cost_usd", c)
        span.set_attribute("arena.outcome", ar.outcome_hint)
        _req_latency.record(latency_ms, {"config_id": req.config_id})
        _req_cost.add(c, {"config_id": req.config_id})
        # cap returned rows so a wide result doesn't bloat the response
        rows = [list(r) for r in (ar.rows or [])][:200]
        return AskResponse(
            config_id=req.config_id, sql=ar.sql, columns=ar.cols, rows=rows,
            cost_usd=round(c, 6), latency_ms=latency_ms,
            outcome="ok" if ar.error is None else ar.outcome_hint, error=ar.error)
