-- Версия 1: объявления, история цен, журнал прогонов.
-- Таблицы заявок/матчей/контактов создаются пустыми — наполняются на M2 и M4.

CREATE TABLE IF NOT EXISTS listings (
    id              TEXT PRIMARY KEY,
    url             TEXT NOT NULL,
    title           TEXT,
    district        TEXT,
    street          TEXT,
    price_raw       TEXT,
    currency        TEXT,
    price_usd       REAL,
    price_amd       REAL,
    area            REAL,
    rooms           INTEGER,
    floor           INTEGER,
    floors_total    INTEGER,
    price_per_sqm   REAL,
    seller_type     TEXT,
    verified        INTEGER,
    new_build       INTEGER,
    cluster_id      TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_listings_district ON listings(district);
CREATE INDEX IF NOT EXISTS idx_listings_price_usd ON listings(price_usd);
CREATE INDEX IF NOT EXISTS idx_listings_rooms ON listings(rooms);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_first_seen ON listings(first_seen);
CREATE INDEX IF NOT EXISTS idx_listings_cluster ON listings(cluster_id);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  TEXT NOT NULL REFERENCES listings(id),
    seen_at     TEXT NOT NULL,
    price_usd   REAL
);

CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id);

CREATE TABLE IF NOT EXISTS requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id     TEXT UNIQUE,
    client_name     TEXT,
    client_phone    TEXT,
    created_at      TEXT,
    status          TEXT DEFAULT 'active',
    budget_max      REAL,
    budget_stretch  REAL,
    districts       TEXT,
    rooms           TEXT,
    area_min        REAL,
    area_max        REAL,
    floor_rules     TEXT,
    must_have       TEXT,
    nice_to_have    TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS matches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id    INTEGER NOT NULL REFERENCES requests(id),
    listing_id    TEXT NOT NULL REFERENCES listings(id),
    score         REAL,
    matched_at    TEXT,
    status        TEXT DEFAULT 'new',
    reject_reason TEXT,
    UNIQUE(request_id, listing_id)
);

CREATE TABLE IF NOT EXISTS contacts (
    listing_id  TEXT PRIMARY KEY REFERENCES listings(id),
    seller_name TEXT,
    phone       TEXT,
    fetched_at  TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    rate_amd_per_usd  REAL,
    pages_fetched     INTEGER DEFAULT 0,
    listings_seen     INTEGER DEFAULT 0,
    new_listings      INTEGER DEFAULT 0,
    updated_listings  INTEGER DEFAULT 0,
    errors            INTEGER DEFAULT 0,
    notes             TEXT
);
