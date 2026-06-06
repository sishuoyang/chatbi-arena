# ChatBI Arena — Measurement Core (M0–M3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-contained, demoable NL→SQL benchmark core: seed ClickHouse Cloud (AWS) with synthetic e-commerce data, run a Bedrock-backed agent through a read-only SQL sandbox, grade answers by execution accuracy against a golden set, persist per-config results to a ClickHouse table, mirror traces to LangFuse, and render a leaderboard dashboard from ClickHouse.

**Architecture:** Approach B (measurement core first). The stable contract is the `v_*` ClickHouse views; in this plan they sit over plain `ReplacingMergeTree` seed tables loaded directly (a later plan repoints them at ClickPipes CDC tables). Orchestration (agent loop, grading, harness, dashboard) runs locally in Python; Bedrock provides inference via the Converse API; results land in `arena.eval_runs` which is the dashboard's single source of truth; LangFuse Cloud holds rich traces in parallel.

**Tech Stack:** Python 3.11, clickhouse-connect, boto3 (Bedrock Converse), langfuse SDK, FastAPI + uvicorn, PyYAML, pydantic, python-dotenv, pytest.

---

## File Structure

```
chatbi-arena/
├── .gitignore
├── .env                         # EXISTS — secrets (gitignored)
├── requirements.txt
├── config.yaml                  # models, prompts, pricing, eval settings, limits
├── arena/
│   ├── __init__.py
│   └── config.py                # typed config loader, ${ENV} expansion
├── agents/
│   ├── __init__.py
│   ├── chclient.py              # ROClickHouseClient (read-only) + admin client factory
│   ├── sqlguard.py              # validate_select_only
│   ├── bedrock.py               # Usage, BedrockClient.converse
│   ├── prompts.py               # P1..P5 builders, correction_turn
│   └── loop.py                  # AgentResult, run_agent, extract_sql_block
├── eval/
│   ├── __init__.py
│   ├── grading.py               # normalize, grade, classify_outcome
│   ├── golden.py                # GoldenQuestion, load_golden
│   ├── results.py               # ensure_results_tables, write_eval_run, EvalRunRow
│   ├── langfuse_adapter.py      # version-isolated LangFuse tracing/scores
│   └── harness.py               # grid runner CLI
├── datagen/
│   ├── __init__.py
│   └── generator.py             # synthetic e-commerce → ClickHouse seed tables
├── schema/
│   ├── seed_tables.sql          # phase-1 RMT base tables
│   ├── clickhouse_views.sql     # v_* views (the agent-facing contract)
│   ├── gen_schema_context.py    # regenerate schema_context.md from system.columns
│   └── schema_context.md        # generated; injected into prompts
├── golden/
│   └── questions.yaml           # ~20 golden questions across 5 tiers
├── dashboard/
│   ├── app.py                   # FastAPI: JSON from ClickHouse + static HTML
│   └── static/index.html        # leaderboard UI
├── scripts/
│   ├── check_connectivity.py    # M0: verify CH + Bedrock + LangFuse reachable
│   └── setup_clickhouse.py      # create db, RO user, seed tables, views
└── tests/
    ├── test_sqlguard.py
    ├── test_grading.py
    ├── test_extract_sql.py
    └── test_config.py
```

---

## Task 0: Project scaffold

**Files:**
- Create: `.gitignore`, `requirements.txt`, `arena/__init__.py`, `agents/__init__.py`, `eval/__init__.py`, `datagen/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Initialize git and Python venv**

```bash
cd /Users/yss/code/chatbi-arena
git init
python3.11 -m venv .venv
source .venv/bin/activate
python -V   # expect Python 3.11.x
```

- [ ] **Step 2: Write `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.env
.pytest_cache/
*.egg-info/
.DS_Store
schema/schema_context.md
```

- [ ] **Step 3: Write `requirements.txt`**

```
clickhouse-connect==0.8.15
boto3==1.35.74
langfuse==2.57.0
fastapi==0.115.6
uvicorn==0.34.0
pyyaml==6.0.2
pydantic==2.10.4
python-dotenv==1.0.1
faker==33.1.0
pytest==8.3.4
```

- [ ] **Step 4: Install dependencies**

Run: `pip install -r requirements.txt`
Expected: all install without error. (If `langfuse==2.57.0` is unavailable, pin to the latest 2.x and note it; the adapter in Task 11 isolates the SDK.)

- [ ] **Step 5: Create empty package markers**

```bash
mkdir -p arena agents eval datagen schema golden dashboard/static scripts tests
touch arena/__init__.py agents/__init__.py eval/__init__.py datagen/__init__.py tests/__init__.py
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: project scaffold, deps, gitignore"
```

---

## Task 1: Typed config loader

**Files:**
- Create: `config.yaml`, `arena/config.py`, `tests/test_config.py`

- [ ] **Step 1: Write `config.yaml`**

```yaml
clickhouse:
  host: ${CLICKHOUSE_CLOUD_HOST}
  port: 8443
  secure: true
  database: ${CLICKHOUSE_CLOUD_DATABASE}
  admin_user: ${CLICKHOUSE_CLOUD_USER}
  admin_password: ${CLICKHOUSE_CLOUD_PASSWORD}
  ro_user: arena_ro
  ro_password: ${ARENA_RO_PASSWORD}
  query_limits:
    max_execution_time: 15
    max_result_rows: 100000
    max_memory_usage: 4000000000
    max_rows_to_read: 200000000

bedrock:
  region: ap-southeast-1
  inference:
    temperature: 0.0
    maxTokens: 1024

langfuse:
  host: ${LANGFUSE_BASE_URL}
  public_key: ${LANGFUSE_PUBLIC_KEY}
  secret_key: ${LANGFUSE_SECRET_KEY}

eval:
  float_dp: 4
  default_max_retries: 1
  run_tag: "core"

# Start M1-M3 with ONE cheap model. Verify exact IDs/prices for ap-southeast-1.
models:
  - id: apac.amazon.nova-lite-v1:0
    name: nova-lite
    family: nova
    price_per_1m_in: 0.06
    price_per_1m_out: 0.24

prompts:
  - name: P1_zeroshot
    self_correct: false
  - name: P3_dialect
    self_correct: false

grid:
  models: ["*"]
  prompts: ["*"]
```

- [ ] **Step 2: Write the failing test `tests/test_config.py`**

```python
import os
from arena.config import load_config

def test_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_CLOUD_HOST", "example.clickhouse.cloud")
    monkeypatch.setenv("CLICKHOUSE_CLOUD_DATABASE", "arena_house")
    monkeypatch.setenv("CLICKHOUSE_CLOUD_USER", "default")
    monkeypatch.setenv("CLICKHOUSE_CLOUD_PASSWORD", "pw")
    monkeypatch.setenv("ARENA_RO_PASSWORD", "ropw")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    cfg = load_config("config.yaml")
    assert cfg.clickhouse.host == "example.clickhouse.cloud"
    assert cfg.clickhouse.database == "arena_house"
    assert cfg.clickhouse.ro_user == "arena_ro"
    assert cfg.bedrock.region == "ap-southeast-1"
    assert len(cfg.models) == 1
    assert cfg.models[0].name == "nova-lite"
    assert {p.name for p in cfg.prompts} == {"P1_zeroshot", "P3_dialect"}

def test_resolve_grid():
    # '*' expands to all configured names
    from arena.config import load_config
    cfg = load_config("config.yaml")
    model_names, prompt_names = cfg.resolved_grid()
    assert model_names == ["nova-lite"]
    assert prompt_names == ["P1_zeroshot", "P3_dialect"]
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arena.config'`

- [ ] **Step 4: Write `arena/config.py`**

