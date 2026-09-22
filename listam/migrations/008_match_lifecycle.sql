-- Версия 8: у матча появляется конец жизни.
--
-- matches.retired_at — когда полный проход по заявке перестал подтверждать
--   этот матч. Удалять строку нельзя: в ней след звонка (status,
--   reject_reason), и «мы звонили по этой квартире» не должно исчезать
--   вместе с подорожавшим объявлением.
-- matches.retired_reason — почему перестал: «бюджет», «район», «не
--   представитель кластера». Читает человек, а не программа.
--
-- Индекс по (request_id, retired_at) — витрина спрашивает только живые
-- матчи одной заявки, и это её главный запрос.

ALTER TABLE matches ADD COLUMN retired_at TEXT;
ALTER TABLE matches ADD COLUMN retired_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_matches_alive ON matches(request_id, retired_at);
