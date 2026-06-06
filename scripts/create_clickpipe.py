"""Manage the Aurora->ClickHouse ClickPipes Postgres CDC pipe via the ClickHouse
Cloud REST API (no console). The CLI-native way to provision/inspect/destroy CDC.

Actions:
  create   idempotent: create the pipe if one named --name doesn't exist
  wait     poll until the pipe reports Running
  status   print the pipe's current state
  delete   delete the pipe named --name (for teardown)

Env (from .env): CH_CLOUD_KEY_ID, CH_CLOUD_KEY_SECRET. For create: AURORA_DSN
(host/port/db) + ARENA_CDC_PASSWORD (the arena_cdc role's password). Ensure the
ClickPipes egress IPs are allowlisted in Aurora's SG first.

  source .env && AURORA_DSN=... python scripts/create_clickpipe.py --action create
  python scripts/create_clickpipe.py --action wait
  python scripts/create_clickpipe.py --action delete
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

API = "https://api.clickhouse.cloud/v1"
ORG = "feb9fd36-1a3b-415d-b78d-f6b7ef354deb"
SVC = "b96dfec2-44a8-48f7-998f-036a0d16a871"  # arena-house, aws ap-southeast-1
TABLES = ["customers", "products", "orders", "order_items", "events"]
CDC_USER = "arena_cdc"


def _req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    auth = base64.b64encode(
        f"{os.environ['CH_CLOUD_KEY_ID']}:{os.environ['CH_CLOUD_KEY_SECRET']}".encode()
    ).decode()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                 headers={"Authorization": f"Basic {auth}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _base() -> str:
    return f"/organizations/{ORG}/services/{SVC}/clickpipes"


def find_pipe(name: str) -> dict | None:
    _, resp = _req("GET", _base())
    for p in resp.get("result", []):
        if p.get("name") == name:
            return p
    return None


def parse_dsn(dsn: str) -> dict:
    m = re.match(r"postgresql://([^:]+):([^@]+)@([^:/]+):(\d+)/(\w+)", dsn)
    if not m:
        sys.exit("could not parse AURORA_DSN")
    _, _, h, port, db = m.groups()
    return {"host": h, "port": int(port), "database": db}


def build_body(pg: dict, name: str, dest_db: str) -> dict:
    return {
        "name": name,
        "source": {
            "postgres": {
                "host": pg["host"], "port": pg["port"], "database": pg["database"],
                "credentials": {"username": CDC_USER,
                                "password": os.environ.get("ARENA_CDC_PASSWORD",
                                                           "Arena_cdc_2026_demo")},
                "settings": {"publicationName": "arena_pub", "replicationMode": "cdc"},
                "tableMappings": [
                    {"sourceSchemaName": "public", "sourceTable": t, "targetTable": t}
                    for t in TABLES
                ],
            }
        },
        "destination": {"database": dest_db},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", choices=["create", "wait", "status", "delete"],
                    default="create")
    ap.add_argument("--name", default="arena-cdc")
    ap.add_argument("--dest-db", default="arena_cdc")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    if args.action == "create":
        existing = find_pipe(args.name)
        if existing:
            print(f"pipe '{args.name}' already exists: id={existing['id']} "
                  f"state={existing['state']}")
            return
        pg = parse_dsn(os.environ.get("AURORA_DSN") or sys.exit("set AURORA_DSN"))
        status, resp = _req("POST", _base(), build_body(pg, args.name, args.dest_db))
        if status >= 300:
            sys.exit(f"create failed HTTP {status}: {json.dumps(resp)}")
        print(f"created pipe '{args.name}': id={resp['result']['id']} "
              f"state={resp['result']['state']}")
        return

    pipe = find_pipe(args.name)
    if not pipe:
        print(f"no pipe named '{args.name}'")
        return

    if args.action == "status":
        _, resp = _req("GET", f"{_base()}/{pipe['id']}")
        print(f"pipe '{args.name}' id={pipe['id']} state={resp['result']['state']}")
    elif args.action == "delete":
        status, resp = _req("DELETE", f"{_base()}/{pipe['id']}")
        print(f"delete '{args.name}' (id={pipe['id']}) -> HTTP {status}")
    elif args.action == "wait":
        deadline = args.timeout
        while deadline > 0:
            _, resp = _req("GET", f"{_base()}/{pipe['id']}")
            state = resp["result"]["state"]
            print(f"pipe state: {state}")
            if state == "Running":
                return
            time.sleep(15)
            deadline -= 15
        sys.exit(f"pipe did not reach Running within {args.timeout}s")


if __name__ == "__main__":
    main()