```python
import os
import re
from typing import Any
import yaml
from pydantic import BaseModel

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            var = m.group(1)
            if var not in os.environ:
                raise KeyError(f"Missing required env var: {var}")
            return os.environ[var]
        return _ENV_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


class QueryLimits(BaseModel):
    max_execution_time: int
    max_result_rows: int
    max_memory_usage: int
    max_rows_to_read: int


class ClickHouseCfg(BaseModel):
    host: str
    port: int
    secure: bool
    database: str
    admin_user: str
    admin_password: str
    ro_user: str
    ro_password: str
    query_limits: QueryLimits


class BedrockCfg(BaseModel):
    region: str
    inference: dict


class LangfuseCfg(BaseModel):
    host: str
    public_key: str
    secret_key: str


class EvalCfg(BaseModel):
    float_dp: int
    default_max_retries: int
    run_tag: str


class ModelCfg(BaseModel):
    id: str
    name: str
    family: str
    price_per_1m_in: float
    price_per_1m_out: float


class PromptCfg(BaseModel):
    name: str
    self_correct: bool = False
    k: int = 0


class GridCfg(BaseModel):
    models: list[str]
    prompts: list[str]


class Config(BaseModel):
    clickhouse: ClickHouseCfg
    bedrock: BedrockCfg
    langfuse: LangfuseCfg
    eval: EvalCfg
    models: list[ModelCfg]
    prompts: list[PromptCfg]
    grid: GridCfg

    def resolved_grid(self) -> tuple[list[str], list[str]]:
        all_models = [m.name for m in self.models]
        all_prompts = [p.name for p in self.prompts]
        mods = all_models if self.grid.models == ["*"] else self.grid.models
        prms = all_prompts if self.grid.prompts == ["*"] else self.grid.prompts
        return mods, prms

    def model_by_name(self, name: str) -> ModelCfg:
        return next(m for m in self.models if m.name == name)

    def prompt_by_name(self, name: str) -> PromptCfg:
        return next(p for p in self.prompts if p.name == name)


def load_config(path: str = "config.yaml") -> Config:
    from dotenv import load_dotenv
    load_dotenv()
    with open(path) as f:
        raw = yaml.safe_load(f)
    return Config(**_expand(raw))
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (both tests). Note: the `.env` file already defines the ClickHouse + LangFuse vars; `ARENA_RO_PASSWORD` is added in Task 2.

- [ ] **Step 6: Commit**

```bash
git add config.yaml arena/config.py tests/test_config.py
git commit -m "feat: typed config loader with env expansion"
```

---

## Task 2: ClickHouse clients + setup (db, RO user) — M0 connectivity

**Files:**
- Create: `agents/chclient.py`, `scripts/setup_clickhouse.py`
- Modify: `.env` (append `ARENA_RO_PASSWORD`)

- [ ] **Step 1: Append a RO password to `.env`**

Add this line to `.env` (pick any strong value):
```
export ARENA_RO_PASSWORD="Arena_ro_2026!demo"
```

- [ ] **Step 2: Write `agents/chclient.py`**

```python
from dataclasses import dataclass
import clickhouse_connect
from arena.config import ClickHouseCfg


def make_admin_client(cfg: ClickHouseCfg):
    """Full-privilege client for setup + writing results. Never used to run agent SQL."""
    return clickhouse_connect.get_client(
        host=cfg.host, port=cfg.port, secure=cfg.secure,
        username=cfg.admin_user, password=cfg.admin_password,
        database=cfg.database,
    )


@dataclass
class QueryResult:
    rows: list[tuple]
    cols: list[str]


class ROClickHouseClient:
    """Read-only client used to execute agent-generated SQL. Enforces server-side limits."""

    def __init__(self, cfg: ClickHouseCfg):
        self._cfg = cfg
        lim = cfg.query_limits
        self._settings = {
            "readonly": 1,
            "max_execution_time": lim.max_execution_time,
            "max_result_rows": lim.max_result_rows,
            "max_memory_usage": lim.max_memory_usage,
            "max_rows_to_read": lim.max_rows_to_read,
            "result_overflow_mode": "throw",
        }
        self._client = clickhouse_connect.get_client(
            host=cfg.host, port=cfg.port, secure=cfg.secure,
            username=cfg.ro_user, password=cfg.ro_password,
            database=cfg.database,
        )

    def query(self, sql: str) -> QueryResult:
        res = self._client.query(sql, settings=self._settings)
        rows = [tuple(r) for r in res.result_rows]
        return QueryResult(rows=rows, cols=list(res.column_names))
```

- [ ] **Step 3: Write `scripts/setup_clickhouse.py`** (creates database + read-only user; idempotent)

```python
"""Create the arena database and a least-privilege read-only user.
Run once after credentials are in place. Idempotent.
Usage: python scripts/setup_clickhouse.py
"""
from arena.config import load_config
from agents.chclient import make_admin_client


def main() -> None:
    cfg = load_config()
    ch = cfg.clickhouse
    admin = make_admin_client(ch)

    admin.command(f"CREATE DATABASE IF NOT EXISTS {ch.database}")

    admin.command(
        f"CREATE USER IF NOT EXISTS {ch.ro_user} "
        f"IDENTIFIED WITH sha256_password BY '{ch.ro_password}' "
        f"SETTINGS readonly = 1"
    )
    # Least privilege: SELECT only on the v_* views (granted after views exist).
    # Grant on the whole database is acceptable for the POC RO user; tighten later.
    admin.command(f"GRANT SELECT ON {ch.database}.* TO {ch.ro_user}")
    print(f"OK: database {ch.database} and user {ch.ro_user} ready.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run setup against the real service**

Run: `source .env && python scripts/setup_clickhouse.py`
Expected: `OK: database arena_house and user arena_ro ready.`
(Requires the ClickHouse Cloud creds in `.env` — already present.)

- [ ] **Step 5: Commit**

```bash
git add agents/chclient.py scripts/setup_clickhouse.py
git commit -m "feat: clickhouse admin + read-only clients and setup script"
```

---

## Task 3: SQL guard (TDD)

**Files:**
- Create: `agents/sqlguard.py`, `tests/test_sqlguard.py`

- [ ] **Step 1: Write failing test `tests/test_sqlguard.py`**

```python
import pytest
from agents.sqlguard import validate_select_only

@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select count() from v_orders",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "  \n SELECT a, b FROM v_orders WHERE a > 1  ",
    "SELECT * FROM v_orders -- a trailing comment\n",
])
def test_accepts_single_select(sql):
    ok, reason = validate_select_only(sql)
    assert ok is True, reason

@pytest.mark.parametrize("sql", [
    "",
    "   ",
    "INSERT INTO v_orders VALUES (1)",
    "DROP TABLE v_orders",
    "ALTER TABLE v_orders DELETE WHERE 1=1",
    "SYSTEM RELOAD CONFIG",
    "SELECT 1; DROP TABLE v_orders",          # multi-statement
    "SELECT 1; SELECT 2",                       # multi-statement
    "TRUNCATE TABLE v_orders",
    "GRANT SELECT ON *.* TO x",
    "select 1 into outfile 'x'",
])
def test_rejects_non_select_or_multi(sql):
    ok, reason = validate_select_only(sql)
    assert ok is False
    assert reason
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_sqlguard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.sqlguard'`

- [ ] **Step 3: Write `agents/sqlguard.py`**

```python
import re

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|RENAME|ATTACH|DETACH|"
    r"OPTIMIZE|GRANT|REVOKE|SET|SYSTEM|KILL|INTO\s+OUTFILE|INTO\s+DUMPFILE)\b",
    re.IGNORECASE,
)


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return sql


def validate_select_only(sql: str) -> tuple[bool, str | None]:
    """Accept exactly one SELECT or WITH...SELECT statement. Reject everything else."""
    if sql is None:
        return False, "empty SQL"
    cleaned = _strip_comments(sql).strip().rstrip(";").strip()
    if not cleaned:
        return False, "empty SQL"
    if ";" in cleaned:
        return False, "multiple statements are not allowed"
    head = cleaned[:6].upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        return False, "only SELECT / WITH...SELECT statements are allowed"
    if _FORBIDDEN.search(cleaned):
        return False, "statement contains a forbidden keyword"
    return True, None
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_sqlguard.py -v`
Expected: PASS (all parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add agents/sqlguard.py tests/test_sqlguard.py
git commit -m "feat: read-only SQL guard with tests"
```

---

## Task 4: Bedrock Converse wrapper + connectivity check

**Files:**
- Create: `agents/bedrock.py`, `scripts/check_connectivity.py`

- [ ] **Step 1: Write `agents/bedrock.py`**

```python
from dataclasses import dataclass
import boto3


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens,
                     self.output_tokens + other.output_tokens)


def ZeroUsage() -> Usage:
    return Usage(0, 0)


@dataclass
class ConverseResult:
    text: str
    usage: Usage


class BedrockClient:
    def __init__(self, region: str):
        self._client = boto3.client("bedrock-runtime", region_name=region)

    def converse(self, model_id: str, system: str, messages: list[dict],
                 inference: dict) -> ConverseResult:
        """messages: [{"role": "user"|"assistant", "content": [{"text": "..."}]}]"""
        kwargs = dict(modelId=model_id, messages=messages,
                      inferenceConfig=inference)
        if system:
            kwargs["system"] = [{"text": system}]
        resp = self._client.converse(**kwargs)
        parts = resp["output"]["message"]["content"]
        text = "".join(p.get("text", "") for p in parts)
        u = resp.get("usage", {})
        usage = Usage(input_tokens=u.get("inputTokens", 0),
                      output_tokens=u.get("outputTokens", 0))
        return ConverseResult(text=text, usage=usage)


def cost_usd(usage: Usage, price_in_per_1m: float, price_out_per_1m: float) -> float:
    return (usage.input_tokens / 1_000_000.0) * price_in_per_1m + \
           (usage.output_tokens / 1_000_000.0) * price_out_per_1m
```

- [ ] **Step 2: Write `scripts/check_connectivity.py`** (M0 acceptance gate)

```python
"""Verify all external dependencies are reachable before building further.
Usage: source .env && python scripts/check_connectivity.py
"""
from arena.config import load_config


def check_clickhouse(cfg) -> None:
    from agents.chclient import make_admin_client
    admin = make_admin_client(cfg.clickhouse)
    v = admin.query("SELECT version()").result_rows[0][0]
    print(f"[ok] ClickHouse reachable, version {v}")


def check_bedrock(cfg) -> None:
    from agents.bedrock import BedrockClient
    bc = BedrockClient(cfg.bedrock.region)
    model_id = cfg.models[0].id
    res = bc.converse(model_id, system="You are a calculator.",
                      messages=[{"role": "user", "content": [{"text": "Reply with the number 2 only."}]}],
                      inference={"temperature": 0.0, "maxTokens": 8})
    print(f"[ok] Bedrock {model_id} responded: {res.text!r}, usage={res.usage}")


def check_langfuse(cfg) -> None:
    from langfuse import Langfuse
    lf = Langfuse(public_key=cfg.langfuse.public_key,
                  secret_key=cfg.langfuse.secret_key,
                  host=cfg.langfuse.host)
    assert lf.auth_check(), "LangFuse auth failed"
    print("[ok] LangFuse auth OK")


def main() -> None:
    cfg = load_config()
    check_clickhouse(cfg)
    check_langfuse(cfg)
    try:
        check_bedrock(cfg)
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] Bedrock check failed (need AWS creds + model access): {e}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the connectivity check**

