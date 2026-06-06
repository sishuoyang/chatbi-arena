# M5 Runbook — Aurora → ClickPipes CDC → ClickHouse

Brings the e-commerce data in through the **live AWS pipeline** instead of the
direct ClickHouse seed. The `v_*` views are the stable contract; only their
underlying source changes, so the agents/golden SQL/leaderboard are untouched.

## Cost & prerequisites (read first)
- `terraform apply` creates a **billable** Aurora Serverless v2 cluster
  (~0.5–2 ACU). Run `terraform destroy` when done.
- Creating the **ClickPipes pipe** programmatically needs a **ClickHouse Cloud
  organization API key** (key id + secret). We don't have one in `.env`, so the
  pipe is created via the console below (or via the optional Terraform snippet
  once you have a key). Everything else is automated.
- Use the `sa` AWS profile (account 959934561610, ap-southeast-1).

## 1. Provision Aurora (Terraform)
```bash
cd infra/terraform
export AWS_PROFILE=sa
terraform init
# admin_ingress_cidr = your public IP /32 (for data-gen). Add ClickPipes egress
# IPs to clickpipes_ingress_cidrs (step 3) or re-apply after you look them up.
terraform apply -var "admin_ingress_cidr=$(curl -s ifconfig.me)/32"
export AURORA_DSN="$(terraform output -raw aurora_dsn)"
cd ../..
```
The cluster parameter group sets `rds.logical_replication=1`. If the cluster was
already running, reboot the instance once so the static parameter takes effect.

## 2. Seed Aurora + create the CDC role/publication
```bash
source .env
python -m datagen.generator --target aurora --seed 42        # ~90d of history
# then, in psql against $AURORA_DSN, create the replication identity:
#   CREATE ROLE arena_cdc WITH LOGIN PASSWORD '<pw>';
#   GRANT rds_replication TO arena_cdc;
#   GRANT SELECT ON ALL TABLES IN SCHEMA public TO arena_cdc;
#   CREATE PUBLICATION arena_pub FOR ALL TABLES;
# (ClickPipes can also create the publication itself if the role may.)
```

## 3. Create the ClickPipes Postgres pipe (console)
ClickHouse Cloud → **Data sources → ClickPipes → Postgres CDC**:
- **Host/port/db**: from `terraform output` (writer endpoint, 5432, `arena`).
- **User/password**: `arena_cdc`.
- **Tables**: `customers, products, orders, order_items, events`.
- **Target database**: choose **`arena_cdc`** (NOT `arena_house`) so the landed
  tables don't clobber the phase-1 seed tables.
- The wizard shows ClickPipes' **static egress IPs** — copy them into
  `clickpipes_ingress_cidrs` in Terraform and `terraform apply` again so Aurora's
  security group lets the pipe connect.

Wait for the initial load to finish; the pipe then streams inserts + updates.

> Optional Terraform (needs an org API key): add the ClickHouse provider and a
> `clickhouse_clickpipe` resource, supplying `CLICKHOUSE_API_KEY_ID` /
> `CLICKHOUSE_API_KEY_SECRET` and the service/organization ids. The resource
> schema is version-sensitive — pin the provider and verify against its docs.

## 4. Repoint the views at the CDC tables
```bash
source .env
python schema/repoint_views.py --source-db arena_cdc
```
This auto-detects the real `_peerdb*` bookkeeping columns (don't assume their
names) and rebuilds each `v_*` to `SELECT * EXCEPT(<those>) FROM arena_cdc.<t>
FINAL WHERE <soft_delete>=0`.

## 5. Verify
```bash
# counts track Aurora after replication lag; no duplicate keys via FINAL views
python -c "from arena.config import load_config; from agents.chclient import ROClickHouseClient; \
c=ROClickHouseClient(load_config().clickhouse); print('orders via view:', c.query('SELECT count() FROM v_orders').rows)"
# exercise a mutation and watch it propagate (status transitions = CDC UPDATEs)
AURORA_DSN=$AURORA_DSN python -m datagen.generator --target aurora --mutate 500
```
Re-run `python -m eval.harness` — identical interface, now over CDC-backed data.

## 6. Teardown
```bash
cd infra/terraform && terraform destroy -var "admin_ingress_cidr=0.0.0.0/32"
```
Delete the ClickPipes pipe in the console; optionally `DROP DATABASE arena_cdc`.
