# Schema context (agent-facing)

Query ONLY these views. ClickHouse SQL dialect. Revenue = quantity*unit_price - discount.


## v_customers
- `customer_id` Int64
- `full_name` String
- `email` String
- `country` String — ISO-ish: SG|VN|TH|ID|AU|IN|TW|JP
- `segment` String — consumer|smb|enterprise
- `signup_date` Date32
- `created_at` DateTime64(6)
- `updated_at` DateTime64(6)

## v_products
- `product_id` Int64
- `name` String
- `category` String — electronics|home|apparel|grocery|beauty
- `brand` String
- `unit_price` Decimal(10, 2)
- `unit_cost` Decimal(10, 2)
- `created_at` DateTime64(6)
- `updated_at` DateTime64(6)

## v_orders
- `order_id` Int64
- `customer_id` Int64
- `order_ts` DateTime64(6)
- `status` String — one of placed|paid|shipped|delivered|cancelled|returned
- `channel` String — one of web|ios|android|partner
- `created_at` DateTime64(6)
- `updated_at` DateTime64(6)

## v_order_items
- `order_item_id` Int64
- `order_id` Int64
- `product_id` Int64
- `quantity` Int32
- `unit_price` Decimal(10, 2) — price at time of sale
- `discount` Decimal(10, 2)
- `created_at` DateTime64(6)
- `updated_at` DateTime64(6)

## v_events
- `event_id` Int64
- `customer_id` Int64
- `session_id` String
- `event_type` String — view|search|add_to_cart|checkout|purchase
- `product_id` Int64
- `event_ts` DateTime64(6)
- `created_at` DateTime64(6)
