"""Repoint the v_* views from the phase-1 seed tables to the ClickPipes
CDC-landed tables, AUTO-DETECTING the PeerDB bookkeeping columns so we don't
depend on assumed names (design risk #2).

Run AFTER the ClickPipes pipe has created and backfilled its target tables.
Point ClickPipes at a dedicated database (e.g. arena_cdc) to avoid clobbering
the seed tables, then:

  source .env && python schema/repoint_views.py --source-db arena_cdc

The views keep living in the arena (query) database; only their source swaps.
Agents and golden SQL are unaffected — they only ever see v_*.
"""
import argparse
from arena.config import load_config
from agents.chclient import make_admin_client

TABLES = ["customers", "products", "orders", "order_items", "events"]


def detect_peerdb_columns(admin, db: str, table: str) -> tuple[list[str], str]:
    """Return (bookkeeping_columns_to_drop, soft_delete_column)."""
    rows = admin.query(
        "SELECT name FROM system.columns "
        f"WHERE database = '{db}' AND table = '{table}'").result_rows
    names = [r[0] for r in rows]
    if not names:
        raise SystemExit(f"table {db}.{table} not found — has ClickPipes landed it yet?")
    bookkeeping = [n for n in names if n.startswith("_peerdb")]
    soft_delete = next((n for n in bookkeeping if "deleted" in n.lower()), None)
    if not soft_delete:
        raise SystemExit(f"no *deleted* column among {bookkeeping} in {db}.{table}; "
                         "inspect the landed schema and adjust this script.")
    return bookkeeping, soft_delete


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-db", required=True,
                    help="database ClickPipes landed the CDC tables into")
    args = ap.parse_args()

    cfg = load_config()
    view_db = cfg.clickhouse.database
    admin = make_admin_client(cfg.clickhouse)

    for t in TABLES:
        drop_cols, soft_delete = detect_peerdb_columns(admin, args.source_db, t)
        except_list = ", ".join(drop_cols)
        ddl = (f"CREATE OR REPLACE VIEW {view_db}.v_{t} AS "
               f"SELECT * EXCEPT ({except_list}) "
               f"FROM {args.source_db}.{t} FINAL WHERE {soft_delete} = 0")
        admin.command(ddl)
        n = admin.query(f"SELECT count() FROM {view_db}.v_{t}").result_rows[0][0]
        print(f"v_{t} -> {args.source_db}.{t} (dropped {except_list}; "
              f"filter {soft_delete}=0); rows={n}")
    print("repoint complete — agents now read CDC-backed views")


if __name__ == "__main__":
    main()
