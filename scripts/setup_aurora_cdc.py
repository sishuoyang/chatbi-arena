"""Create the least-privilege CDC role + publication in Aurora (idempotent).
Reads AURORA_DSN (master) and ARENA_CDC_PASSWORD (default below).

  source .env && AURORA_DSN=... python scripts/setup_aurora_cdc.py
"""
import os
import psycopg2

CDC_USER = "arena_cdc"
CDC_PW = os.environ.get("ARENA_CDC_PASSWORD", "Arena_cdc_2026_demo")
PUBLICATION = "arena_pub"


def main() -> None:
    dsn = os.environ.get("AURORA_DSN")
    if not dsn:
        raise SystemExit("set AURORA_DSN (terraform output -raw aurora_dsn)")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (CDC_USER,))
    if not cur.fetchone():
        cur.execute(f"CREATE ROLE {CDC_USER} WITH LOGIN PASSWORD '{CDC_PW}'")
    cur.execute(f"GRANT rds_replication TO {CDC_USER}")
    cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {CDC_USER}")
    cur.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {CDC_USER}")
    cur.execute("SELECT 1 FROM pg_publication WHERE pubname=%s", (PUBLICATION,))
    if not cur.fetchone():
        cur.execute(f"CREATE PUBLICATION {PUBLICATION} FOR ALL TABLES")
    cur.execute("SHOW rds.logical_replication")
    print(f"OK: role {CDC_USER}, publication {PUBLICATION}, "
          f"logical_replication={cur.fetchone()[0]}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
