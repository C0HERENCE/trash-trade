-- SQLite schema for trash-trade
-- PRAGMA journal_mode=WAL;
-- PRAGMA synchronous=NORMAL;
-- PRAGMA foreign_keys=ON;

BEGIN;

CREATE TABLE IF NOT EXISTS klines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  interval TEXT NOT NULL,
  open_time INTEGER NOT NULL,
  close_time INTEGER NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL NOT NULL,
  trades INTEGER NOT NULL,
  is_closed INTEGER NOT NULL, -- 0/1
  source TEXT NOT NULL,       -- rest/ws
  created_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_klines_symbol_interval_open_time
  ON klines(symbol, interval, open_time);

CREATE INDEX IF NOT EXISTS idx_klines_close_time
  ON klines(close_time);

CREATE TABLE IF NOT EXISTS positions (
  position_id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy TEXT NOT NULL DEFAULT 'default',
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,          -- LONG/SHORT
  qty REAL NOT NULL,
  entry_price REAL NOT NULL,
  entry_time INTEGER NOT NULL,
  leverage INTEGER NOT NULL,
  margin REAL NOT NULL,
  stop_price REAL,
  tp1_price REAL,
  tp2_price REAL,
  status TEXT NOT NULL,        -- OPEN/CLOSED
  realized_pnl REAL NOT NULL DEFAULT 0,
  fees_total REAL NOT NULL DEFAULT 0,
  liq_price REAL,
  close_time INTEGER,
  close_reason TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_positions_status
  ON positions(status);

CREATE INDEX IF NOT EXISTS idx_positions_symbol_status
  ON positions(symbol, status);

CREATE INDEX IF NOT EXISTS idx_positions_strategy_status
  ON positions(strategy, status);

CREATE INDEX IF NOT EXISTS idx_positions_entry_time
  ON positions(entry_time);

CREATE TABLE IF NOT EXISTS trades (
  trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy TEXT NOT NULL DEFAULT 'default',
  symbol TEXT NOT NULL,
  position_id INTEGER NOT NULL,
  side TEXT NOT NULL,          -- BUY/SELL
  trade_type TEXT NOT NULL,    -- ENTRY/EXIT
  price REAL NOT NULL,
  qty REAL NOT NULL,
  notional REAL NOT NULL,
  fee_amount REAL NOT NULL,
  fee_rate REAL NOT NULL,
  timestamp INTEGER NOT NULL,
  reason TEXT NOT NULL,        -- signal/stop/tp/manual etc
  created_at INTEGER NOT NULL,
  FOREIGN KEY(position_id) REFERENCES positions(position_id)
);

CREATE INDEX IF NOT EXISTS idx_trades_timestamp
  ON trades(timestamp);

CREATE INDEX IF NOT EXISTS idx_trades_position
  ON trades(position_id);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_timestamp
  ON trades(symbol, timestamp);

CREATE INDEX IF NOT EXISTS idx_trades_strategy_timestamp
  ON trades(strategy, timestamp);

CREATE TABLE IF NOT EXISTS equity_snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy TEXT NOT NULL DEFAULT 'default',
  timestamp INTEGER NOT NULL,
  balance REAL NOT NULL,
  equity REAL NOT NULL,
  upl REAL NOT NULL,
  margin_used REAL NOT NULL,
  free_margin REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_equity_snapshots_timestamp
  ON equity_snapshots(timestamp);

CREATE INDEX IF NOT EXISTS idx_equity_snapshots_strategy_timestamp
  ON equity_snapshots(strategy, timestamp);

CREATE TABLE IF NOT EXISTS alerts (
  alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy TEXT NOT NULL DEFAULT 'default',
  timestamp INTEGER NOT NULL,
  channel TEXT NOT NULL,
  level TEXT NOT NULL,         -- INFO/WARN/ERROR
  message TEXT NOT NULL,
  dedup_key TEXT,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_timestamp
  ON alerts(timestamp);

CREATE INDEX IF NOT EXISTS idx_alerts_dedup_key
  ON alerts(dedup_key);

CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger (
  ledger_id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy TEXT NOT NULL DEFAULT 'default',
  timestamp INTEGER NOT NULL,
  type TEXT NOT NULL,            -- fee / realized_pnl / funding
  amount REAL NOT NULL,          -- positive = credit, negative = debit
  currency TEXT NOT NULL DEFAULT 'USDT',
  symbol TEXT,
  ref TEXT,                      -- e.g. position_id or funding time
  note TEXT,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_timestamp
  ON ledger(timestamp);

CREATE INDEX IF NOT EXISTS idx_ledger_type
  ON ledger(type);

CREATE INDEX IF NOT EXISTS idx_ledger_strategy_timestamp
  ON ledger(strategy, timestamp);

COMMIT;
