import re
from dataclasses import dataclass
from agents.bedrock import BedrockClient, Usage, ZeroUsage
from agents.chclient import ROClickHouseClient
from agents.sqlguard import validate_select_only
from agents.prompts import PROMPT_BUILDERS, correction_turn
from arena.config import ModelCfg, PromptCfg

_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_sql_block(text: str) -> str:
    blocks = _FENCE.findall(text or "")
    if blocks:
        return blocks[-1].strip()
    return (text or "").strip()


@dataclass
class AgentResult:
    sql: str | None
    rows: list | None
    cols: list | None
    error: str | None
    attempts: int
    usage: Usage
    outcome_hint: str = ""  # 'sql_policy_rejected'|'sql_exec_error'|'ok'


def run_agent(question: str, model_cfg: ModelCfg, prompt_cfg: PromptCfg,
              schema_ctx: str, ch: ROClickHouseClient, bedrock: BedrockClient,
              inference: dict, examples=None, max_retries: int = 1) -> AgentResult:
    builder = PROMPT_BUILDERS[prompt_cfg.name]
    system, messages = builder(schema_ctx, question, examples)
    usage_total = ZeroUsage()
    last_sql = last_err = None
    hint = "ok"
    attempt = 0

    for attempt in range(max_retries + 1):
        resp = bedrock.converse(model_cfg.id, system, messages, inference)
        usage_total = usage_total + resp.usage
        last_sql = extract_sql_block(resp.text)
        ok, reason = validate_select_only(last_sql)
        if not ok:
            last_err, hint = f"policy: {reason}", "sql_policy_rejected"
        else:
            try:
                qr = ch.query(last_sql)
                return AgentResult(last_sql, qr.rows, qr.cols, None,
                                   attempt + 1, usage_total, "ok")
            except Exception as e:  # noqa: BLE001
                last_err, hint = str(e), "sql_exec_error"
        if attempt < max_retries and prompt_cfg.self_correct:
            messages = messages + correction_turn(last_sql or "", last_err or "")
            continue
        break

    return AgentResult(last_sql, None, None, last_err,
                       attempt + 1, usage_total, hint)
