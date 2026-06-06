"""Populate eval_runs with a CLEARLY-LABELED mock run to validate the full
grade->persist->leaderboard pipeline and the dashboard WITHOUT live Bedrock.

Replace with a real `python -m eval.harness` once Bedrock access is granted;
the only difference is the BedrockClient — every other line of the path is real.

Usage: source .env && python scripts/seed_mock_results.py
"""
import time
import uuid
from arena.config import load_config
from agents.bedrock import Usage, cost_usd
from agents.chclient import make_admin_client, ROClickHouseClient
from agents.loop import run_agent
from agents.prompts import PROMPT_BUILDERS
from eval.golden import load_golden
from eval.grading import grade, classify_outcome
from eval.results import ensure_results_tables, write_eval_run, EvalRunRow


class FakeBedrock:
    """Returns a canned SQL response. Simulates a stronger config (P3) that
    answers most questions and a weaker config (P1) that fumbles a few, so the
    leaderboard shows realistic variation. NOT a real model."""

    def __init__(self, golden_by_q, weak: bool):
        self._golden = golden_by_q
        self._weak = weak

    def converse(self, model_id, system, messages, inference):
        # recover question id from the trailing marker we inject below
        qid = messages[-1]["content"][0]["text"].rsplit("##QID:", 1)[-1].strip()
        sql = self._golden[qid]
        # weak config mangles a few harder questions to produce wrong/error rows
        if self._weak and qid in {"q011", "q016", "q017", "q018", "q020"}:
            sql = "SELECT 1 AS definitely_wrong"
        return type("R", (), {"text": f"```sql\n{sql}\n```",
                              "usage": Usage(900 if self._weak else 1200, 60)})()


def main() -> None:
    cfg = load_config()
    db = cfg.clickhouse.database
    run_id = f"mock-{uuid.uuid4().hex[:6]}"
    admin = make_admin_client(cfg.clickhouse)
    ensure_results_tables(admin, db)
    ro = ROClickHouseClient(cfg.clickhouse)

    questions = load_golden()
    golden_by_q = {q.id: q.golden_sql for q in questions}
    snapshot_ts = admin.query("SELECT now()").result_rows[0][0]
    golden_cache = {q.id: ro.query(q.golden_sql) for q in questions}

    configs = [("nova-lite", "P1_zeroshot", True, 0.06, 0.24),
               ("nova-lite", "P3_dialect", False, 0.06, 0.24)]

    # patch prompts to carry the qid so FakeBedrock can look up the golden SQL
    orig = dict(PROMPT_BUILDERS)

    for mname, pname, weak, pin, pout in configs:
        fake = FakeBedrock(golden_by_q, weak)
        config_id = f"{mname}__{pname}"
        for q in questions:
            def builder(schema_ctx, question, examples=None, _qid=q.id, _b=orig[pname]):
                s, m = _b(schema_ctx, question, examples)
                m[-1]["content"][0]["text"] += f"\n##QID:{_qid}"
                return s, m
            PROMPT_BUILDERS[pname] = builder
            t0 = time.time()
            ar = run_agent(q.question, cfg.model_by_name(mname),
                           cfg.prompt_by_name(pname), "SCHEMA", ro, fake,
                           dict(cfg.bedrock.inference), max_retries=0)
            latency_ms = int((time.time() - t0) * 1000)
            gold = golden_cache[q.id]
            score = grade(ar, gold.rows, gold.cols, q.ordered, cfg.eval.float_dp)
            outcome = classify_outcome(ar, gold.rows, score)
            c = cost_usd(ar.usage, pin, pout)
            write_eval_run(admin, db, EvalRunRow(
                run_id=run_id, config_id=config_id, model_name=mname,
                prompt_name=pname, question_id=q.id, tier=q.tier,
                correctness=score, cost_usd=c, latency_ms=latency_ms,
                retries=ar.attempts - 1, outcome=outcome, sql=ar.sql or "",
                tags=",".join(q.tags), data_snapshot_ts=snapshot_ts))
            print(f"  {config_id} {q.id} score={score} {outcome}")
    PROMPT_BUILDERS.clear()
    PROMPT_BUILDERS.update(orig)
    print(f"done. run_id={run_id}")


if __name__ == "__main__":
    main()
