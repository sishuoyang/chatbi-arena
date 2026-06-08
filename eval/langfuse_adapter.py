"""All LangFuse SDK calls live here so a version bump touches one file.
Verified against langfuse 2.57.0.

Uses LangFuse's higher-level primitives so the data is actually *used*, not just
written:
  - Datasets/Experiments: the golden set is a Dataset; each model×prompt config is
    a Dataset Run, so LangFuse's native experiment-comparison UI lights up.
  - Sessions: traces are grouped by session_id = run_id__config_id, so a session is
    one config's whole pass over the golden set (a replayable transcript).
  - Conversation: each trace logs the full message transcript as a generation.
"""
import base64
import json
import urllib.request
from urllib.parse import quote
from langfuse import Langfuse
from arena.config import LangfuseCfg

DATASET_NAME = "arena-golden"


def _project_base(cfg: LangfuseCfg) -> str:
    """Return '{host}/project/{projectId}' for building trace/session deep links."""
    try:
        auth = base64.b64encode(f"{cfg.public_key}:{cfg.secret_key}".encode()).decode()
        req = urllib.request.Request(f"{cfg.host}/api/public/projects",
                                     headers={"Authorization": f"Basic {auth}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            pid = json.loads(r.read())["data"][0]["id"]
        return f"{cfg.host}/project/{pid}"
    except Exception:  # noqa: BLE001
        return cfg.host  # fall back to host root


class LangfuseTracer:
    def __init__(self, cfg: LangfuseCfg):
        self._lf = Langfuse(public_key=cfg.public_key,
                            secret_key=cfg.secret_key, host=cfg.host)
        self._base = _project_base(cfg)
        self._items = {}  # dataset item id -> DatasetItemClient (for run linking)

    # --- Datasets / Experiments ------------------------------------------------
    def ensure_dataset(self, items: list[dict]) -> None:
        """items: [{id, question, golden_sql, ordered, tier}]. Idempotent."""
        try:
            self._lf.create_dataset(name=DATASET_NAME,
                                    description="ChatBI Arena golden questions")
        except Exception:  # noqa: BLE001 - already exists
            pass
        for it in items:
            self._lf.create_dataset_item(
                dataset_name=DATASET_NAME, id=it["id"],
                input={"question": it["question"]},
                expected_output={"golden_sql": it["golden_sql"], "ordered": it["ordered"]},
                metadata={"tier": it["tier"]})
        # cache item clients for fast run-linking
        self._items = {i.id: i for i in self._lf.get_dataset(DATASET_NAME).items}

    # --- Per-run trace ---------------------------------------------------------
    def trace_run(self, *, trace_id, session_id, run_id, config_id, question_id,
                  question, transcript, sql, model_name, prompt_name, usage,
                  latency_ms, correctness, cost_usd, outcome,
                  judge_score=None, link_experiment=True):
        trace = self._lf.trace(
            id=trace_id, name="agent_run", session_id=session_id,
            input={"question": question}, output={"sql": sql},
            tags=[config_id, f"run:{run_id}", model_name, prompt_name],
            metadata={"config_id": config_id, "question_id": question_id,
                      "model": model_name, "prompt": prompt_name,
                      "outcome": outcome, "run_id": run_id})
        # the full conversation (system/user/assistant turns) as a chat generation
        trace.generation(
            name="bedrock_call", model=model_name, input=transcript, output=sql,
            usage={"input": usage.input_tokens, "output": usage.output_tokens})

        scores = [("correctness", correctness), ("cost_usd", cost_usd),
                  ("latency_ms", latency_ms)]
        if judge_score is not None:
            scores.append(("llm_judge", judge_score))
        for name, value in scores:
            trace.score(name=name, value=float(value))

        # attach this trace to the config's Dataset Run (the experiment)
        if link_experiment and question_id in self._items:
            try:
                self._items[question_id].link(
                    trace, run_name=config_id,
                    run_metadata={"model": model_name, "prompt": prompt_name,
                                  "run_id": run_id})
            except Exception:  # noqa: BLE001
                pass

        trace_url = f"{self._base}/traces/{trace_id}"
        session_url = f"{self._base}/sessions/{quote(session_id, safe='')}"
        return trace_url, session_url

    def flush(self) -> None:
        self._lf.flush()
