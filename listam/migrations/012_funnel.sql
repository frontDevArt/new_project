-- Версия 12: воронка и обратная связь.
--
-- listing_pages — кэш открытой страницы объявления: разобранные поля (JSON
--   `PageFields`), а не HTML. Сырьё для отладки кладёт на диск только
--   `pages --keep-html`.
--   status — ok | gone | failed. gone не открывается больше никогда,
--   failed повторяется до `funnel.max_attempts`.
--   price_raw — сырая цена карточки в момент открытия: сменилась — страницу
--   пора открыть снова (решение 13).
--   parser_version — чем разобрано: правка разбора может потребовать
--   переразбора, и отличить старое от нового надо по строке, а не по дате.
-- request_exclusions — отказы клиента, сузившие заявку (фаза 6). Таблицу
--   заявок ведёт брокер, и синхронизация её не перезаписывает — поэтому
--   исключения живут здесь. match_id — какая отметка их породила: откат
--   отметки снимает её исключения.
-- matches.origin — market | request: кто родил матч. «Звони сейчас» берёт
--   только market (решение 14). Пишется при вставке и не сравнивается.
--   NULL — матч из времён до этой миграции.

CREATE TABLE IF NOT EXISTS listing_pages (
    listing_id      TEXT PRIMARY KEY,
    fetched_at      TEXT,
    status          TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    price_raw       TEXT,
    fields          TEXT,
    parser_version  INTEGER NOT NULL DEFAULT 1,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS request_exclusions (
    id          INTEGER PRIMARY KEY,
    request_id  INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    value       TEXT,
    reason      TEXT,
    match_id    INTEGER,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_exclusions_request ON request_exclusions(request_id);

ALTER TABLE matches ADD COLUMN origin TEXT;
