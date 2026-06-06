"""All LangFuse SDK calls live here so a version bump touches one file.
Verified against langfuse 2.57.0: Langfuse.trace() -> StatefulTraceClient with
.generation(), .score(name=, value=), and .id. If the installed SDK differs,
adjust ONLY this module."""
from langfuse import Langfuse
from arena.config import LangfuseCfg


class LangfuseTracer:
    def __init__(self, cfg: LangfuseCfg):
        self._lf = Langfuse(public_key=cfg.public_key,
                            secret_key=cfg.secret_key, host=cfg.host)

    def trace_run(self, *, run_id, config_id, question_id, question,
                  sql, model_name, prompt_name, usage, latency_ms,
                  correctness, cost_usd, outcome):
        trace = self._lf.trace(
            name="agent_run",
            input={"question": question},
            output={"sql": sql},
            tags=[config_id, f"run:{run_id}"],
            metadata={"config_id": config_id, "question_id": question_id,
                      "model": model_name, "prompt": prompt_name,
                      "outcome": outcome, "run_id": run_id},
        )
        trace.generation(
            name="bedrock_call", model=model_name,
            usage={"input": usage.input_tokens, "output": usage.output_tokens},
            output=sql,
        )
        for name, value in [("correctness", correctness),
                            ("cost_usd", cost_usd),
                            ("latency_ms", latency_ms)]:
            trace.score(name=name, value=float(value))
        return trace.id

    def flush(self) -> None:
        self._lf.flush()
