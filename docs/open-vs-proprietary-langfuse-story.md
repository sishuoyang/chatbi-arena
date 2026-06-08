# Open vs. Proprietary on ClickHouse NL→SQL — a LangFuse-guided walkthrough

*A story in which a leaderboard tells us **what** happened, and LangFuse tells us **why** —
turning six red cells into a precise diagnosis, and saving us from blaming the models for
our own ambiguous questions.*

**Run:** `open-vs-proprietary1` · 6 models × 3 prompts (P1 zero-shot, P3 dialect, P5
self-correct) × 18 golden questions = 324 graded NL→SQL attempts against a live
ClickHouse dataset. Models: **Claude** (Sonnet 4.5, Opus 4.5) vs the open-weight camp
(**Qwen3-235b, DeepSeek-R1, gpt-oss-120b, Kimi-K2-thinking**).

> The two lenses: **ClickHouse `eval_runs`** is the scoreboard (who won). **LangFuse**
> is the film room (why). This walkthrough uses both — and every number, SQL snippet,
> and trace below is from the actual run.

---

## 1. The scoreboard (LangFuse Datasets & Experiments)

Each `model × prompt` config was run as a **LangFuse Dataset Run** against the
`arena-golden` **Dataset** (the 18 questions). That means LangFuse's native
**Experiment comparison** view shows every config side-by-side, with per-item
correctness/cost/latency/judge **Scores** attached to each trace — no custom UI needed.

The headline (sorted by accuracy, then cost-per-correct):

| Config | Accuracy | $ / correct | Avg latency |
|---|---|---|---|
| **Claude Sonnet 4.5** (P5 / P3) | **83%** (15/18) | $0.0043 | 2.6 s |
| **Qwen3-235b** (open) | **78%** (14/18) | **$0.0008** | 1.7 s |
| gpt-oss-120b (open) | 61% | $0.0004 | ~2 s |
| Claude Opus 4.5 | 72% | $0.0073–0.0083 | 2.4 s |
| DeepSeek-R1 (reasoning) | 50–72% | $0.003–0.005 | **6 s** |
| Kimi-K2-thinking (reasoning) | 50–56% | $0.005 | **7–8.5 s** |

**What the scoreboard says:** Claude Sonnet wins raw accuracy; **open-weight Qwen3-235b
is the value champion** — one question behind Sonnet at **~6× lower cost per correct
answer**; Opus is *worse and pricier* than Sonnet; the reasoning models are slow and
didn't convert their extra thinking into accuracy.

That's the **what**. Now we open LangFuse to find the **why** — and three of these
stories turn out to be more interesting than the ranking.

---

## 2. Drill 1 — "Why did *all six* models fail q007?" (Traces → root cause)

The Experiment view shows one row that's **red across every single run**:

> **q007** (tier 2): *"Top 5 product categories by revenue in the last 30 days, highest first."*

