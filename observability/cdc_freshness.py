"""CDC freshness / replication-lag signal for ClickStack.

Measures how stale the analytic store is vs. wall clock — `now() - max(order_ts)`
over the v_orders view — and emits it as an OTel gauge `arena.cdc_freshness_seconds`.
A 'correct' answer over stale data is still operationally wrong, so this panel
sits next to the correctness leaderboard.

  source .env && python -m observability.cdc_freshness            # one sample
  source .env && python -m observability.cdc_freshness --watch 15 # every 15s
"""
import argparse
import time
from arena.config import load_config
from agents.chclient import make_admin_client
from observability.instrumentation import init_telemetry


def measure(admin, db: str) -> float:
    row = admin.query(
        f"SELECT dateDiff('second', max(order_ts), now()) FROM {db}.v_orders").result_rows
    return float(row[0][0]) if row and row[0][0] is not None else -1.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0, help="poll interval seconds (0 = once)")
    args = ap.parse_args()

    cfg = load_config()
    db = cfg.clickhouse.database
    admin = make_admin_client(cfg.clickhouse)
    _, meter = init_telemetry("arena-cdc")

    latest = {"v": 0.0}
    meter.create_observable_gauge(
        "arena.cdc_freshness_seconds", unit="s",
        callbacks=[lambda opts: [_obs(latest["v"])]])

    while True:
        latest["v"] = measure(admin, db)
        print(f"cdc_freshness_seconds = {latest['v']:.0f}")
        if not args.watch:
            time.sleep(6)  # let the periodic reader flush one export
            break
        time.sleep(args.watch)


def _obs(value):
    from opentelemetry.metrics import Observation
    return Observation(value)


if __name__ == "__main__":
    main()
