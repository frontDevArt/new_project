-- Версия 2: аномалии в карточках, курс у точки истории, возобновление обхода.
--
-- Зачем каждая колонка:
--   price_history.rate_amd_per_usd — без курса прогона старую точку истории
--     нельзя истолковать: непонятно, подешевело объявление или сдвинулся курс.
--   listings.anomaly — перечень сработавших правил проверки через запятую
--     (NULL — чисто). Объявление с опечаткой продавца мы храним как есть,
--     но помечаем, чтобы оно не попадало в медианы.
--   runs.last_page — номер последней успешно пройденной страницы,
--     с неё продолжает `scrape --resume`.

ALTER TABLE price_history ADD COLUMN rate_amd_per_usd REAL;
ALTER TABLE listings ADD COLUMN anomaly TEXT;
ALTER TABLE runs ADD COLUMN last_page INTEGER;

CREATE INDEX IF NOT EXISTS idx_listings_anomaly ON listings(anomaly);