Run: `source .env && python scripts/check_connectivity.py`
Expected: `[ok] ClickHouse ...`, `[ok] LangFuse auth OK`. Bedrock prints `[ok] ...` if AWS creds + model access are present, else `[WARN] ...`.
**Gate:** if Bedrock WARNs, flag to the user that AWS credentials / ap-southeast-1 model access are required before Task 9 (harness run). ClickHouse + LangFuse must both be `[ok]`.

- [ ] **Step 4: Commit**

```bash
git add agents/bedrock.py scripts/check_connectivity.py
git commit -m "feat: bedrock converse wrapper + connectivity check"
```

---

## Task 5: Seed tables + synthetic data generator (phase-1, direct to ClickHouse)

**Files:**
- Create: `schema/seed_tables.sql`, `datagen/generator.py`

- [ ] **Step 1: Write `schema/seed_tables.sql`** (phase-1 base tables mirroring the CDC convention: `ReplacingMergeTree` with a version column and a soft-delete marker, so the `v_*` views in Task 6 are identical in phase 1 and phase 2)

```sql
-- Phase-1 seed tables. Column conventions mirror ClickPipes CDC output so the
-- v_* views are unchanged when we later repoint them at CDC-landed tables.
CREATE TABLE IF NOT EXISTS arena_house.customers (
  customer_id UInt64, full_name String, email String, country String,
  segment String, signup_date Date, created_at DateTime64(3), updated_at DateTime64(3),
  _peerdb_version UInt64, _peerdb_is_deleted UInt8
) ENGINE = ReplacingMergeTree(_peerdb_version) ORDER BY customer_id;

CREATE TABLE IF NOT EXISTS arena_house.products (
  product_id UInt64, name String, category String, brand String,
  unit_price Decimal(10,2), unit_cost Decimal(10,2),
  created_at DateTime64(3), updated_at DateTime64(3),
  _peerdb_version UInt64, _peerdb_is_deleted UInt8
) ENGINE = ReplacingMergeTree(_peerdb_version) ORDER BY product_id;

CREATE TABLE IF NOT EXISTS arena_house.orders (
  order_id UInt64, customer_id UInt64, order_ts DateTime64(3), status String,
  channel String, created_at DateTime64(3), updated_at DateTime64(3),
  _peerdb_version UInt64, _peerdb_is_deleted UInt8
) ENGINE = ReplacingMergeTree(_peerdb_version) ORDER BY order_id;

CREATE TABLE IF NOT EXISTS arena_house.order_items (
  order_item_id UInt64, order_id UInt64, product_id UInt64, quantity UInt32,
  unit_price Decimal(10,2), discount Decimal(10,2),
  created_at DateTime64(3), updated_at DateTime64(3),
  _peerdb_version UInt64, _peerdb_is_deleted UInt8
) ENGINE = ReplacingMergeTree(_peerdb_version) ORDER BY order_item_id;

CREATE TABLE IF NOT EXISTS arena_house.events (
  event_id UInt64, customer_id Nullable(UInt64), session_id String,
  event_type String, product_id Nullable(UInt64), event_ts DateTime64(3),
  created_at DateTime64(3),
  _peerdb_version UInt64, _peerdb_is_deleted UInt8
) ENGINE = ReplacingMergeTree(_peerdb_version) ORDER BY event_id;
```

- [ ] **Step 2: Write `datagen/generator.py`** (seed ~90 days of history directly into the seed tables; deterministic with `--seed`)

