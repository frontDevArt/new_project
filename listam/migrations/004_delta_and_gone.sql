-- Версия 4: дельта прогона и снятые объявления.
--
-- listings.gone_at — когда объявление впервые не встретилось на полном обходе.
--   Отдельная колонка, а не вывод из last_seen: вернувшееся объявление обязано
--   забыть дату снятия, а last_seen двигает каждый прогон.
-- runs.mode — full | partial | resume | fresh. Меркой полноты для следующего
--   обхода считается только full: инкрементальный прогон на двух страницах
--   не имеет права стать нормой, иначе проверка недобора страниц замолчит.
--   NULL — прогоны M0, они были полными.
-- runs.price_changed — сколько карточек сменили сырую цену. Раньше они терялись
--   внутри updated_listings, и «фиксирует изменения цен» нечем было предъявить.
-- runs.gone_marked — сколько объявлений помечено снятыми этим прогоном.
-- runs.stop_reason — почему обход кончился: конец ленты, лимит, страницы без
--   новых, потолок, ошибка.

ALTER TABLE listings ADD COLUMN gone_at TEXT;

ALTER TABLE runs ADD COLUMN mode TEXT;
ALTER TABLE runs ADD COLUMN price_changed INTEGER DEFAULT 0;
ALTER TABLE runs ADD COLUMN gone_marked INTEGER DEFAULT 0;
ALTER TABLE runs ADD COLUMN stop_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_listings_gone_at ON listings(gone_at);
CREATE INDEX IF NOT EXISTS idx_listings_last_seen ON listings(last_seen);
CREATE INDEX IF NOT EXISTS idx_price_history_seen_at ON price_history(seen_at);
