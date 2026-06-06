"""Create the arena database and a least-privilege read-only user.
Run once after credentials are in place. Idempotent.
Usage: source .env && python scripts/setup_clickhouse.py
"""
from arena.config import load_config
from agents.chclient import make_admin_client


def main() -> None:
    cfg = load_config()
    ch = cfg.clickhouse
    # Bootstrap via the always-present 'default' database (arena db may not exist yet).
    admin = make_admin_client(ch, database="default")

    admin.command(f"CREATE DATABASE IF NOT EXISTS {ch.database}")

    admin.command(
        f"CREATE USER IF NOT EXISTS {ch.ro_user} "
        f"IDENTIFIED WITH sha256_password BY '{ch.ro_password}'"
    )
    # Enforce resource limits + read-only as the user's own server-side settings.
    # readonly=1 means the user cannot run non-SELECT statements AND cannot change
    # any of these settings at query time, so the limits are non-negotiable.
    lim = ch.query_limits
    admin.command(
        f"ALTER USER {ch.ro_user} SETTINGS "
        f"max_execution_time = {lim.max_execution_time}, "
        f"max_result_rows = {lim.max_result_rows}, "
        f"max_memory_usage = {lim.max_memory_usage}, "
        f"max_rows_to_read = {lim.max_rows_to_read}, "
        f"readonly = 1"
    )
    # Least privilege: SELECT only on the arena database (tighten to v_* later).
    admin.command(f"GRANT SELECT ON {ch.database}.* TO {ch.ro_user}")
    print(f"OK: database {ch.database} and user {ch.ro_user} ready.")


if __name__ == "__main__":
    main()