```python
"""Synthetic e-commerce generator. Phase-1 mode: writes directly to ClickHouse
seed tables with ~90 days of history so time-relative golden questions have data.

Usage: source .env && python -m datagen.generator --seed 42 \
         --customers 2000 --products 300 --days 90
"""
import argparse
import random
from datetime import datetime, timedelta, timezone
from faker import Faker
from arena.config import load_config
from agents.chclient import make_admin_client

COUNTRIES = ["SG", "VN", "TH", "ID", "AU", "IN", "TW", "JP"]
SEGMENTS = ["consumer", "smb", "enterprise"]
CATEGORIES = ["electronics", "home", "apparel", "grocery", "beauty"]
CHANNELS = ["web", "ios", "android", "partner"]
EVENT_TYPES = ["view", "search", "add_to_cart", "checkout", "purchase"]
TERMINAL_OK = ["delivered"]
PROGRESSION = ["placed", "paid", "shipped", "delivered"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def gen(seed: int, n_customers: int, n_products: int, days: int) -> dict:
    rng = random.Random(seed)
    fake = Faker()
    Faker.seed(seed)
    now = _now()
    start = now - timedelta(days=days)

    customers, products, orders, items, events = [], [], [], [], []
    ver = 1

    for cid in range(1, n_customers + 1):
        signup = (start + timedelta(days=rng.randint(0, days))).date()
        ts = datetime(signup.year, signup.month, signup.day, tzinfo=timezone.utc)
        customers.append([cid, fake.name(), fake.unique.email(),
                          rng.choice(COUNTRIES), rng.choice(SEGMENTS),
                          signup, ts, ts, ver, 0])

    # long-tail popularity weights
    pop_weights = [1.0 / (i + 1) for i in range(n_products)]
    for pid in range(1, n_products + 1):
        price = round(rng.uniform(5, 800), 2)
        cost = round(price * rng.uniform(0.4, 0.8), 2)
        ts = start
        products.append([pid, f"{fake.color_name()} {fake.word().title()}",
                         rng.choice(CATEGORIES), fake.company(), price, cost,
                         ts, ts, ver, 0])

    oid = 0
    oiid = 0
    eid = 0
    n_orders = n_customers * 6  # ~6 orders/customer over the window
    for _ in range(n_orders):
        oid += 1
        cust = rng.randint(1, n_customers)
        # diurnal-ish: bias order hour toward 9-21
        day_offset = rng.randint(0, days - 1)
        hour = int(min(23, max(0, rng.gauss(15, 4))))
        ots = start + timedelta(days=day_offset, hours=hour,
                                minutes=rng.randint(0, 59))
        # status: most progress to delivered; small fraction cancelled/returned
        roll = rng.random()
        if roll < 0.05:
            status = "cancelled"
        elif roll < 0.10:
            status = "returned"
        else:
            status = rng.choice(PROGRESSION)
        upd = ots + timedelta(hours=rng.randint(1, 72))
        orders.append([oid, cust, ots, status, rng.choice(CHANNELS),
                       ots, upd, ver, 0])
        # 1-4 line items, weighted product choice
        for _ in range(rng.randint(1, 4)):
            oiid += 1
            pid = rng.choices(range(1, n_products + 1), weights=pop_weights)[0]
            p = products[pid - 1]
            qty = rng.randint(1, 5)
            disc = round(rng.choice([0, 0, 0, 5, 10]) * 1.0, 2)
            items.append([oiid, oid, pid, qty, p[4], disc, ots, ots, ver, 0])
        # session events around the order
        sess = f"s-{oid}-{rng.randint(1000,9999)}"
        for et in ["view", "add_to_cart", "checkout", "purchase"]:
            if et == "purchase" and status in ("cancelled",):
                continue
            eid += 1
            ets = ots - timedelta(minutes=rng.randint(1, 120))
            pid_ev = rng.randint(1, n_products)
            events.append([eid, cust, sess, et, pid_ev, ets, ets, ver, 0])
        # extra anonymous view-only sessions (for conversion-rate questions)
        if rng.random() < 0.4:
            eid += 1
            sess2 = f"a-{oid}-{rng.randint(1000,9999)}"
            ets = ots - timedelta(minutes=rng.randint(1, 240))
            events.append([eid, None, sess2, "view", rng.randint(1, n_products),
                           ets, ets, ver, 0])

    return {"customers": customers, "products": products, "orders": orders,
            "order_items": items, "events": events}


COLUMNS = {
    "customers": ["customer_id", "full_name", "email", "country", "segment",
                  "signup_date", "created_at", "updated_at", "_peerdb_version", "_peerdb_is_deleted"],
    "products": ["product_id", "name", "category", "brand", "unit_price", "unit_cost",
                 "created_at", "updated_at", "_peerdb_version", "_peerdb_is_deleted"],
    "orders": ["order_id", "customer_id", "order_ts", "status", "channel",
               "created_at", "updated_at", "_peerdb_version", "_peerdb_is_deleted"],
    "order_items": ["order_item_id", "order_id", "product_id", "quantity", "unit_price",
                    "discount", "created_at", "updated_at", "_peerdb_version", "_peerdb_is_deleted"],
    "events": ["event_id", "customer_id", "session_id", "event_type", "product_id",
               "event_ts", "created_at", "_peerdb_version", "_peerdb_is_deleted"],
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--customers", type=int, default=2000)
    ap.add_argument("--products", type=int, default=300)
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()

    cfg = load_config()
    admin = make_admin_client(cfg.clickhouse)
    db = cfg.clickhouse.database

    # apply seed_tables.sql
    with open("schema/seed_tables.sql") as f:
        for stmt in f.read().split(";"):
            if stmt.strip():
                admin.command(stmt)

    data = gen(args.seed, args.customers, args.products, args.days)
    for table, rows in data.items():
        if not rows:
            continue
        admin.insert(f"{db}.{table}", rows, column_names=COLUMNS[table])
        print(f"inserted {len(rows)} into {table}")
    print("seed complete")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the seed**

Run: `source .env && python -m datagen.generator --seed 42 --customers 2000 --products 300 --days 90`
Expected: `inserted N into customers ... seed complete`.

- [ ] **Step 4: Commit**

```bash
git add schema/seed_tables.sql datagen/generator.py
git commit -m "feat: seed tables + synthetic e-commerce generator (phase-1)"
```

---

## Task 6: Views + schema context

**Files:**
- Create: `schema/clickhouse_views.sql`, `schema/gen_schema_context.py`

- [ ] **Step 1: Write `schema/clickhouse_views.sql`** (the agent-facing contract — `FINAL` + soft-delete filter)

```sql
CREATE VIEW IF NOT EXISTS arena_house.v_customers AS
  SELECT * EXCEPT (_peerdb_version, _peerdb_is_deleted) FROM arena_house.customers FINAL
  WHERE _peerdb_is_deleted = 0;
CREATE VIEW IF NOT EXISTS arena_house.v_products AS
  SELECT * EXCEPT (_peerdb_version, _peerdb_is_deleted) FROM arena_house.products FINAL
  WHERE _peerdb_is_deleted = 0;
CREATE VIEW IF NOT EXISTS arena_house.v_orders AS
  SELECT * EXCEPT (_peerdb_version, _peerdb_is_deleted) FROM arena_house.orders FINAL
  WHERE _peerdb_is_deleted = 0;
CREATE VIEW IF NOT EXISTS arena_house.v_order_items AS
  SELECT * EXCEPT (_peerdb_version, _peerdb_is_deleted) FROM arena_house.order_items FINAL
  WHERE _peerdb_is_deleted = 0;
CREATE VIEW IF NOT EXISTS arena_house.v_events AS
  SELECT * EXCEPT (_peerdb_version, _peerdb_is_deleted) FROM arena_house.events FINAL
  WHERE _peerdb_is_deleted = 0;
```

- [ ] **Step 2: Write `schema/gen_schema_context.py`** (regenerate the agent-facing schema doc from `system.columns` so it never drifts)

```python
"""Apply v_* views and regenerate schema/schema_context.md from system.columns.
Usage: source .env && python schema/gen_schema_context.py
"""
from arena.config import load_config
from agents.chclient import make_admin_client

VIEWS = ["v_customers", "v_products", "v_orders", "v_order_items", "v_events"]

SEMANTICS = {
    "v_orders.status": "one of placed|paid|shipped|delivered|cancelled|returned",
    "v_orders.channel": "one of web|ios|android|partner",
    "v_customers.country": "ISO-ish: SG|VN|TH|ID|AU|IN|TW|JP",
    "v_customers.segment": "consumer|smb|enterprise",
    "v_products.category": "electronics|home|apparel|grocery|beauty",
    "v_events.event_type": "view|search|add_to_cart|checkout|purchase",
    "v_order_items.unit_price": "price at time of sale",
}


def main() -> None:
    cfg = load_config()
    db = cfg.clickhouse.database
    admin = make_admin_client(cfg.clickhouse)
    with open("schema/clickhouse_views.sql") as f:
        for stmt in f.read().split(";"):
            if stmt.strip():
                admin.command(stmt)

    lines = ["# Schema context (agent-facing)\n",
             "Query ONLY these views. ClickHouse SQL dialect. "
             "Revenue = quantity*unit_price - discount.\n"]
    for v in VIEWS:
        cols = admin.query(
            "SELECT name, type FROM system.columns "
            f"WHERE database = '{db}' AND table = '{v}' ORDER BY position"
        ).result_rows
        lines.append(f"\n## {v}")
        for name, typ in cols:
            sem = SEMANTICS.get(f"{v}.{name}", "")
            sem = f" — {sem}" if sem else ""
            lines.append(f"- `{name}` {typ}{sem}")
    with open("schema/schema_context.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote schema/schema_context.md")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run it and sanity-check a view**

Run:
```bash
source .env && python schema/gen_schema_context.py
python -c "from arena.config import load_config; from agents.chclient import ROClickHouseClient; \
c=ROClickHouseClient(load_config().clickhouse); r=c.query('SELECT count() FROM v_orders'); print(r.rows)"
```
Expected: `wrote schema/schema_context.md`, then a non-zero count from the read-only user (proves RO grants + views work).

- [ ] **Step 4: Commit**

```bash
git add schema/clickhouse_views.sql schema/gen_schema_context.py
git commit -m "feat: v_* views + generated schema context"
```

---

## Task 7: Prompt strategies

**Files:**
- Create: `agents/prompts.py`

- [ ] **Step 1: Write `agents/prompts.py`**

