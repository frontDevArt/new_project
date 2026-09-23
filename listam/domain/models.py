"""Доменные сущности. Ядро системы — заявка; на M0 наполняется только объявление."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime


@dataclass
class Listing:
    """Объявление о продаже квартиры — снимок карточки из ленты."""

    id: str
    url: str
    title: str | None = None
    district: str | None = None
    street: str | None = None
    price_raw: str | None = None
    currency: str | None = None
    price_amount: float | None = None      # сумма в валюте оригинала, как на сайте
    price_usd: float | None = None
    price_amd: float | None = None
    area: float | None = None
    rooms: int | None = None
    floor: int | None = None
    floors_total: int | None = None
    price_per_sqm: float | None = None
    seller_type: str | None = None        # owner | agency | None
    verified: bool | None = None
    new_build: bool | None = None
    anomaly: str | None = None            # сработавшие правила проверки через запятую
    cluster_id: str | None = None         # заполняется на M2 (дедуп)
    status: str = "active"                # active | gone
    gone_at: datetime | None = None       # когда объявление ушло с ленты; вернулось — снова None
    returned_at: datetime | None = None   # когда снятое объявление снова встретилось на ленте
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    @classmethod
    def field_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]


@dataclass
class PricePoint:
    listing_id: str
    seen_at: datetime
    price_usd: float | None
    rate_amd_per_usd: float | None = None   # курс прогона: без него точку не истолковать


@dataclass
class Run:
    """Журнал прогона: курс фиксируем, чтобы старые цифры воспроизводились."""

    id: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    rate_amd_per_usd: float | None = None
    pages_fetched: int = 0
    listings_seen: int = 0
    new_listings: int = 0
    updated_listings: int = 0
    errors: int = 0
    notes: str | None = None
    mode: str | None = None                 # full | partial | resume | fresh
    price_changed: int = 0                  # карточек, у которых сменилась сырая цена
    gone_marked: int = 0                    # объявлений, помеченных снятыми
    returned: int = 0                       # снятых объявлений, вернувшихся на ленту
    stop_reason: str | None = None          # чем кончился обход
    last_page: int = 0                      # с неё продолжает `scrape --resume`


@dataclass
class Notification:
    """Строка журнала отправок: одна успешная отправка одного вида."""

    id: int | None = None
    kind: str = ""                          # hot | digest | feed
    sent_at: datetime | None = None
    window_from: datetime | None = None     # None — первая отправка этого вида
    window_to: datetime | None = None       # отсюда считается следующее окно
    events: int = 0
    requests: int = 0
    text: str | None = None
    notes: str | None = None
    channel: str | None = None              # stdout | telegram; None — до миграции 011


@dataclass
class Request:
    """Заявка покупателя — ядро системы. Фильтр производен от неё, а не наоборот."""

    id: int | None = None
    external_id: str | None = None          # идентификатор строки во внешней таблице
    client_name: str | None = None
    client_phone: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None      # двигается только содержательной правкой
    matched_at: datetime | None = None      # когда по ней последний раз шёл полный подбор
    status: str = "active"                  # active | paused | closed
    budget_max: float | None = None
    budget_stretch: float | None = None     # пусто — потолок плюс процент из конфига
    districts: list[str] = field(default_factory=list)
    districts_priority: list[str] = field(default_factory=list)
    rooms: list[int] = field(default_factory=list)
    area_min: float | None = None
    area_max: float | None = None
    floor_min: int | None = None
    floor_max: int | None = None
    no_first_floor: bool = False
    no_last_floor: bool = False
    must_have: str | None = None
    nice_to_have: str | None = None
    floor_rules: str | None = None          # человеческая заметка, скорингом не читается
    notes: str | None = None
    source_row: str | None = None           # сырая строка источника целиком (JSON)

    def stretch(self, percent: float) -> float | None:
        """Растянутый потолок бюджета.

        Вариант на 5 000 дороже бюджета всё равно стоит звонка: жёсткий
        критерий отсекает по нему, а не по `budget_max`.
        """
        if self.budget_stretch is not None:
            return self.budget_stretch
        if self.budget_max is None:
            return None
        # Не `budget_max * (1 + percent/100)`: на 100 000 и 10% это даёт
        # 110000.00000000001, и растянутый бюджет перестаёт быть круглым
        # числом в отчёте. Прибавка считается отдельно и складывается.
        return self.budget_max + self.budget_max * float(percent) / 100


@dataclass
class Match:
    """Объявление, подошедшее заявке: балл, его разбор и снимок кластера."""

    id: int | None = None
    request_id: int | None = None
    listing_id: str | None = None
    score: float | None = None
    matched_at: datetime | None = None
    first_matched_at: datetime | None = None   # когда нашли впервые; пересчёт не двигает
    status: str = "new"                        # new | sent | called | rejected
    reject_reason: str | None = None
    run_id: int | None = None
    breakdown: dict | None = None              # балл по факторам: «почему 68, а не 71»
    cluster_id: str | None = None
    cluster_size: int = 1
    cluster_spread_usd: float | None = None
    retired_at: datetime | None = None      # когда проход перестал его подтверждать
    retired_reason: str | None = None       # почему: «бюджет», «район», «не представитель»
    revived_at: datetime | None = None      # когда закрытый матч снова подтвердился
    origin: str | None = None               # market | request: кто родил; не сравнивается


@dataclass
class PageFields:
    """Разобранная страница объявления: то, чего нет в карточке ленты.

    `values` — поле → значение: `renovation` → «косметический» (слово сайта
    строчными), `elevator` → True, `ceiling_height` → 2.7. Подписи, которых
    разбор не знает, лежат списком в `values["_unknown"]`: прогон их считает,
    а не молчит.
    """

    values: dict[str, object] = field(default_factory=dict)
    description: str | None = None
    photos: list[str] = field(default_factory=list)      # ссылки; для M4


@dataclass
class ListingPage:
    """Строка кэша открытых страниц. HTML не хранится — только разобранное."""

    listing_id: str
    status: str                             # ok | gone | failed
    fetched_at: datetime | None = None      # когда открылась удачно
    attempts: int = 0                       # сколько раз пробовали; считает вызывающий
    price_raw: str | None = None            # цена карточки в момент открытия
    fields: PageFields | None = None
    error: str | None = None