Six different models — including the frontier Claude Opus — all marked `wrong_result`
on a *tier-2* question. Either every model is incompetent, or something else is going on.
**Click the trace** ([example: claude-opus-4.5 q007](https://us.cloud.langfuse.com/project/cmq1yepps03v0ad0du2ba9qa9/traces/b0a0d810-c82f-52ef-88e8-3e39e6d16bf0)).

The trace shows the exact prompt, the generated SQL, and the result. Claude Sonnet's SQL:

```sql
SELECT p.category, SUM(oi.quantity * oi.unit_price - oi.discount) AS revenue
FROM v_order_items oi
JOIN v_orders o ON oi.order_id = o.order_id
JOIN v_products p ON oi.product_id = p.product_id
WHERE o.order_ts >= now() - INTERVAL 30 DAY
GROUP BY p.category ORDER BY revenue DESC LIMIT 5
```

Running it next to the golden, the **ordering is identical** but the **numbers are off**:

| category | model revenue | golden revenue |
|---|---|---|
| home | 5,536,453 | **4,951,288** |
| grocery | 3,369,052 | **3,013,565** |

Every model's revenue is *inflated by the same amount*. Comparing the SQL to the golden
reveals the single difference: the golden has `AND o.status NOT IN ('cancelled','returned')`
— **the models counted cancelled and returned orders**, the golden doesn't.

**The punchline:** re-read the question. *"Top 5 product categories by revenue in the
last 30 days"* — it **never says** to exclude cancelled/returned orders. The golden
quietly assumed a business rule the prompt didn't state. (Contrast **q005**, which *does*
say *"excluding cancelled and returned orders"* — there the models passed.)

> **Verdict:** not a model failure — an **ambiguous benchmark question**. Six identical
> "wrong" results were the tell. Without the traces we'd have wrongly concluded the models
> can't do tier-2 aggregation; *with* them, we found a bug in our own golden set.
>
> **LangFuse features used:** Experiment comparison (spot the all-red row) → Trace
> (prompt + generated SQL + result) → cross-model comparison (same omission everywhere).
> **Action:** add the business rule to q007 (or accept the literal reading) and re-run.

---

## 3. Drill 2 — "When open beat proprietary" — q015 (Trace diff)

q015 (tier 4, *"Daily delivered-order revenue for the last 14 days, oldest first"*) is
**green for DeepSeek-R1 and Qwen3-235b but red for both Claude models** — a rare case
where the open camp wins outright. Why?

The traces show near-identical SQL; the only difference is the date window. Claude:

```sql
WHERE o.status = 'delivered'
  AND o.order_ts >= today() - INTERVAL 14 DAY
  AND o.order_ts <  today()          -- excludes today
```

Golden / Qwen / DeepSeek used `o.order_ts >= now() - INTERVAL 14 DAY` (includes today).
Result: Claude returned **14 days**, the golden **15**. The daily values that *do* overlap
match to the cent — Claude's answer is off by exactly one boundary day.

> **Verdict:** another **specification ambiguity** ("last 14 days" — inclusive of today?),
> not a capability gap. The open models happened to match the golden's convention; Claude's
> reading was defensible but off-by-one. Again, only visible by diffing the traces.
>
> **LangFuse features used:** Trace inputs/outputs side-by-side across configs.

---

## 4. Drill 3 — Why the reasoning models are slow *and* fragile (Latency + the conversation view)

The leaderboard flagged DeepSeek-R1 and Kimi-K2-thinking as slow. The **trace latencies**
make it concrete — the slowest calls in the whole run:

| Model | Question | Latency | Outcome |
|---|---|---|---|
| Kimi-K2-thinking | q018 (P5) | **37.6 s** | correct |
| Kimi-K2-thinking | q016 (P5) | 27.0 s | wrong_result |
| DeepSeek-R1 | q018 (P5) | 24.2 s | sql_policy_rejected |

Open the trace and the **generation span** shows why: the output is dominated by
chain-of-thought tokens before any SQL appears. For a single-statement analytics query,
that "thinking" bought no accuracy here — just 10–20× the latency and cost of Qwen/Claude.

Worse, reasoning models are **format-fragile**. Several DeepSeek-R1 and Kimi attempts came
back `sql_policy_rejected` ([e.g. Kimi q015, 15.5 s](https://us.cloud.langfuse.com/project/cmq1yepps03v0ad0du2ba9qa9/traces/e7f12a41-a4ac-5241-950e-4dac9ba83257)). The **"View conversation"** panel (read live from the LangFuse
API) shows the model wrapped its answer so the SQL couldn't be cleanly extracted / wasn't
a single `SELECT`, so the read-only SQL guard correctly refused it. You can watch the whole
exchange — including P5's self-correction turn — replayed as a chat.

> **Verdict:** for structured NL→SQL, reasoning models are a poor trade: slower, costlier,
> and more likely to emit unusable output. The trace latency + the conversation replay
> show exactly where the time and the failures went.
>
> **LangFuse features used:** per-trace latency, the generation span (token usage), and
> the in-app **conversation/session replay**.

---

## 5. Drill 4 — Don't trust the LLM judge alone (multiple Scores per trace)

Every trace carries an **`llm_judge` score** (a Bedrock model rating the SQL's quality)
*alongside* the ground-truth `correctness` score. Filtering for **`llm_judge ≥ 0.8` AND
`correctness = 0`** returns **100 traces** — answers the judge loved that were actually
wrong. The q007 traces are textbook: the judge gave Claude Opus's SQL **0.9** because it
*looks* correct (right tables, right shape) — but it returns the wrong numbers.

> **Verdict:** an LLM judge grades *plausibility*; **execution accuracy is the ground
> truth.** Keep the judge as a cheap secondary signal, but rank on execution. LangFuse
> makes the disagreement visible: both scores sit on every trace, and you can sort/filter
> to find exactly where they diverge.
>
> **LangFuse features used:** multiple named Scores per trace + score-based filtering.

---

## 6. Drill 5 — Replay an agent end-to-end (Sessions)

Every run sets `session_id = <run>__<config>`, so a **LangFuse Session** is one model's
*entire* pass over the golden set — 18 Q→SQL exchanges in sequence. The Session view (and
the dashboard's **"View conversation"** button, which reads it via the LangFuse API) lets
you replay a model's behavior as one conversation: spot a model that *consistently* forgets
a filter, see whether P5's self-correction actually fixed anything, or scan for the verbose
reasoning that blows up latency. It's the difference between a number and a behavior.

> **LangFuse features used:** Sessions (trace grouping) + conversation replay.

---

## 7. What we learned

**For the business question** ("which model + prompt for NL→SQL, and what does it cost?"):
- **Qwen3-235b (open-weight) is the value pick** — near-top accuracy at ~6× lower cost
  per correct answer than Claude Sonnet, and faster.
- **Claude Sonnet 4.5 is the accuracy pick** — but you pay a large premium for ~1 extra
  question.
- **Claude Opus 4.5 and the reasoning models are poor ROI here** — Opus is worse *and*
  pricier than Sonnet; reasoning models are slow, fragile, and no more accurate.
- **Prompt strategy barely moved the needle** — self-correct (P5) and the dialect
  cheat-sheet (P3) rarely beat plain zero-shot; they mostly added latency.

**For the methodology** (the real point of the rig): execution-accuracy grading + LangFuse
drill-down separated three very different causes that a single accuracy number would have
blurred together:
1. **Genuine ranking differences** (Qwen vs Opus vs reasoning).
2. **Ambiguous golden questions** — q007 and q015 weren't model failures at all; they were
   under-specified questions. We only knew because the traces showed every model making the
   *same reasonable* choice.
3. **Operational issues** — reasoning-model latency and format-fragility, visible in span
   timings and the conversation replay.

> Two of our "failures" were **bugs in the eval, not the models** — and they were
> invisible on the leaderboard. ClickHouse told us the score; **LangFuse told us the story.**

---

## 8. Reproduce / explore it yourself

- **Leaderboard:** web UI → Leaderboard tab → select run `open-vs-proprietary1`. Click any
  config row to drill into per-question results; each row links to its **LangFuse trace**;
  **"View conversation"** replays the session from the LangFuse API; **"Open experiment in
  LangFuse"** opens the native Dataset/Experiment comparison.
- **Re-run after fixing the ambiguous questions:** add the missing business rules to q007 /
  q015 in `golden/questions.yaml`, then ▶ Run benchmark again and compare runs in LangFuse.
- **The data:** `arena_house.eval_runs` (run_id `open-vs-proprietary1`) in ClickHouse;
  traces/sessions/dataset-runs in LangFuse Cloud.