```python
"""Prompt strategy builders. Each returns (system_text, messages).
messages use the Bedrock Converse shape: [{"role","content":[{"text"}]}]."""

_BASE = (
    "You are a senior analytics engineer. Translate the user's question into a "
    "single ClickHouse SQL query. Query ONLY the provided views. Return the SQL "
    "inside one fenced ```sql code block and nothing else."
)

_DIALECT = (
    "\n\nClickHouse dialect notes:\n"
    "- Dates: now(), today(), toStartOfQuarter(), INTERVAL N DAY, toDate(x).\n"
    "- Aggregates: count(), sum(), avg(), uniqExact(), quantile(), argMax(), countIf(cond).\n"
    "- Use ROUND(x, 2) for money. Window: avg(x) OVER (ORDER BY d ROWS BETWEEN 6 PRECEDING AND CURRENT ROW).\n"
    "- No ILIKE; use lower(x) LIKE. Boolean expr like (a = 'x') yields 0/1."
)


def _user_msg(schema_ctx: str, question: str) -> list[dict]:
    content = f"Schema:\n{schema_ctx}\n\nQuestion: {question}"
    return [{"role": "user", "content": [{"text": content}]}]


def build_p1(schema_ctx, question, examples=None):
    return _BASE, _user_msg(schema_ctx, question)


def build_p3(schema_ctx, question, examples=None):
    return _BASE + _DIALECT, _user_msg(schema_ctx, question)


def build_p4(schema_ctx, question, examples=None):
    sys = (_BASE + "\n\nThink briefly step by step first, then put the FINAL query "
           "in the last ```sql block.")
    return sys, _user_msg(schema_ctx, question)


def build_p2(schema_ctx, question, examples=None):
    ex = examples or []
    shots = "\n\n".join(
        f"Q: {e['question']}\n```sql\n{e['golden_sql'].strip()}\n```" for e in ex)
    sys = _BASE + ("\n\nWorked examples:\n" + shots if shots else "")
    return sys, _user_msg(schema_ctx, question)


PROMPT_BUILDERS = {
    "P1_zeroshot": build_p1,
    "P2_fewshot": build_p2,
    "P3_dialect": build_p3,
    "P4_cot": build_p4,
    "P5_selfcorrect": build_p3,  # P3 prompt + retry enabled via self_correct flag
}


def correction_turn(prev_sql: str, error: str) -> list[dict]:
    return [
        {"role": "assistant", "content": [{"text": f"```sql\n{prev_sql}\n```"}]},
        {"role": "user", "content": [{"text":
            f"That query failed with ClickHouse error:\n{error}\n"
            "Return a corrected single ClickHouse SQL query in one ```sql block."}]},
    ]
```

- [ ] **Step 2: Commit**

```bash
git add agents/prompts.py
git commit -m "feat: P1-P5 prompt strategy builders"
```

---

## Task 8: SQL extraction + agent loop

**Files:**
- Create: `agents/loop.py`, `tests/test_extract_sql.py`

- [ ] **Step 1: Write failing test `tests/test_extract_sql.py`**

```python
from agents.loop import extract_sql_block

def test_extracts_fenced_sql():
    text = "Here:\n```sql\nSELECT 1\n```\nDone."
    assert extract_sql_block(text) == "SELECT 1"

def test_takes_last_block_for_cot():
    text = "```sql\nSELECT bad\n```\nfinal:\n```sql\nSELECT good\n```"
    assert extract_sql_block(text) == "SELECT good"

def test_falls_back_to_raw_when_no_fence():
    text = "SELECT 1 FROM v_orders"
    assert extract_sql_block(text) == "SELECT 1 FROM v_orders"

def test_handles_bare_triple_backticks():
    text = "```\nSELECT 2\n```"
    assert extract_sql_block(text) == "SELECT 2"
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_extract_sql.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `agents/loop.py`**

```python
import re
from dataclasses import dataclass, field
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_extract_sql.py -v`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add agents/loop.py tests/test_extract_sql.py
git commit -m "feat: agent loop + sql extraction with tests"
```

---

## Task 9: Grading (TDD — the crux)

**Files:**
- Create: `eval/grading.py`, `tests/test_grading.py`

- [ ] **Step 1: Write failing test `tests/test_grading.py`**

```python
from decimal import Decimal
from eval.grading import normalize, grade, classify_outcome
from agents.loop import AgentResult
from agents.bedrock import ZeroUsage

def _agent(rows, cols, error=None, hint="ok"):
    return AgentResult(sql="SELECT 1", rows=rows, cols=cols, error=error,
                       attempts=1, usage=ZeroUsage(), outcome_hint=hint)

def test_normalize_rounds_and_sentinels():
    rows = [(1, 2.0/3.0, None), (2, Decimal("1.50000"), "x")]
    out = normalize(rows, ordered=False, dp=4)
    assert ("1", "0.6667", "∅") in out
    assert ("2", "1.5000", "x") in out

def test_unordered_equal_despite_row_order():
    a = _agent([(1, "a"), (2, "b")], ["x", "y"])
    assert grade(a, golden_rows=[(2, "b"), (1, "a")], golden_cols=["x", "y"],
                 ordered=False) == 1

def test_ordered_penalizes_wrong_order():
    a = _agent([(2,), (1,)], ["n"])
    assert grade(a, golden_rows=[(1,), (2,)], golden_cols=["n"], ordered=True) == 0

def test_column_count_mismatch_scores_zero():
    a = _agent([(1, 2)], ["x", "y"])
    assert grade(a, golden_rows=[(1,)], golden_cols=["x"], ordered=False) == 0

def test_compare_by_position_not_name():
    a = _agent([(5,)], ["revenue"])
    assert grade(a, golden_rows=[(5,)], golden_cols=["total"], ordered=False) == 1

def test_float_equivalence_within_dp():
    a = _agent([(1.23456,)], ["v"])
    assert grade(a, golden_rows=[(1.23457,)], golden_cols=["v"], ordered=False) == 1

def test_both_empty_scores_one():
    a = _agent([], ["x"])
    assert grade(a, golden_rows=[], golden_cols=["x"], ordered=False) == 1

def test_error_scores_zero():
    a = _agent(None, None, error="boom", hint="sql_exec_error")
    assert grade(a, golden_rows=[(1,)], golden_cols=["x"], ordered=False) == 0

def test_keeps_duplicate_rows():
    a = _agent([(1,), (1,)], ["x"])
    assert grade(a, golden_rows=[(1,)], golden_cols=["x"], ordered=False) == 0

def test_outcome_taxonomy():
    assert classify_outcome(_agent(None, None, "e", "sql_policy_rejected"),
                            golden_rows=[(1,)], score=0) == "sql_policy_rejected"
    assert classify_outcome(_agent([(1,)], ["x"]), golden_rows=[(1,)], score=1) == "correct"
    assert classify_outcome(_agent([(9,)], ["x"]), golden_rows=[(1,)], score=0) == "wrong_result"
    assert classify_outcome(_agent([], ["x"]), golden_rows=[(1,)], score=0) == "empty_but_expected"
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_grading.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write `eval/grading.py`**

```python
from decimal import Decimal


def normalize(rows, ordered: bool, dp: int = 4):
    out = []
    for r in rows:
        cells = []
        for v in r:
            if v is None:
                cells.append("∅")
            elif isinstance(v, (float, Decimal)):
                cells.append(f"{float(v):.{dp}f}")
            elif isinstance(v, bool):
                cells.append("1" if v else "0")
            else:
                cells.append(str(v))
        out.append(tuple(cells))
    return out if ordered else sorted(out)


def grade(agent, golden_rows, golden_cols, ordered: bool, dp: int = 4) -> int:
    if agent.error or agent.rows is None:
        return 0
    if not agent.rows and not golden_rows:
        return 1
    if agent.rows and golden_rows and len(agent.cols) != len(golden_cols):
        return 0
    return int(normalize(agent.rows, ordered, dp) == normalize(golden_rows, ordered, dp))


def classify_outcome(agent, golden_rows, score: int) -> str:
    if score == 1:
        return "correct"
    if agent.outcome_hint == "sql_policy_rejected":
        return "sql_policy_rejected"
    if agent.outcome_hint == "sql_exec_error" or agent.error:
        return "sql_exec_error"
    if agent.rows is not None and len(agent.rows) == 0 and golden_rows:
        return "empty_but_expected"
    return "wrong_result"
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_grading.py -v`
Expected: PASS (all cases). This suite is the make-or-break per spec §16; do not proceed to the harness until green.

