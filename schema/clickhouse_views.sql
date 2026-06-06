CREATE VIEW IF NOT EXISTS arena_house.v_customers AS
  SELECT * EXCEPT (_peerdb_version, _peerdb_is_deleted) FROM arena_house.customers FINAL
  WHERE _peerdb_is_deleted = 0;
CREATE VIEW IF NOT EXISTS arena_house.v_products AS
  SELECT * EXCEPT (_peerdb_version, _peerdb_is_deleted) FROM arena_house.products FINAL
  WHERE _peerdb_is_deleted = 0;
CREATE VIEW IF NOT EXISTS arena_house.v_orders AS
  SELECT * EXCEPT (_peerdb_version, _peerdb_is_deleted) FROM arena_house.orders FINAL
  WHERE _peerdb_is_deleted = 0;
CREATE VIEW IF NOT EXISTS arena_house.v_order_items AS
  SELECT * EXCEPT (_peerdb_version, _peerdb_is_deleted) FROM arena_house.order_items FINAL
  WHERE _peerdb_is_deleted = 0;
CREATE VIEW IF NOT EXISTS arena_house.v_events AS
  SELECT * EXCEPT (_peerdb_version, _peerdb_is_deleted) FROM arena_house.events FINAL
  WHERE _peerdb_is_deleted = 0;
