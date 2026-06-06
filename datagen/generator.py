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

    pop_weights = [1.0 / (i + 1) for i in range(n_products)]
    for pid in range(1, n_products + 1):
        price = round(rng.uniform(5, 800), 2)
        cost = round(price * rng.uniform(0.4, 0.8), 2)
        ts = start
        products.append([pid, f"{fake.color_name()} {fake.word().title()}",
                         rng.choice(CATEGORIES), fake.company(), price, cost,
                         ts, ts, ver, 0])

    oid = oiid = eid = 0
    n_orders = n_customers * 6
    for _ in range(n_orders):
        oid += 1
        cust = rng.randint(1, n_customers)
        day_offset = rng.randint(0, days - 1)
        hour = int(min(23, max(0, rng.gauss(15, 4))))
        ots = start + timedelta(days=day_offset, hours=hour,
                                minutes=rng.randint(0, 59))
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
        for _ in range(rng.randint(1, 4)):
            oiid += 1
            pid = rng.choices(range(1, n_products + 1), weights=pop_weights)[0]
            p = products[pid - 1]
            qty = rng.randint(1, 5)
            disc = round(rng.choice([0, 0, 0, 5, 10]) * 1.0, 2)
            items.append([oiid, oid, pid, qty, p[4], disc, ots, ots, ver, 0])
        sess = f"s-{oid}-{rng.randint(1000, 9999)}"
        for et in ["view", "add_to_cart", "checkout", "purchase"]:
            if et == "purchase" and status == "cancelled":
                continue
            eid += 1
            ets = ots - timedelta(minutes=rng.randint(1, 120))
            pid_ev = rng.randint(1, n_products)
            events.append([eid, cust, sess, et, pid_ev, ets, ets, ver, 0])
        if rng.random() < 0.4:
            eid += 1
            sess2 = f"a-{oid}-{rng.randint(1000, 9999)}"
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