- [ ] **Step 5: Commit**

```bash
git add eval/grading.py tests/test_grading.py
git commit -m "feat: execution-accuracy grading with should-match/should-not-match suite"
```

---

## Task 10: Golden question set + loader

**Files:**
- Create: `golden/questions.yaml`, `eval/golden.py`

- [ ] **Step 1: Write `golden/questions.yaml`** (~20 questions across tiers; a few flagged `fewshot_holdout: true` for P2). All SQL targets `v_*` views and ClickHouse dialect.

```yaml
- id: q001
  tier: 1
  question: "How many customers are there in total?"
  ordered: false
  golden_sql: "SELECT count() FROM v_customers"
  tags: [count]
- id: q002
  tier: 1
  question: "How many orders were placed in the last 30 days, excluding cancelled and returned?"
  ordered: false
  golden_sql: |
    SELECT count() FROM v_orders
    WHERE order_ts >= now() - INTERVAL 30 DAY AND status NOT IN ('cancelled','returned')
  tags: [count, filter]
- id: q003
  tier: 1
  question: "How many distinct product categories exist?"
  ordered: false
  golden_sql: "SELECT uniqExact(category) FROM v_products"
  tags: [distinct]
- id: q004
  tier: 1
  question: "What is the total number of order line items?"
  ordered: false
  golden_sql: "SELECT count() FROM v_order_items"
  tags: [count]
- id: q005
  tier: 2
  question: "Total revenue in the last 30 days. Revenue = quantity*unit_price - discount, excluding cancelled and returned orders. Return a single number rounded to 2 decimals."
  ordered: false
  golden_sql: |
    SELECT round(sum(oi.quantity * oi.unit_price - oi.discount), 2)
    FROM v_order_items oi INNER JOIN v_orders o ON oi.order_id = o.order_id
    WHERE o.order_ts >= now() - INTERVAL 30 DAY AND o.status NOT IN ('cancelled','returned')
  tags: [revenue, join]
- id: q006
  tier: 2
  question: "Count of orders by status, highest count first. Return status and count."
  ordered: true
  golden_sql: "SELECT status, count() AS c FROM v_orders GROUP BY status ORDER BY c DESC"
  tags: [groupby, orderby]
- id: q007
  tier: 2
  question: "Top 5 product categories by revenue in the last 30 days, highest first. Return category and revenue."
  ordered: true
  golden_sql: |
    SELECT p.category, round(sum(oi.quantity * oi.unit_price - oi.discount), 2) AS revenue
    FROM v_order_items oi
    INNER JOIN v_orders o ON oi.order_id = o.order_id
    INNER JOIN v_products p ON oi.product_id = p.product_id
    WHERE o.order_ts >= now() - INTERVAL 30 DAY AND o.status NOT IN ('cancelled','returned')
    GROUP BY p.category ORDER BY revenue DESC LIMIT 5
  tags: [groupby, topn, join]
- id: q008
  tier: 2
  question: "Number of customers by country, most first. Return country and count."
  ordered: true
  golden_sql: "SELECT country, count() AS c FROM v_customers GROUP BY country ORDER BY c DESC"
  tags: [groupby]
- id: q009
  tier: 2
  question: "Average order line-item discount across all items, rounded to 2 decimals."
  ordered: false
  golden_sql: "SELECT round(avg(discount), 2) FROM v_order_items"
  tags: [avg]
- id: q010
  tier: 3
  question: "Top 10 customers by total spend overall (delivered, paid, shipped, placed only — exclude cancelled and returned). Return name and spend, highest first."
  ordered: true
  golden_sql: |
    SELECT c.full_name, round(sum(oi.quantity * oi.unit_price - oi.discount), 2) AS spend
    FROM v_order_items oi
    INNER JOIN v_orders o ON oi.order_id = o.order_id
    INNER JOIN v_customers c ON o.customer_id = c.customer_id
    WHERE o.status NOT IN ('cancelled','returned')
    GROUP BY c.full_name ORDER BY spend DESC LIMIT 10
  tags: [join, topn]
- id: q011
  tier: 3
  question: "Which customers in Singapore had the highest total spend? Top 10, name and spend, excluding cancelled/returned."
  ordered: true
  golden_sql: |
    SELECT c.full_name, round(sum(oi.quantity * oi.unit_price - oi.discount), 2) AS spend
    FROM v_order_items oi
    INNER JOIN v_orders o ON oi.order_id = o.order_id
    INNER JOIN v_customers c ON o.customer_id = c.customer_id
    WHERE c.country = 'SG' AND o.status NOT IN ('cancelled','returned')
    GROUP BY c.full_name ORDER BY spend DESC LIMIT 10
  tags: [join, filter, topn]
- id: q012
  tier: 3
  question: "Revenue by channel in the last 30 days, highest first. Return channel and revenue, excluding cancelled/returned."
  ordered: true
  golden_sql: |
    SELECT o.channel, round(sum(oi.quantity * oi.unit_price - oi.discount), 2) AS revenue
    FROM v_order_items oi INNER JOIN v_orders o ON oi.order_id = o.order_id
    WHERE o.order_ts >= now() - INTERVAL 30 DAY AND o.status NOT IN ('cancelled','returned')
    GROUP BY o.channel ORDER BY revenue DESC
  tags: [join, groupby]
- id: q013
  tier: 3
  question: "How many orders were returned, by country? Return country and count, most first."
  ordered: true
  golden_sql: |
    SELECT c.country, count() AS c
    FROM v_orders o INNER JOIN v_customers c ON o.customer_id = c.customer_id
    WHERE o.status = 'returned' GROUP BY c.country ORDER BY c DESC
  tags: [join, filter]
- id: q014
  tier: 3
  question: "Average number of line items per order, rounded to 2 decimals."
  ordered: false
  golden_sql: |
    SELECT round(avg(n), 2) FROM (
      SELECT order_id, count() AS n FROM v_order_items GROUP BY order_id)
  tags: [subquery, avg]
- id: q015
  tier: 4
  question: "Daily delivered-order revenue for the last 14 days, oldest first. Return date and revenue."
  ordered: true
  golden_sql: |
    SELECT toDate(o.order_ts) AS d,
           round(sum(oi.quantity * oi.unit_price - oi.discount), 2) AS revenue
    FROM v_order_items oi INNER JOIN v_orders o ON oi.order_id = o.order_id
    WHERE o.status = 'delivered' AND o.order_ts >= now() - INTERVAL 14 DAY
    GROUP BY d ORDER BY d
  tags: [timeseries]
- id: q016
  tier: 4
  question: "7-day rolling average of daily delivered-order revenue for the last 30 days, oldest first. Return date and rolling_avg."
  ordered: true
  golden_sql: |
    WITH daily AS (
      SELECT toDate(o.order_ts) AS d, sum(oi.quantity * oi.unit_price - oi.discount) AS rev
      FROM v_order_items oi INNER JOIN v_orders o ON oi.order_id = o.order_id
      WHERE o.status = 'delivered' AND o.order_ts >= now() - INTERVAL 30 DAY
      GROUP BY d)
    SELECT d, round(avg(rev) OVER (ORDER BY d ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 2)
    FROM daily ORDER BY d
  tags: [window, timeseries]
- id: q017
  tier: 4
  question: "For each segment, the average revenue per order in the last 30 days (excluding cancelled/returned), highest first. Return segment and avg_order_revenue."
  ordered: true
  golden_sql: |
    WITH ord AS (
      SELECT o.order_id, c.segment,
             sum(oi.quantity * oi.unit_price - oi.discount) AS rev
      FROM v_order_items oi
      INNER JOIN v_orders o ON oi.order_id = o.order_id
      INNER JOIN v_customers c ON o.customer_id = c.customer_id
      WHERE o.order_ts >= now() - INTERVAL 30 DAY AND o.status NOT IN ('cancelled','returned')
      GROUP BY o.order_id, c.segment)
    SELECT segment, round(avg(rev), 2) AS avg_order_revenue
    FROM ord GROUP BY segment ORDER BY avg_order_revenue DESC
  tags: [cte, join, avg]
- id: q018
  tier: 5
  question: "View-to-purchase conversion rate in the last 7 days: of sessions with a view event, what fraction also had a purchase event? Return a single percentage rounded to 2 decimals."
  ordered: false
  golden_sql: |
    SELECT round(100 * countIf(has_purchase) / count(), 2) FROM (
      SELECT session_id, max(event_type = 'view') AS has_view,
             max(event_type = 'purchase') AS has_purchase
      FROM v_events WHERE event_ts >= now() - INTERVAL 7 DAY
      GROUP BY session_id HAVING has_view = 1)
  tags: [funnel, conversion]
- id: q019
  tier: 5
  question: "Top 5 best-selling products by total quantity sold (exclude cancelled/returned orders), highest first. Return product name and total_qty."
  ordered: true
  golden_sql: |
    SELECT p.name, sum(oi.quantity) AS total_qty
    FROM v_order_items oi
    INNER JOIN v_orders o ON oi.order_id = o.order_id
    INNER JOIN v_products p ON oi.product_id = p.product_id
    WHERE o.status NOT IN ('cancelled','returned')
    GROUP BY p.name ORDER BY total_qty DESC LIMIT 5
  tags: [join, topn]
  fewshot_holdout: true
- id: q020
  tier: 5
  question: "Gross margin in the last 30 days = sum(quantity*(unit_price-unit_cost)) over delivered orders, joining items to products for unit_cost. Return a single number rounded to 2 decimals."
  ordered: false
  golden_sql: |
    SELECT round(sum(oi.quantity * (oi.unit_price - p.unit_cost)), 2)
    FROM v_order_items oi
    INNER JOIN v_orders o ON oi.order_id = o.order_id
    INNER JOIN v_products p ON oi.product_id = p.product_id
    WHERE o.status = 'delivered' AND o.order_ts >= now() - INTERVAL 30 DAY
  tags: [margin, join]
  fewshot_holdout: true
```

