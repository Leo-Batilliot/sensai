-- business.db: demo data of Nordvent Distribution (B2B office supplies).
--
-- Conventions
--   * Dates are ISO 'YYYY-MM-DD' text: comparisons and julianday() work directly
--     on them. Checked with date(x) IS x, not =, because date() returns NULL on a
--     bad format and a CHECK that evaluates to NULL passes.
--   * Amounts are euros, excluding tax unless the column says otherwise.
--   * Comments inside CREATE TABLE are kept in sqlite_master.sql and can be shown
--     to the model, so keep them short and factual. Comments outside a CREATE
--     statement, like this header, are not stored.
--   * Foreign keys are only enforced on connections that run
--     PRAGMA foreign_keys = ON: the setting is per connection, not stored in the file.
--     Open the database with sensai.data.schema.connect(), which turns it on.

PRAGMA foreign_keys = ON;

CREATE TABLE sales_reps (
  id         INTEGER PRIMARY KEY,
  full_name  TEXT NOT NULL,
  email      TEXT NOT NULL UNIQUE,
  region     TEXT NOT NULL,                -- sales region, e.g. 'Hauts-de-France'
  hired_on   TEXT NOT NULL CHECK (date(hired_on) IS hired_on)
);

CREATE TABLE clients (
  id                  INTEGER PRIMARY KEY,
  name                TEXT NOT NULL,
  siret               TEXT UNIQUE,          -- French company id, 14 digits
  sector              TEXT NOT NULL,        -- business sector, e.g. 'Restauration'
  city                TEXT NOT NULL,
  segment             TEXT NOT NULL CHECK (segment IN ('TPE','PME','ETI','GE')),  -- company size
  status              TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active','inactive','archived')),
  sales_rep_id        INTEGER REFERENCES sales_reps(id),  -- account manager
  payment_terms_days  INTEGER NOT NULL DEFAULT 30 CHECK (payment_terms_days >= 0),
  created_at          TEXT NOT NULL CHECK (date(created_at) IS created_at)
);

CREATE TABLE contacts (
  id         INTEGER PRIMARY KEY,
  client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  full_name  TEXT NOT NULL,
  email      TEXT,
  phone      TEXT,
  role       TEXT                          -- job title at the client
);

CREATE TABLE products (
  id          INTEGER PRIMARY KEY,
  sku         TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  category    TEXT NOT NULL,
  unit_price  REAL NOT NULL CHECK (unit_price >= 0),  -- current list price
  unit_cost   REAL NOT NULL CHECK (unit_cost >= 0),   -- current purchase cost
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE orders (
  id          INTEGER PRIMARY KEY,
  client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
  order_date  TEXT NOT NULL CHECK (date(order_date) IS order_date),
  status      TEXT NOT NULL CHECK (status IN ('pending','shipped','delivered','cancelled'))
);

CREATE TABLE order_lines (
  id          INTEGER PRIMARY KEY,
  order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_id  INTEGER NOT NULL REFERENCES products(id),
  quantity    INTEGER NOT NULL CHECK (quantity > 0),
  unit_price  REAL NOT NULL CHECK (unit_price >= 0),  -- price charged, discount included
  unit_cost   REAL NOT NULL CHECK (unit_cost >= 0)    -- cost frozen at order time; margin = quantity * (unit_price - unit_cost)
);

CREATE TABLE invoices (
  id          INTEGER PRIMARY KEY,
  order_id    INTEGER NOT NULL UNIQUE REFERENCES orders(id) ON DELETE RESTRICT,  -- one invoice per order
  client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE RESTRICT,
  issued_on   TEXT NOT NULL CHECK (date(issued_on) IS issued_on),
  due_on      TEXT NOT NULL CHECK (date(due_on) IS due_on AND due_on >= issued_on),
  amount_ht   REAL NOT NULL CHECK (amount_ht >= 0),           -- excluding tax
  amount_ttc  REAL NOT NULL CHECK (amount_ttc >= amount_ht),  -- including tax
  status      TEXT NOT NULL
              CHECK (status IN ('issued','paid','partially_paid','overdue','cancelled'))
);

CREATE TABLE payments (
  id          INTEGER PRIMARY KEY,
  invoice_id  INTEGER NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT,
  paid_on     TEXT NOT NULL CHECK (date(paid_on) IS paid_on),  -- payment delay = julianday(paid_on) - julianday(invoices.issued_on)
  amount      REAL NOT NULL CHECK (amount > 0),
  method      TEXT NOT NULL CHECK (method IN ('virement','cheque','carte','prelevement'))
);

CREATE TABLE write_audit (
  id            INTEGER PRIMARY KEY,
  at            TEXT NOT NULL,             -- ISO timestamp of the commit
  session_id    TEXT,
  sql           TEXT NOT NULL,
  rows_affected INTEGER NOT NULL,
  approved_by   TEXT NOT NULL
);

CREATE INDEX idx_clients_sales_rep ON clients(sales_rep_id);
CREATE INDEX idx_contacts_client ON contacts(client_id);
CREATE INDEX idx_orders_client_date ON orders(client_id, order_date);
CREATE INDEX idx_order_lines_order ON order_lines(order_id);
CREATE INDEX idx_order_lines_product ON order_lines(product_id);
CREATE INDEX idx_invoices_client ON invoices(client_id);
CREATE INDEX idx_payments_invoice ON payments(invoice_id);
