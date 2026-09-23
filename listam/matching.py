"""Подбор объявлений под заявки: три выборки, один проход.

Оркестрация по образцу `listam/changes.py` и `listam/clustering_run.py`:
решают домены (`clustering`, `stats`, `scoring`), здесь — кто с кем
сравнивается и что из этого записывается.

Три вещи, которые здесь важнее скорости:

- **Кластеры считаются по всей базе, а не по выборке.** Иначе `--new` не
  узнает, что у свежего объявления уже есть тридцать двойников, и клиент
  получит тридцать первый звонок про ту же квартиру.
- **Отказы не пишутся.** Пара «заявка × объявление» в боевой базе даёт
  миллион строк, и девятьсот девяносто тысяч из них — «район не тот».
  В базе нужны те, по которым можно звонить.
- **Пересчёт не трогает след звонка.** `status` и `reject_reason` пишет
  только человек (решение 7); `upsert_match` их не перечисляет вовсе.

Печать витрины живёт в `listam/matches_view.py`: подбор пишет базу под
замком, витрина её только читает, и поводы для правки у них разные.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from listam.changes import since_point
from listam.clustering_run import area_tolerance, cluster_database
from listam.config import Config, ConfigError, score_threshold, threshold
from listam.domain.clustering import clusters
from listam.domain.events import NOT_REPRESENTATIVE
from listam.domain.models import ListingPage, Match, PageFields, Request
from listam.domain.scoring import (DEFAULT_HOT, DEFAULT_SECONDARY_DISTRICT,
                                   DEFAULT_STRETCH_PERCENT, DEFAULT_WEIGHTS, NOT_OPENED,
                                   score)
from listam.domain.wishes import request_wishes
from listam.domain.stats import median_price_per_sqm_by_district
from listam.ports.database import Database
from listam.runner import SessionRefused, publish, working_session

DEFAULT_DIGEST = 40.0


@dataclass
class MatchReport:
    """Что сделал подбор. Печатает это CLI, а не сам подбор."""

    scope: str = ""             # «заявка R-1», «новые объявления…», «вся база»
    requests: int = 0
    listings: int = 0           # объявлений в выборке
    considered: int = 0         # из них представителей кластеров
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    retired: int = 0            # матчей закрыто: проход их больше не подтверждает
    hot: int = 0                # score >= match.thresholds.hot
    digest: int = 0             # hot > score >= digest
    errors: int = 0
    notes: str | None = None
    finished_at: datetime | None = None

    def render(self) -> str:
        lines = [
            f"Подбор: {self.scope}",
            f"Заявок: {self.requests}, объявлений в выборке: {self.listings}, "
            f"из них представителей кластеров: {self.considered}",
            f"Матчи: новых {self.new}, обновлённых {self.updated}, "
            f"без изменений {self.unchanged}",
        ]
        if self.retired:
            lines.append(f"Закрыто матчей: {self.retired} — вариант больше не подходит")
        lines.append(f"Из них горячих: {self.hot}, в дайджест: {self.digest}")
        if self.notes:
            lines.append(self.notes)
        return "\n".join(lines)


@dataclass
class Settings:
    """Веса и пороги подбора — из конфига, а не из кода."""

    weights: dict[str, float]
    stretch_percent: float
    hot: float | None
    digest: float | None
    secondary_district: float = DEFAULT_SECONDARY_DISTRICT


def _share(config: Config, key: str, default: float) -> float:
    """Доля 0…1. `null` здесь не «выключено»: доле нечего выключать."""
    value = threshold(config, key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float))             or not 0 <= value <= 1:
        raise ConfigError(
            f"{key} = {value!r} не годится: это доля от 0 до 1, числом без кавычек."
        )
    return float(value)


def settings(config: Config) -> Settings:
    """Читает секцию `match`. Ноль значит ноль, `null` — выключено.

    Имена факторов проверяются на входе. Опечатка `budjet` вместо `budget`
    стоит фактору веса 30 и не видна ничем: балл считается, пишется в базу
    и выглядит правдоподобно — просто он другой. Молчать про это нельзя,
    как нельзя молчать про `--limit 0`.
    """
    weights = config.get("match.weights", None)
    if weights:
        known = set(DEFAULT_WEIGHTS)
        unknown = sorted(set(weights) - known)
        missing = sorted(known - set(weights))
        if unknown or missing:
            trouble = []
            if unknown:
                trouble.append(f"таких факторов нет: {', '.join(unknown)}")
            if missing:
                trouble.append(f"не названы: {', '.join(missing)}")
            raise ConfigError(
                f"match.weights — {'; '.join(trouble)}. "
                f"Факторы балла: {', '.join(sorted(known))}. "
                f"Вес 0 выключает фактор; убирать его из списка нельзя — "
                f"молча выпавший фактор меняет балл и не виден ничем."
            )
    stretch = threshold(config, "match.budget_stretch_percent", DEFAULT_STRETCH_PERCENT)
    hot = score_threshold(config, "match.thresholds.hot", DEFAULT_HOT)
    digest = score_threshold(config, "match.thresholds.digest", DEFAULT_DIGEST)
    secondary = _share(config, "match.secondary_district", DEFAULT_SECONDARY_DISTRICT)
    return Settings(
        weights=dict(weights) if weights else dict(DEFAULT_WEIGHTS),
        # Растяжка выключена — значит не растягиваем вовсе, а не «берём
        # десять процентов по умолчанию»: человек сказал «нет», а не промолчал.
        stretch_percent=0.0 if stretch is None else float(stretch),
        hot=hot,
        digest=digest,
        secondary_district=secondary,
    )


def _requests_to_match(database: Database, external_id: str | None
                       ) -> tuple[list[Request], str | None]:
    """Заявки выборки и причина, если выборки нет.

    Заявка, названная руками, обязана быть активной: «подобрал ноль» на
    приостановленной заявке — это не ответ, а молчание.
    """
    if external_id is None:
        return list(database.iter_requests()), None

    request = database.get_request(external_id)
    if request is None:
        return [], f"заявки {external_id} в базе нет — сначала прочитай источник: " \
                   f"python -m listam requests"
    if request.status != "active":
        return [], f"заявка {external_id} со статусом {request.status}: " \
                   f"матчатся только active"
    return [request], None


def _listings_scope(database: Database, only_new: bool, config: Config,
                    everything: list) -> tuple[list, str]:
    """Выборка объявлений и как она называется по-человечески.

    `everything` — уже прочитанная вся база: при подборе без сужений выборка
    это она и есть, и читать её вторым запросом незачем.
    """
    if not only_new:
        return everything, "вся база"
    # Мерка та же, что у `changes`: начало последнего прогона. Но берём не
    # «появившееся с неё», а «тронувшееся с неё»: подешевевшая квартира новой
    # не стала, а звонить по ней надо сегодня.
    mark, note = since_point(
        database, None, fallback_hours=config.get("changes.fallback_hours", 24)
    )
    return (database.listings_touched_since(mark),
            f"новое и подешевевшее: {note}")


def _is_edited(request: Request) -> bool:
    """Заявку тронули после её последнего подбора?

    Ни разу не подбиравшаяся заявка — тоже «правленая»: по выборке последнего
    прогона она увидит три вчерашних объявления вместо всей базы.
    """
    if request.matched_at is None:
        return True
    return (request.updated_at or request.created_at or request.matched_at) \
        > request.matched_at


def known_fields(page: ListingPage | None, max_attempts: int) -> PageFields | None:
    """Что подбор знает о странице объявления.

    Открыта — её поля. Не открывалась или ждёт повтора — `None`: объявление
    с жёсткими пожеланиями остаётся кандидатом (решение 9). Исчерпала
    попытки — пустые поля: узнать больше нечем, всё неизвестно, а неизвестное
    не отказ (решение 13). Снятая — тоже `None`: матч по ней не рождается,
    а ленту догонит ночной обход.
    """
    if page is None:
        return None
    if page.status == "ok":
        return page.fields or PageFields()
    if page.status == "failed" and page.attempts >= max_attempts:
        return PageFields()
    return None


def run_match(config: Config, *, external_id: str | None = None,
              only_new: bool = False) -> MatchReport:
    """Один проход подбора. Сводку печатает вызывающий.

    Флага «пересчитать всё» здесь нет: «все активные заявки по всей базе» —
    это и есть подбор без сужений. Проверка «флаг обязателен» живёт в CLI и
    там и остаётся: команда без флагов отклоняется кодом 2, а не толкуется
    как «пересчитай всё». Аргумент `recount_all` подбор принимал и нигде не
    использовал — параметр, которому нечего делать, однажды прочитают как
    обещание.
    """
    report = MatchReport(scope="заявка " + external_id if external_id else "вся база")

    # Конфиг читается до замка и до базы: «бессмысленное значение отклоняется
    # на входе» значит «до работы». Опечатка в имени веса, прочитанная посреди
    # прохода, прилетала бы человеку уже поверх пересчитанных кластеров.
    tuning = settings(config)
    # Воронка — там же, до замка: кривой словарь пожеланий отклоняется на
    # входе, а не посреди прохода. Импорт здесь: шаг `pages` сам берёт
    # у подбора `settings`, и сверху получился бы круг.
    from listam.pages import funnel_settings
    funnel = funnel_settings(config)

    # Замок, свежая копия, миграции и заливка — общий каркас
    # (`listam/runner.py`): тот же порядок, что у прогона, пересчёта,
    # кластеров и заявок.
    notes: list[str] = []
    try:
        with working_session(config) as session:
            notes.extend(session.notes)
            session.notes = notes     # заливка пишет в тот же список
            database = session.database

            requests, refusal = _requests_to_match(database, external_id)
            if refusal is not None:
                report.errors = 1
                notes.append(refusal)
                return report
            report.requests = len(requests)
            if not requests:
                # Не ошибка: заявок может не быть ещё или уже. Но и не тишина —
                # пустой подбор обязан сказать, почему он пустой.
                notes.append("подбирать не под что: нет активных заявок")
                return report

            # Вся таблица читается один раз за прогон. Кластеры, медианы и
            # выборка считаются по ней, а не каждый по своему чтению.
            #
            # Кластеры считаются по всей базе, а не по выборке: у свежего
            # объявления двойники могли появиться задолго до него.
            everything = database.listings_for_matching()
            _count_clusters(database, config, notes, everything)
            found = clusters(everything, area_tolerance(config))
            representatives = {cluster.cheapest_id: cluster for cluster in found}
            medians = median_price_per_sqm_by_district(everything)

            selection, scope = _listings_scope(database, only_new, config, everything)
            report.scope = f"заявка {external_id}" if external_id else scope
            report.listings = len(selection)
            candidates = [item for item in selection if item.id in representatives]
            report.considered = len(candidates)

            # Чего проход не видел: снятое с ленты и отложенное аномалией.
            # Закрывать по такому нельзя — см. `_write_matches`.
            alive_ids = {item.id for item in everything}
            off_the_feed = database.known_ids() - alive_ids

            last_run = database.last_run()
            run_id = last_run.id if last_run else None

            # Страницы кандидатов — одним запросом на прогон (решение 9).
            pages = database.pages_for(representatives)
            # Решение 14: матч новой или правленой заявки — первичная
            # подборка (`request`), остальное принёс рынок (`market`).
            born = {request.id for request in requests if _is_edited(request)}
            context = _Context(pages=pages, max_attempts=funnel.max_attempts,
                               wishes=funnel.wishes, born=born)

            # Заявка, которую тронули после её последнего подбора, выборкой
            # объявлений не покрывается: изменился не рынок, а условия. Такую
            # ведём по всей базе — иначе поднятый бюджет заработает только ночью.
            edited = [request for request in requests
                      if only_new and _is_edited(request)]
            fresh = [request for request in requests if request not in edited]
            if edited:
                notes.append(f"правленых заявок: {len(edited)} — по всей базе")
                whole = [item for item in everything if item.id in representatives]
                _write_matches(database, report, edited, whole, representatives,
                               medians, run_id, tuning,
                               full_sweep=True, off_the_feed=off_the_feed,
                               everything_by_id=alive_ids, context=context)
            if fresh:
                _write_matches(database, report, fresh, candidates, representatives,
                               medians, run_id, tuning,
                               full_sweep=not only_new, off_the_feed=off_the_feed,
                               everything_by_id=alive_ids, context=context)
            database.mark_requests_matched(
                [request.id for request in requests if request.id is not None],
                datetime.now(timezone.utc),
            )

            if report.new or report.updated or report.retired:
                publish(session, config, "база с матчами")
                report.errors += session.failures
    except SessionRefused as exc:
        report.errors = 1
        notes.append(str(exc))
    finally:
        report.notes = "; ".join(note for note in notes if note) or None
        report.finished_at = datetime.now(timezone.utc)
    return report


def _count_clusters(database: Database, config: Config, notes: list[str],
                    listings: list) -> None:
    """Кластеры перед подбором — каждый раз, а не когда в базе есть пустые.

    Спека: пересчёт идёт автоматически перед матчингом. Команду `cluster`
    никто не обязан помнить, а без кластеров клиент получает одну квартиру
    тридцать раз.

    Признак «есть объявление без cluster_id» ловит только появление. Уход
    с ленты состав кластера тоже меняет, колонок не трогая: кластер из трёх
    становится кластером из двух и получает другое имя, а `listings.cluster_id`
    остаётся вчерашним — и база начинает спорить со снимком в матче, который
    считается заново каждым подбором.

    Замка здесь второго нет: `cluster_database` работает по уже открытой
    базе — ровно для этого он и отделён от команды. Выборку он тоже не читает
    сам: её читает `run_match`, один раз за прогон.
    """
    counted = cluster_database(database, area_tolerance(config), listings=listings)
    notes.append(
        f"кластеры пересчитаны: объявлений {counted.listings}, "
        f"кластеров {counted.clusters}, изменено строк {counted.changed}"
    )


@dataclass
class _Context:
    """Что подбору нужно от воронки: страницы, словарь и кто родил матч."""

    pages: dict[str, ListingPage]
    max_attempts: int
    wishes: dict
    born: set[int]


def _write_matches(database: Database, report: MatchReport, requests, candidates,
                   representatives: dict, medians: dict[str, float],
                   run_id: int | None, tuning: Settings,
                   full_sweep: bool, off_the_feed: set[str],
                   everything_by_id: set[str], context: _Context) -> None:
    """Пара «заявка × представитель» → балл → строка в `matches`.

    `full_sweep` — прошли ли по всей базе. Только полный проход имеет право
    закрывать матчи: по выборке `--new` «не подтвердился» значит «его не было
    в выборке», и закрытие выкинуло бы из витрины всё, кроме свежего.
    Исключение одно — «не представитель кластера»: его знает кластеризация
    всей базы, и частичный проход закрывает такие матчи тоже.

    `off_the_feed` — объявления, которых проход не видел вовсе: снятые с
    ленты и отложенные аномалией. Их матчи не закрываются даже полным
    проходом: решение 8 спеки велит витрине снятое помечать, а не прятать,
    и «мы звонили по этой квартире» уходу объявления не подчиняется.

    `everything_by_id` — все активные объявления прохода. Живое, но не
    представитель кластера, закрывается со своей причиной: появился двойник
    дешевле. Это не «бюджет» и не «район» — клиенту ту же квартиру покажут
    по другой карточке.

    `context` — страницы и пожелания (фаза 3 M3.5). Отказ «страница не
    открыта» не пишется и не закрывает старый матч (решение 12): проход
    это объявление не видел, а не видел и не подтвердил.
    """
    now = datetime.now(timezone.utc)
    not_representatives = everything_by_id - representatives.keys()
    for request in requests:
        confirmed: set[str] = set()
        # Почему вариант не подтвердился — знает только этот цикл: жёсткий
        # критерий назвал причину словом, а представительство в кластере
        # видно по `representatives`. Дальше это слово читает человек в
        # уведомлении «отпало: бюджет 3, район 1», и общая фраза ему
        # не отвечает ни на что.
        reasons: dict[str, str] = dict.fromkeys(not_representatives,
                                                NOT_REPRESENTATIVE)
        # Матчи заявки копятся и пишутся одной транзакцией: по одной на строку
        # боевые 67 000 матчей стоили минуту фиксаций на диск.
        batch: list[Match] = []
        must, nice = request_wishes(request, context.wishes)
        origin = "request" if request.id in context.born else "market"
        unseen: set[str] = set()
        for listing in candidates:
            page = (known_fields(context.pages.get(listing.id), context.max_attempts)
                    if must or nice else None)
            result = score(request, listing, median_by_district=medians,
                           weights=tuning.weights,
                           stretch_percent=tuning.stretch_percent,
                           page=page, must=must, nice=nice,
                           secondary_district=tuning.secondary_district)
            if result.rejected_by == NOT_OPENED:
                unseen.add(listing.id)
                continue
            if result.rejected_by is not None:
                # Отказ в базу не пишется: их миллионы, и звонить по ним некуда.
                # Но причина запоминается: если на это объявление есть вчерашний
                # матч, закрыть его надо со словом, а не с общей фразой.
                reasons[listing.id] = result.rejected_by
                continue
            cluster = representatives[listing.id]
            batch.append(Match(
                request_id=request.id,
                listing_id=listing.id,
                score=float(result.value),
                run_id=run_id,
                breakdown=result.breakdown or None,
                cluster_id=cluster.cluster_id,
                cluster_size=cluster.size,
                cluster_spread_usd=cluster.spread_usd,
                origin=origin,
            ))
            confirmed.add(listing.id)
            if tuning.hot is not None and result.value >= tuning.hot:
                report.hot += 1
            elif tuning.digest is not None and result.value >= tuning.digest:
                report.digest += 1
        for outcome, count in database.upsert_matches(batch, now).items():
            setattr(report, outcome, getattr(report, outcome) + count)
        if full_sweep:
            report.retired += database.retire_matches(
                request.id, keep=confirmed | off_the_feed | unseen, now=now,
                reasons=reasons,
                default="проход больше не подтверждает этот вариант",
            )
        else:
            # Представителя кластера выбирает вся база, а не выборка: карточка,
            # уступившая место двойнику дешевле, известна и частичному проходу.
            # Не закрыть её здесь — значит отдать «Звони сейчас» двойника
            # «новым», а закрытие — ночному `match --all`, в чужое окно.
            report.retired += database.retire_matches(
                request.id, keep=representatives.keys() | off_the_feed, now=now,
                reasons=reasons, default=NOT_REPRESENTATIVE,
            )