- [ ] **Step 2: Write `eval/golden.py`**

```python
from dataclasses import dataclass, field
import yaml


@dataclass
class GoldenQuestion:
    id: str
    tier: int
    question: str
    ordered: bool
    golden_sql: str
    tags: list = field(default_factory=list)
    notes: str = ""
    fewshot_holdout: bool = False


def load_golden(path: str = "golden/questions.yaml") -> list[GoldenQuestion]:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return [GoldenQuestion(
        id=q["id"], tier=q["tier"], question=q["question"],
        ordered=q.get("ordered", False), golden_sql=q["golden_sql"],
        tags=q.get("tags", []), notes=q.get("notes", ""),
        fewshot_holdout=q.get("fewshot_holdout", False),
    ) for q in raw]


def fewshot_examples(questions: list[GoldenQuestion], k: int) -> list[dict]:
    held = [q for q in questions if q.fewshot_holdout][:k]
    return [{"question": q.question, "golden_sql": q.golden_sql} for q in held]
```

- [ ] **Step 3: Validate every golden SQL actually runs**

Run:
```bash
source .env && python -c "
from arena.config import load_config
from agents.chclient import ROClickHouseClient
from eval.golden import load_golden
c = ROClickHouseClient(load_config().clickhouse)
for q in load_golden():
    r = c.query(q.golden_sql)
    print(q.id, 'rows=', len(r.rows))
"
```
Expected: every question prints a row count with no exception. Fix any golden SQL that errors before continuing.

- [ ] **Step 4: Commit**

```bash
git add golden/questions.yaml eval/golden.py
git commit -m "feat: 20-question golden set across 5 tiers + loader"
```

---

## Task 11: Results table + LangFuse adapter

**Files:**
- Create: `eval/results.py`, `eval/langfuse_adapter.py`

- [ ] **Step 1: Write `eval/results.py`**

```python
from dataclasses import dataclass, asdict
from arena.config import ClickHouseCfg


@dataclass
class EvalRunRow:
    run_id: str
    config_id: str
    model_name: str
    prompt_name: str
    question_id: str
    tier: int
    correctness: int
    cost_usd: float
    latency_ms: int
    retries: int
    outcome: str
    sql: str
    tags: str
    data_snapshot_ts: str  # 'YYYY-MM-DD HH:MM:SS'


_DDL = """
CREATE TABLE IF NOT EXISTS {db}.eval_runs (
  run_id String, config_id String, model_name String, prompt_name String,
  question_id String, tier UInt8, correctness UInt8, cost_usd Float64,
  latency_ms UInt32, retries UInt8, outcome String, sql String, tags String,
  data_snapshot_ts DateTime, inserted_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY (run_id, config_id, question_id)
"""

_LEADERBOARD = """
CREATE VIEW IF NOT EXISTS {db}.v_leaderboard AS
SELECT run_id, config_id, model_name, prompt_name,
       count() AS n_questions,
       round(avg(correctness), 4) AS accuracy,
       sum(correctness) AS n_correct,
       round(sum(cost_usd), 6) AS total_cost_usd,
       round(avg(cost_usd), 6) AS avg_cost_usd,
       round(avg(latency_ms), 1) AS avg_latency_ms,
       round(sum(cost_usd) / nullIf(sum(correctness), 0), 6) AS cost_per_correct_answer
FROM (SELECT * FROM {db}.eval_runs FINAL)
GROUP BY run_id, config_id, model_name, prompt_name
"""


def ensure_results_tables(admin, db: str) -> None:
    admin.command(_DDL.format(db=db))
    admin.command(_LEADERBOARD.format(db=db))


def write_eval_run(admin, db: str, row: EvalRunRow) -> None:
    d = asdict(row)
    cols = list(d.keys())
    admin.insert(f"{db}.eval_runs", [[d[c] for c in cols]], column_names=cols)
```

