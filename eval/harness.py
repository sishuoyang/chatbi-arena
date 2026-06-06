"""Grid runner: for each (model x prompt) config, run every golden question,
grade against cached golden results, persist to eval_runs, mirror to LangFuse.

Usage: source .env && python -m eval.harness [--run-id RID] [--limit N]
"""
import argparse
import time
import uuid
from arena.config import load_config
from agents.chclient import make_admin_client, ROClickHouseClient
from agents.bedrock import BedrockClient, cost_usd
from agents.loop import run_agent
from eval.golden import load_golden, fewshot_examples
from eval.grading import grade, classify_outcome
from eval.results import ensure_results_tables, write_eval_run, EvalRunRow
from eval.langfuse_adapter import LangfuseTracer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--limit", type=int, default=0, help="limit number of questions")
    args = ap.parse_args()

    cfg = load_config()
    db = cfg.clickhouse.database
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:8]}"

    admin = make_admin_client(cfg.clickhouse)
    ensure_results_tables(admin, db)
    ro = ROClickHouseClient(cfg.clickhouse)
    bedrock = BedrockClient(cfg.bedrock.region)
    tracer = LangfuseTracer(cfg.langfuse)

    with open("schema/schema_context.md") as f:
        schema_ctx = f.read()

    questions = load_golden()
    if args.limit:
        questions = questions[:args.limit]

    # Snapshot golden results once per run (grading determinism, spec §15).
    snapshot_ts = admin.query("SELECT now()").result_rows[0][0]
    golden_cache = {}
    for q in questions:
        gr = ro.query(q.golden_sql)
        golden_cache[q.id] = (gr.rows, gr.cols)

    model_names, prompt_names = cfg.resolved_grid()
    print(f"run_id={run_id} configs={len(model_names)}x{len(prompt_names)} "
          f"questions={len(questions)}")

    for mname in model_names:
        mcfg = cfg.model_by_name(mname)
        for pname in prompt_names:
            pcfg = cfg.prompt_by_name(pname)
            config_id = f"{mname}__{pname}"
            examples = fewshot_examples(questions, pcfg.k) if pcfg.k else None
            for q in questions:
                gold_rows, gold_cols = golden_cache[q.id]
                t0 = time.time()
                ar = run_agent(q.question, mcfg, pcfg, schema_ctx, ro, bedrock,
                               dict(cfg.bedrock.inference), examples=examples,
                               max_retries=cfg.eval.default_max_retries)
                latency_ms = int((time.time() - t0) * 1000)
                score = grade(ar, gold_rows, gold_cols, q.ordered, cfg.eval.float_dp)
                outcome = classify_outcome(ar, gold_rows, score)
                c = cost_usd(ar.usage, mcfg.price_per_1m_in, mcfg.price_per_1m_out)
                write_eval_run(admin, db, EvalRunRow(
                    run_id=run_id, config_id=config_id, model_name=mname,
                    prompt_name=pname, question_id=q.id, tier=q.tier,
                    correctness=score, cost_usd=c, latency_ms=latency_ms,
                    retries=ar.attempts - 1, outcome=outcome,
                    sql=ar.sql or "", tags=",".join(q.tags),
                    data_snapshot_ts=str(snapshot_ts),
                ))
                tracer.trace_run(
                    run_id=run_id, config_id=config_id, question_id=q.id,
                    question=q.question, sql=ar.sql or "", model_name=mname,
                    prompt_name=pname, usage=ar.usage, latency_ms=latency_ms,
                    correctness=score, cost_usd=c, outcome=outcome)
                print(f"  {config_id} {q.id} score={score} {outcome} "
                      f"{latency_ms}ms ${c:.5f}")
    tracer.flush()
    print(f"done. leaderboard: SELECT * FROM {db}.v_leaderboard "
          f"WHERE run_id='{run_id}' ORDER BY cost_per_correct_answer")


if __name__ == "__main__":
    main()
