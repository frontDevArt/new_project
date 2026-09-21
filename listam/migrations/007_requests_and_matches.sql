-- Версия 7: заявки покупателей и матчи.
--
-- requests.districts_priority — приоритетные районы отдельно от допустимых:
--   фактор «район» весом 20 различает «согласен» и «хочет именно там».
-- requests.floor_min/floor_max/no_first_floor/no_last_floor — этажные правила
--   колонками, а не строкой floor_rules: скоринг читает их без разбора
--   свободного текста, а floor_rules остаётся человеческой заметкой.
-- requests.source_row — сырая строка источника целиком (JSON). Спорную заявку
--   надо уметь предъявить ровно в том виде, в каком её ввёл человек.
-- matches.breakdown — разбор балла по факторам (JSON). Без него на вопрос
--   «почему 68, а не 71» нечего ответить ни в отладке, ни клиенту.
-- matches.cluster_* — снимок кластера на момент матча: сколько объявлений
--   в нём было и какой разброс цен. Единица показа — кластер, а не карточка.
-- matches.first_matched_at — когда матч появился впервые. matched_at двигает
--   каждый пересчёт, и по нему «когда мы это нашли» уже не узнать.
-- runs.new_matches — счётчик назван в спеке с M0 и до сих пор не заведён.

ALTER TABLE requests ADD COLUMN districts_priority TEXT;
ALTER TABLE requests ADD COLUMN floor_min INTEGER;
ALTER TABLE requests ADD COLUMN floor_max INTEGER;
ALTER TABLE requests ADD COLUMN no_first_floor INTEGER DEFAULT 0;
ALTER TABLE requests ADD COLUMN no_last_floor INTEGER DEFAULT 0;
ALTER TABLE requests ADD COLUMN updated_at TEXT;
ALTER TABLE requests ADD COLUMN source_row TEXT;

ALTER TABLE matches ADD COLUMN run_id INTEGER;
ALTER TABLE matches ADD COLUMN breakdown TEXT;
ALTER TABLE matches ADD COLUMN cluster_id TEXT;
ALTER TABLE matches ADD COLUMN cluster_size INTEGER DEFAULT 1;
ALTER TABLE matches ADD COLUMN cluster_spread_usd REAL;
ALTER TABLE matches ADD COLUMN first_matched_at TEXT;

ALTER TABLE runs ADD COLUMN new_matches INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_matches_request_score ON matches(request_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_matches_listing ON matches(listing_id);
CREATE INDEX IF NOT EXISTS idx_listings_cluster ON listings(cluster_id);
CREATE INDEX IF NOT EXISTS idx_listings_status_district ON listings(status, district);
CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