- [ ] **Step 2: Write `eval/langfuse_adapter.py`** (version-isolated; before writing, invoke the `langfuse` skill to confirm the installed SDK's trace/score method names, then implement against them. The code below targets langfuse 2.x.)

```python
"""All LangFuse SDK calls live here so a version bump touches one file.
Targets langfuse 2.x (Langfuse client + trace/span/score). If the installed
SDK differs, adjust ONLY this module."""
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
```

- [ ] **Step 3: Commit**

```bash
git add eval/results.py eval/langfuse_adapter.py
git commit -m "feat: eval_runs results table + leaderboard view + langfuse adapter"
```

---

## Task 12: Benchmark harness (grid runner)

**Files:**
- Create: `eval/harness.py`

- [ ] **Step 1: Write `eval/harness.py`**

```python
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
```

- [ ] **Step 2: Smoke-run on 3 questions, one config** (requires AWS Bedrock creds)

Run: `source .env && python -m eval.harness --limit 3`
Expected: per-question lines printing `score=…`, then a `done.` line. If Bedrock creds are missing it will error on the first `converse` — flag to user.

- [ ] **Step 3: Verify rows + leaderboard**

Run:
```bash
source .env && python -c "
from arena.config import load_config; from agents.chclient import make_admin_client
db=load_config().clickhouse.database; a=make_admin_client(load_config().clickhouse)
print(a.query(f'SELECT config_id, accuracy, cost_per_correct_answer, avg_latency_ms FROM {db}.v_leaderboard').result_rows)
"
```
Expected: at least one leaderboard row with accuracy in [0,1].

- [ ] **Step 4: Commit**

```bash
git add eval/harness.py
git commit -m "feat: benchmark grid runner with golden snapshot + langfuse mirror"
```

---

## Task 13: Leaderboard dashboard

**Files:**
- Create: `dashboard/app.py`, `dashboard/static/index.html`

- [ ] **Step 1: Write `dashboard/app.py`**

```python
"""Leaderboard dashboard. Reads ONLY from ClickHouse (single source of truth).
Usage: source .env && uvicorn dashboard.app:app --reload --port 8000
"""
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from arena.config import load_config
from agents.chclient import make_admin_client

app = FastAPI(title="ChatBI Arena Leaderboard")
_cfg = load_config()
_db = _cfg.clickhouse.database
_admin = make_admin_client(_cfg.clickhouse)


def _rows_to_dicts(res):
    return [dict(zip(res.column_names, r)) for r in res.result_rows]


@app.get("/api/runs")
def runs():
    res = _admin.query(
        f"SELECT DISTINCT run_id FROM {_db}.eval_runs ORDER BY run_id DESC")
    return [r[0] for r in res.result_rows]


@app.get("/api/leaderboard")
def leaderboard(run_id: str | None = None):
    where = f"WHERE run_id = '{run_id}'" if run_id else ""
    res = _admin.query(
        f"SELECT config_id, model_name, prompt_name, n_questions, accuracy, "
        f"n_correct, total_cost_usd, avg_latency_ms, cost_per_correct_answer "
        f"FROM {_db}.v_leaderboard {where} ORDER BY accuracy DESC, cost_per_correct_answer ASC")
    return _rows_to_dicts(res)


@app.get("/api/tiers")
def tiers(run_id: str | None = None):
    where = f"WHERE run_id = '{run_id}'" if run_id else ""
    res = _admin.query(
        f"SELECT config_id, tier, round(avg(correctness),3) AS accuracy "
        f"FROM (SELECT * FROM {_db}.eval_runs FINAL) {where} "
        f"GROUP BY config_id, tier ORDER BY config_id, tier")
    return _rows_to_dicts(res)


@app.get("/api/outcomes")
def outcomes(run_id: str | None = None):
    where = f"WHERE run_id = '{run_id}'" if run_id else ""
    res = _admin.query(
        f"SELECT config_id, outcome, count() AS n "
        f"FROM (SELECT * FROM {_db}.eval_runs FINAL) {where} "
        f"GROUP BY config_id, outcome ORDER BY config_id")
    return _rows_to_dicts(res)


@app.get("/")
def index():
    return FileResponse("dashboard/static/index.html")
```

- [ ] **Step 2: Write `dashboard/static/index.html`** (minimal sortable leaderboard; no build step)

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><title>ChatBI Arena — Leaderboard</title>
<style>
 body{font:14px system-ui,sans-serif;margin:2rem;color:#111;background:#fafafa}
 h1{font-size:1.4rem} table{border-collapse:collapse;width:100%;margin-top:1rem;background:#fff}
 th,td{border:1px solid #e3e3e3;padding:.5rem .7rem;text-align:right}
 th:first-child,td:first-child{text-align:left} th{background:#f0f0f3;cursor:pointer}
 tr:hover{background:#f7f9ff} .win{background:#e8f7ec}
 select{padding:.3rem}
</style></head>
<body>
<h1>ChatBI Arena — Leaderboard</h1>
<label>Run: <select id="run"></select></label>
<table id="lb"><thead><tr>
 <th>config</th><th>questions</th><th>accuracy</th><th>correct</th>
 <th>total $</th><th>avg latency ms</th><th>cost / correct $</th>
</tr></thead><tbody></tbody></table>
<script>
async function j(u){return (await fetch(u)).json()}
function fmt(v){return typeof v==='number'?(Number.isInteger(v)?v:v.toFixed(4)):v}
async function load(run){
  const rows = await j('/api/leaderboard'+(run?`?run_id=${run}`:''));
  const tb = document.querySelector('#lb tbody'); tb.innerHTML='';
  let best = Math.max(...rows.map(r=>r.accuracy), 0);
  for(const r of rows){
    const tr=document.createElement('tr');
    if(r.accuracy===best) tr.className='win';
    tr.innerHTML=`<td>${r.config_id}</td><td>${r.n_questions}</td>
      <td>${(r.accuracy*100).toFixed(1)}%</td><td>${r.n_correct}</td>
      <td>${fmt(r.total_cost_usd)}</td><td>${fmt(r.avg_latency_ms)}</td>
      <td>${r.cost_per_correct_answer==null?'—':fmt(r.cost_per_correct_answer)}</td>`;
    tb.appendChild(tr);
  }
}
(async()=>{
  const runs = await j('/api/runs'); const sel=document.querySelector('#run');
  sel.innerHTML = runs.map(r=>`<option>${r}</option>`).join('');
  sel.onchange = ()=>load(sel.value);
  load(runs[0]);
})();
</script>
</body></html>
```

- [ ] **Step 3: Run the dashboard and verify**

Run: `source .env && uvicorn dashboard.app:app --port 8000` (in a background shell)
Then: `curl -s localhost:8000/api/leaderboard | head -c 400`
Expected: a JSON array of leaderboard rows. Open http://localhost:8000 to see the table with the top-accuracy row highlighted.

- [ ] **Step 4: Commit**

```bash
git add dashboard/app.py dashboard/static/index.html
git commit -m "feat: leaderboard dashboard reading from ClickHouse"
```

---

## Task 14: End-to-end acceptance + README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Full pipeline dry run**

```bash
source .env
python scripts/check_connectivity.py        # CH + LangFuse ok; Bedrock ok or flagged
python scripts/setup_clickhouse.py           # db + RO user
python -m datagen.generator --seed 42        # seed ~90d
python schema/gen_schema_context.py          # views + schema_context.md
pytest -q                                    # all unit tests green
python -m eval.harness                        # full grid (1 model x 2 prompts x 20 Qs)
```
Expected: harness prints per-question scores and a `done.` line; `pytest` all green.

- [ ] **Step 2: Acceptance checks (spec §8 M1–M3)**
  - Read-only user blocks a destructive statement:
    ```bash
    python -c "from arena.config import load_config; from agents.chclient import ROClickHouseClient; \
    c=ROClickHouseClient(load_config().clickhouse); \
    import sys; \
    \nfrom contextlib import suppress; \
    \nok=False; \
    \ntry:\n c.query('DROP TABLE arena_house.orders')\nexcept Exception as e:\n ok=True; print('blocked:', type(e).__name__)\nassert ok"
    ```
    Expected: prints `blocked: ...` (DROP rejected by readonly user).
  - Leaderboard exposes `cost_per_correct_answer` and per-tier accuracy via `/api/leaderboard` and `/api/tiers`.
  - A LangFuse trace per run is visible in the LangFuse Cloud UI with correctness/cost/latency scores.

- [ ] **Step 3: Write `README.md`** (quickstart: env, setup, seed, run, dashboard; note AWS creds required for the harness; link the spec and this plan).

```markdown
# ChatBI Arena (POC — Measurement Core)

NL→SQL agent benchmark over ClickHouse Cloud (AWS), graded by execution accuracy,
tracked in LangFuse, leaderboard rendered from ClickHouse.

## Quickstart
1. `python3.11 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
2. Ensure `.env` has CLICKHOUSE_CLOUD_*, LANGFUSE_*, ARENA_RO_PASSWORD, and AWS creds for ap-southeast-1 Bedrock.
3. `source .env`
4. `python scripts/check_connectivity.py`
5. `python scripts/setup_clickhouse.py`
6. `python -m datagen.generator --seed 42`
7. `python schema/gen_schema_context.py`
8. `pytest -q`
9. `python -m eval.harness`
10. `uvicorn dashboard.app:app --port 8000` → http://localhost:8000

Spec: `docs/superpowers/specs/2026-06-06-chatbi-arena-poc-design.md`
Plan: `docs/superpowers/plans/2026-06-06-chatbi-arena-measurement-core.md`

Follow-on plans: M4 full grid · M5 Aurora+ClickPipes CDC · M6 ClickStack/OTel · M7 serving /ask API.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README quickstart + end-to-end acceptance"
```

---

## Self-review notes

- **Spec coverage (M0–M3):** M0 connectivity (Task 4 check + Task 2 setup); M1 measurement core — seed (Task 5), views + schema_context (Task 6), agent loop + Bedrock + RO sandbox + SQL guard (Tasks 2,3,4,7,8); M2 grading TDD + golden set (Tasks 9,10); M3 results table + leaderboard view + LangFuse + dashboard (Tasks 11,12,13). Read-only-blocks-destructive acceptance is Task 14 Step 2.
- **Deferred to follow-on plans (intentional):** M4 full grid fan-out (config already supports it — add models/prompts and rerun), M5 Aurora+ClickPipes CDC (the `v_*` repoint), M6 ClickStack/OTel, M7 serving `/ask`.
- **Type consistency:** `AgentResult(sql, rows, cols, error, attempts, usage, outcome_hint)` is produced in Task 8 and consumed unchanged in Tasks 9 (grading) and 12 (harness). `QueryResult(rows, cols)` from `ROClickHouseClient.query` (Task 2) matches loop/harness usage. `EvalRunRow` fields (Task 11) match `write_eval_run` and the `eval_runs` DDL columns.
- **External-dependency risks flagged in-plan:** AWS Bedrock creds/model access (Task 4 gate, Task 12 smoke); LangFuse SDK version (Task 11 isolates + use the langfuse skill); ClickHouse RO user semantics (Task 2 + Task 14 destructive-statement check).
```
