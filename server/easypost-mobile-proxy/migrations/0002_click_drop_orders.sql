-- Click & Drop order sync. The desktop pushes order summaries here after each
-- purchase or void so the mobile companion can display them alongside EasyPost
-- shipments. The Click & Drop API has no list-orders endpoint, so D1 is the
-- only way the mobile app can see them.
--
-- owner_hash links each order to the EasyPost account that created it, the same
-- join key used by the devices table — so a phone paired to that account sees
-- exactly that account's Click & Drop orders.

CREATE TABLE IF NOT EXISTS click_drop_orders (
  order_identifier INTEGER NOT NULL,
  owner_hash       TEXT    NOT NULL,
  tracking_number  TEXT,
  service_name     TEXT    NOT NULL,
  order_reference  TEXT,
  status           TEXT    NOT NULL DEFAULT 'purchased',
  created_at       TEXT    NOT NULL,
  PRIMARY KEY (owner_hash, order_identifier)
);

CREATE INDEX IF NOT EXISTS idx_cd_orders_owner ON click_drop_orders(owner_hash);
