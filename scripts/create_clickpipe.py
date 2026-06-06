"""Create the Aurora->ClickHouse ClickPipes Postgres CDC pipe via the
ClickHouse Cloud REST API (no console). The CLI-native way to provision CDC.

Env (from .env): CH_CLOUD_KEY_ID, CH_CLOUD_KEY_SECRET. Aurora connection from
AURORA_DSN (or the individual --pg-* flags). Verify ClickPipes egress IPs are
allowlisted in the Aurora security group first (see infra/README_clickpipes.md).

  source .env && AURORA_DSN=... python scripts/create_clickpipe.py
"""
import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error

API = "https://api.clickhouse.cloud/v1"
ORG = "feb9fd36-1a3b-415d-b78d-f6b7ef354deb"
SVC = "b96dfec2-44a8-48f7-998f-036a0d16a871"  # arena-house, aws ap-southeast-1
TABLES = ["customers", "products", "orders", "order_items", "events"]


def _req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    key_id = os.environ["CH_CLOUD_KEY_ID"]
    secret = os.environ["CH_CLOUD_KEY_SECRET"]
    import base64
    auth = base64.b64encode(f"{key_id}:{secret}".encode()).decode()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                 headers={"Authorization": f"Basic {auth}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def parse_dsn(dsn: str) -> dict:
    m = re.match(r"postgresql://([^:]+):([^@]+)@([^:/]+):(\d+)/(\w+)", dsn)
    if not m:
        sys.exit("could not parse AURORA_DSN")
    u, p, h, port, db = m.groups()
    return {"host": h, "port": int(port), "database": db, "user": u, "password": p}


def build_body(pg: dict, pipe_name: str, dest_db: str) -> dict:
    return {
        "name": pipe_name,
        "source": {
            "postgres": {
                "host": pg["host"],
                "port": pg["port"],
                "database": pg["database"],
                "credentials": {"username": pg["user"], "password": pg["password"]},
                "settings": {"publicationName": "arena_pub", "replicationMode": "cdc"},
                "tableMappings": [
                    {"sourceSchemaName": "public", "sourceTable": t,
                     "targetTable": t} for t in TABLES
                ],
            }
        },
        "destination": {"database": dest_db},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="arena-cdc")
    ap.add_argument("--dest-db", default="arena_cdc")
    ap.add_argument("--dry-run", action="store_true", help="print body, don't POST")
    args = ap.parse_args()

    dsn = os.environ.get("AURORA_DSN")
    if not dsn:
        sys.exit("set AURORA_DSN (terraform output -raw aurora_dsn)")
    pg = parse_dsn(dsn)
    body = build_body(pg, args.name, args.dest_db)

    if args.dry_run:
        print(json.dumps(body, indent=2))
        return

    status, resp = _req("POST", f"/organizations/{ORG}/services/{SVC}/clickpipes", body)
    print("HTTP", status)
    print(json.dumps(resp, indent=2)[:1500])


if __name__ == "__main__":
    main()
