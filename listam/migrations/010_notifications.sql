-- Версия 10: уведомления получают память.
--
-- notifications — журнал отправок. Отметка «это уже отправлено» обязана
--   жить в базе: без неё повторный запуск шлёт всё заново, а отправленное
--   отозвать нельзя. Окно следующего запуска — window_to последней успешной
--   строки этого вида. Сбой сети строки не пишет: окно не сдвинулось,
--   событие не потеряно.
-- notifications.text — что именно ушло в чат. Восстановить сообщение
--   пересчётом нельзя: база с тех пор изменилась.
-- matches.revived_at — когда закрытый матч снова подтвердился. upsert гасит
--   retired_at и retired_reason, и без этой колонки воскресший матч
--   неотличим от обычного пересчёта — а он обязан быть событием ровно
--   один раз.

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,          -- hot | digest | feed
    sent_at     TEXT NOT NULL,
    window_from TEXT,                   -- NULL — первая отправка этого вида
    window_to   TEXT NOT NULL,
    events      INTEGER NOT NULL DEFAULT 0,
    requests    INTEGER NOT NULL DEFAULT 0,
    text        TEXT,
    notes       TEXT
);

CREATE INDEX IF NOT EXISTS idx_notifications_kind ON notifications(kind, sent_at);

ALTER TABLE matches ADD COLUMN revived_at TEXT;
