-- Aurora PostgreSQL OLTP source schema (design §5). Run against the Aurora
-- cluster after `terraform apply`. ClickPipes replicates these tables to
-- ClickHouse; do NOT hand-create the ClickHouse target tables.

CREATE TABLE IF NOT EXISTS customers (
  customer_id  BIGINT PRIMARY KEY,
  full_name    TEXT NOT NULL,
  email        TEXT NOT NULL,
  country      TEXT NOT NULL,
  segment      TEXT NOT NULL,
  signup_date  DATE NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS products (
  product_id  BIGINT PRIMARY KEY,
  name        TEXT NOT NULL,
  category    TEXT NOT NULL,
  brand       TEXT NOT NULL,
  unit_price  NUMERIC(10,2) NOT NULL,
  unit_cost   NUMERIC(10,2) NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orders (
  order_id     BIGINT PRIMARY KEY,
  customer_id  BIGINT NOT NULL REFERENCES customers(customer_id),
  order_ts     TIMESTAMPTZ NOT NULL,
  status       TEXT NOT NULL,
  channel      TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS order_items (
  order_item_id BIGINT PRIMARY KEY,
  order_id      BIGINT NOT NULL REFERENCES orders(order_id),
  product_id    BIGINT NOT NULL REFERENCES products(product_id),
  quantity      INT NOT NULL,
  unit_price    NUMERIC(10,2) NOT NULL,
  discount      NUMERIC(10,2) NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
  event_id     BIGINT PRIMARY KEY,
  customer_id  BIGINT,
  session_id   TEXT NOT NULL,
  event_type   TEXT NOT NULL,
  product_id   BIGINT,
  event_ts     TIMESTAMPTZ NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- CDC prerequisites: a replication-capable role + a publication.
-- (Aurora grants rds_replication; ClickPipes can also create the publication
-- itself if the connecting role is allowed to.)
-- CREATE ROLE arena_cdc WITH LOGIN PASSWORD '<pw>';
-- GRANT rds_replication TO arena_cdc;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO arena_cdc;
-- CREATE PUBLICATION arena_pub FOR ALL TABLES;
