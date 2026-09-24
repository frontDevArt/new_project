# Конкуренты и ценовые ориентиры: мониторинг объявлений и lead-alert инструменты для риелторов

Дата сбора: 24.09.2026. Если не указано иное, цена = цена на странице вендора на дату сбора (24.09.2026). Пересчёт RUB→USD — ориентировочный, по курсу ~85 ₽/$ (допущение автора заметок, курс не проверялся по источнику; см. Gaps).

## 1. Casafari (Европа): функции, цены, рынки, финансирование, источники данных

### Takeaway
Casafari — крупнейший панъевропейский агрегатор с дедупликацией, алертами, AVM и аналитикой; цены не публикует (только демо/продажи), источник данных — собственный ML-агрегатор тысяч сайтов (по сути краулинг), а не партнёрства. Ориентирован на Южную/Западную Европу + ОАЭ, в СНГ/Кавказе/Центральной Азии/Балканах не присутствует (по найденным данным).

### Cited Findings
- Публичных цен нет: все продукты — через демо или разговор с продажами (обзор 2026 г.) — [Stream Estate, 2026](https://stream.estate/blog/casafari-alternative-review-2026); сводка поиска по сайту Casafari подтверждает отсутствие прайса — [Casafari FAQ](https://www.casafari.com/faq/)
- География: «20+ стран Европы плюс ОАЭ» (на странице Data Export — «23 страны», данные непоследовательны) — [Stream Estate, 2026](https://stream.estate/blog/casafari-alternative-review-2026)
- Индексирует «десятки тысяч порталов и сайтов агентств» в дедуплицированную базу; продукты: sourcing, AVM, рыночная аналитика, CRM, мобильное приложение, Chrome-расширение, Property Data API, MCP для AI-агентов, Data Export (дельта ежедневно, полный снимок еженедельно) — [Stream Estate, 2026](https://stream.estate/blog/casafari-alternative-review-2026)
- Заявляет 60 000+ пользователей-профессионалов; клиенты RE/MAX, Century 21, Sotheby's, CBRE, JLL — [Stream Estate, 2026](https://stream.estate/blog/casafari-alternative-review-2026)
- Alerts: ежедневные настраиваемые обновления, до 200 алертов на аккаунт; фильтры: цена, статус (продажа/продано/аренда/сдано), состояние, спальни, ванные, площадь, цена за м² — [Casafari Insights: Alerts](https://www.casafari.com/insights/gather-more-property-leads-with-casafari-get-to-know-alerts/)
- Важно: алерты описаны как «daily» — т.е. не минутная скорость (по описанию продукта) — [Casafari Insights: Alerts](https://www.casafari.com/insights/gather-more-property-leads-with-casafari-get-to-know-alerts/)
- Финансирование: 08.07.2021 — анонс «$135 млн»: $15 млн Series A (лид Prudence Holdings; Armilar, Amavi, HJM, 1Sharpe, FJ Labs, Lakestar) + $120 млн мандат от Stoneweg (не equity в компанию). На тот момент: 15 000 пользователей, 1 500 клиентов, 90 сотрудников, рынки PT/ES/FR/IT — [Casafari press](https://www.casafari.com/insights/casafari-secures-135-million-to-expand-across-europe/)
- Источник данных (2021): «проприетарная ML-технология, агрегирующая миллионы объявлений с тысяч сайтов на разных языках», 95 млн объявлений — [Casafari press](https://www.casafari.com/insights/casafari-secures-135-million-to-expand-across-europe/)
- Всего привлечено ~$20,5 млн за 3 раунда, 12 инвесторов; последний раунд — venture debt ~$5,25 млн от BBVA Spark, 27.11.2024 — [Tracxn](https://tracxn.com/d/companies/casafari/__N3y44AQGdnE1I4Dkm1_Wrh3AObazX1-vmUPuYeMZOVU/funding-and-investors)
- Альтернатива-API Stream Estate (FR/EU): от €99/мес (Starter), 1 500+ источников, 50 млн+ дедуплицированных объявлений, 50 000+ новых/день, вебхуки на создание/изменение цены, self-serve — [Stream Estate, 2026](https://stream.estate/blog/casafari-alternative-review-2026) (источник — сам конкурент, возможна предвзятость)

### Inferences
- Рост пользователей 15k (2021) → 60k+ (2026) указывает, что модель «агрегатор + алерты + аналитика для агентов» масштабируется в Европе.
- Casafari — агрегатор краулингом, т.е. та же правовая модель, что у нашего продукта; партнёрства с порталами как основной источник не упоминаются.
- Ежедневные алерты и отсутствие Telegram-канала — окно для «минутных» пуш-алертов.

### Gaps
- Реальные цены Casafari (по отзывам на G2/Capterra) не найдены; прайса нет.
- Судебных споров Casafari с порталами (напр., Idealista) не найдено.

## 2. Другие EU/глобальные игроки: Idealista/tools, PriceHubble, PropertyData (UK), Stream Estate и др.

### Takeaway
Порталы (Idealista) сами продают агентам инструменты «captación» по частникам с алертами; PriceHubble ушёл в оценку/лидогенерацию для банков и агентов без публичных цен; в UK PropertyData продаёт sourcing + алерты дёшево (£15–60/мес). Публичные self-serve цены — только у UK/API-игроков.

### Cited Findings
- Idealista/tools: «Mapa de captación» — единый список объявлений частников на продажу/аренду, сколько дней в экспозиции, уровень спроса, выставлено ли у других агентств, оценка рыночной цены и история цены; включает объявления частников «опубликованные в интернете (не только Idealista)» — [Idealista tools help](https://www.idealista.com/tools/centrodeayuda/articulos/mapa-de-captacion-de-particulares/)
- Idealista: «alertas de particulares» — уведомления агенту о новых объявлениях частников по критериям, чтобы «первым связаться с собственником» — [Idealista webinar](https://www.idealista.com/cursos/webinars/keynote/230817-alertas-de-particulares)
- Idealista защищается от скрейпинга (DataDome) — [Scrapfly](https://scrapfly.io/blog/posts/how-to-scrape-idealista)
- PriceHubble: Property Lead Engine (лидогенерация + вовлечение клиентов + advisory), Property Advisor (оценки, аналоги для mandate win-rate); цены не опубликованы — [PriceHubble Lead Engine](https://www.pricehubble.com/products/property-lead-engine); [Property Advisor](https://www.pricehubble.com/products/property-advisor)
- PropertyData (UK), цены на 24.09.2026: Basic £15/мес (20 кредитов, 2 алерта), Standard £24 (50 кр., 6 алертов), Pro £35 (80 кр., 10 алертов), Unlimited £60; годовая — 11 мес. по цене 12; API — от £28/мес — [PropertyData pricing](https://propertydata.co.uk/pricing)
- PropertyData Sourcing: поиск объектов на рынке по 32 стратегиям (напр., «требует ремонта», «только кэш», «быстрая продажа»); ежедневные email-алерты по новым возможностям — [PropertyData sourcing](https://propertydata.co.uk/sourcing); [What's new](https://propertydata.co.uk/whats-new)

### Inferences
- В Испании/Португалии портал сам монетизирует «алерты по частникам» для агентов — независимому сервису там сложнее; в наших целевых рынках крупные порталы (krisha, myhome/ss.ge, list.am) такого продукта для агентов, судя по поиску, не имеют — это стоит перепроверить.
- UK-бенчмарк: $20–80/мес за self-serve sourcing с алертами (ежедневными).

### Gaps
- Не исследованы Realadvisor, Rentola, Propstack (цены/функции не собраны — лимит времени).
- Цены Idealista/tools и PriceHubble не публичны.

## 3. США: FSBO/expired lead tools (REDX, Landvoice, Vulcan7, Espresso Agent)

### Takeaway
Зрелая модель per-agent подписки: $40–120/мес за один тип лидов на один район/MLS, $199–399/мес за бандл лидов + дайлер + CRM, доп. пользователь $75–99/мес; без setup fee, помесячно. Ценность продаётся как «список собственников с телефонами + звонилка», а не скоринг сделок.

### Cited Findings
- REDX (24.09.2026): отдельные продукты (GeoLeads, Expired, FSBO, FRBO, Pre-Foreclosure) по $60/мес ($600/год); бандлы Core $199 (все 5 типов + CRM + мейлеры), Connect $299 (+ однолинейный дайлер), Pro $349 (+ 3-линейный дайлер); доп. пользователь $75/мес (Core/Connect) или $99/мес (Pro) — [REDX pricing](https://www.redx.com/pricing/)
- Landvoice (24.09.2026): Starter $87/мес, Essential $127, Pro $177, Elite $227; à la carte: FSBO $40/мес за 2 округа, Expired Basic $60/мес за MLS, Expired Pro $120/мес за MLS, Neighborhood Search $49; setup fee нет («We think setup fees are lame»); помесячно без контракта — [Landvoice pricing](https://www.landvoice.com/pricing)
- Vulcan7: цены не публикуются, только через продажи; по сторонним оценкам 2026 ~ $359/мес за полный набор (Expired+FSBO+FRBO, 2 дайлера, CRM), только Expired (2 года истории) ~ $250/мес, при обязательстве 6–12 мес ~ $288–305/мес; диапазон тарифов $299–558 — [CloudTalk, 2026](https://www.cloudtalk.io/blog/vulcan7-pricing/); [Rezora](https://rezora.io/blog/vulcan7-pricing)
- Espresso Agent (2026): GEO $249/мес, PRO $279/мес (Expired, FSBO, FRBO, дайлер, CRM), PLATINUM $399/мес; без контракта — [Espresso Agent pricing](https://www.espressoagent.com/pricing)

### Inferences
- Ценообразование «за тип лида × за географию (округ/MLS)» напрямую переносится на модель «за регион/город-фид».
- Американские инструменты не делают сопоставление с запросами покупателей и скоринг «ниже рынка» — их лид = продавец (listing-side), не сделка для покупателя.

### Gaps
- Отзывы G2/Capterra по скорости доставки лидов не собраны.

## 4. СНГ: парсеры Avito/Циан/krisha, CRM с парсером собственников, Telegram-боты

### Takeaway
В РФ рынок переполнен и дешёвый: парсеры собственников стоят ~900–1 800 ₽/мес за регион (~$10–20), обновление каждые 1–5 минут, фильтр агентов, Telegram-бот; CRM (JoyWork) встраивают парсер + автоподбор по заявкам с ежеминутным сканированием при цене 89–2 000 ₽ за сотрудника/мес. В Казахстане/Грузии — в основном самописные/GitHub-боты и фриланс, заметных коммерческих SaaS не найдено.

### Cited Findings
- «Квадратные метры»: собирает объявления собственников с Циан, Авито, Яндекс Недвижимость, Юла, SOB.RU, Домклик; обновление «каждые пять минут»; фильтр рекламы агентов; Telegram-бот; API; первые 15 объявлений бесплатно; прайс на лендинге не указан — [kvadratnyemetry.com](https://www.kvadratnyemetry.com/)
- INPARS (24.09.2026): 149 ₽/сутки, 349 ₽/неделя, 899 ₽/мес, 1 599 ₽/2 мес, 2 299 ₽/3 мес, 4 049 ₽/6 мес; для Москвы ×2; 80+ регионов; категории продажа/аренда/обе; «новые объявления каждые 60 секунд», фильтр агентов, API — [inpars.ru/pay](https://inpars.ru/pay); [inpars.ru](https://inpars.ru/)
- ATRealt: 1 500 ₽/мес за регион на всю компанию; 12 площадок (Авито, Циан, Яндекс, Домклик, Юла, Move.ru и региональные); дедупликация; активация через Telegram-поддержку — [atrealt.ru/parser](https://atrealt.ru/parser/)
- Ads-api.ru: выгрузка базы — 100 ₽ за 2 500 объявлений, опт дешевле; API — [сводка поиска по ads-api.ru](https://ads-api.ru/) (страница при прямом запросе не загрузилась, цифра из сниппета поиска — проверить)
- JoyWork (СПб, с 2016): тарифы за сотрудника/мес — от 2 000 ₽ (Старт, до 3 чел.) до 89 ₽ (Бизнес+, до 1 001 чел.); напр., 619 ₽ до 20 чел., 297 ₽ до 101 чел. — [JoyWork тарифы](https://joywork.ru/tariff.php)
- JoyWork парсер: Авито, Циан + другие; разделение на собственников/агентов/застройщиков; перепроверка актуальности; «автопоиск»: система «ежеминутно сканирует все площадки» и доставляет агенту подходящие объекты (подбор под заявки) — [JoyWork парсер](https://joywork.ru/parser-nedvizhimosti.php)
- JoyWork: 200+ проверенных объектов собственников ежедневно, 265 000+ в архиве; триал 7 дней — [сводка поиска по joywork.ru](https://joywork.ru/parser-nedvizhimosti.php)
- В подборке CRM 2026 упоминается CRM с парсером собственников и автоподбором за 229 ₽/мес за пользователя («Стартовый») — [DTF, 2026](https://dtf.ru/luchshii-rating/3591049-top-17-srm-crm-dlya-agentstva-nedvizhimosti-vybor-polzovatelei-v-2026-godu) (название CRM в сниппете не указано — проверить)
- Также на рынке: Sigma Parser («мгновенный парсер», Авито и Циан) — [sigmaparser.ru](https://sigmaparser.ru/) (страница не отрисовалась, цены не получены); HomeCRM парсер Циан — [HomeCRM](https://homecrm.ru/blog/parser-cian)
- Казахстан (krisha.kz): найдены только GitHub-парсеры/боты, десктопные парсеры (Datacol, Авто-Парсер.ру) и сбор телефонов — [GitHub andprov/krisha.kz](https://github.com/andprov/krisha.kz/blob/main/README.ru.md); [Datacol](https://web-data-extractor.net/parser-krisha-kz/); [Авто-Парсер](https://auto-parser.ru/parser_krisha_kz)
- Фриланс-рынок: Telegram-бот парсинга досок объявлений на Kwork от 1 000 ₽ — [Kwork](https://kwork.ru/script-programming/275541/telegram-bot-dlya-parsinga-dvukh-izvestnykh-dosok-obyavleniy-avt-yla)
- Грузия (myhome.ge, ss.ge, korter.ge): только open-source/любительские боты; один — «DealRadar Georgia»: радар покупки/аренды в реальном времени с deal score, картой, объявлениями собственников и Telegram-алертами — [GitHub georent](https://github.com/romangalaxys10-spec/georent); другие — [sandrikkk/real-estate](https://github.com/sandrikkk/real-estate) (фильтр по инвест-критериям, алерты в Telegram), [flat-alert-bot](https://github.com/DanonDefoe/flat-alert-bot), Telegram-канал [Tbilisi Rent Feed](https://t.me/rentfeed_tbilisi_myhomege)

### Inferences
- РФ-бенчмарк: $10–20/мес за регион-фид собственников (INPARS 899 ₽ ≈ $11; ATRealt 1 500 ₽ ≈ $18; Москва ×2); CRM с парсером — $1–25 за сотрудника/мес. Цены в РФ задают очень низкий «потолок» для СНГ-агентств.
- Скорость 1–5 минут и фильтр «только собственники» в РФ — уже стандарт (commodity), не дифференциатор.
- В Грузии появились самописные «deal score»-радары — сигнал, что идея скоринга лежит на поверхности, но коммерческого продукта для агентств не видно.

### Gaps
- Не найдены: Intrum, ReBPM, «Сова» — тарифы и наличие парсера не проверены.
- Коммерческие SaaS для krisha.kz (KZ), list.am (AM), Балкан (4zida, Halo Oglasi, njuskalo) не найдены — поиск не проводился/не дал результатов по Балканам.
- Курс ₽/$ на 09.2026 не сверен с источником.

## 5. Типичные модели ценообразования и диапазоны

### Takeaway
Три модели: (1) per-agent seat (США $87–399/мес; CRM СНГ $1–25/мес за сотрудника), (2) per-region/per-feed на компанию (РФ ~$10–20/мес за регион; США $40–120 за округ/MLS), (3) enterprise/по запросу (Casafari, PriceHubble, Vulcan7). Setup fees в сегменте практически отсутствуют; managed service публичных цен не имеет.

### Cited Findings
- Per-area: Landvoice FSBO $40/2 округа, Expired $60–120/MLS — [Landvoice](https://www.landvoice.com/pricing); ATRealt 1 500 ₽/регион на компанию — [ATRealt](https://atrealt.ru/parser/); INPARS 899 ₽/мес, Москва ×2 — [INPARS](https://inpars.ru/pay)
- Per-seat доп. пользователь: REDX $75–99/мес — [REDX](https://www.redx.com/pricing/); JoyWork — за сотрудника с объёмными скидками (2 000 → 89 ₽) — [JoyWork](https://joywork.ru/tariff.php)
- Без setup fee: Landvoice явно — [Landvoice](https://www.landvoice.com/pricing); помесячно без контрактов: Espresso Agent — [Espresso](https://www.espressoagent.com/pricing)
- По запросу: Casafari — [Stream Estate](https://stream.estate/blog/casafari-alternative-review-2026); Vulcan7 — [CloudTalk](https://www.cloudtalk.io/blog/vulcan7-pricing/)
- Self-serve sourcing UK: £15–60/мес — [PropertyData](https://propertydata.co.uk/pricing); API EU: от €99/мес — [Stream Estate](https://stream.estate/blog/casafari-alternative-review-2026)

### Inferences
- Для наших рынков разумно: фид «город/регион» на агентство + доплата за места агентов; ориентир между РФ-дном ($10–20) и US/UK ($40–350) — цену обосновывать скорингом/подбором, а не самим фидом.

### Gaps
- Нет данных о ценах managed-service (настройка/ведение под ключ) у кого-либо.

## 6. Незакрытые ниши: матчинг с заявками покупателей, скорость (минуты), скоринг против медианы

### Takeaway
Скорость (1–5 мин) и фильтр собственников уже есть в РФ; подбор под заявки есть в CRM (JoyWork «автопоиск»). Но комбинация «оценка цены против медианы района / флаг “ниже рынка” + матчинг с заявкой покупателя + Telegram-пуш “звони сейчас”» как коммерческий продукт для агентств в найденных источниках не встречается; ближайшие — Idealista (оценка цены у частников, только ES/PT/IT, внутри портала) и любительский DealRadar Georgia.

### Cited Findings
- Скорость: INPARS — 60 сек — [INPARS](https://inpars.ru/); «Квадратные метры» — 5 мин — [kvadratnyemetry.com](https://www.kvadratnyemetry.com/); JoyWork — ежеминутно — [JoyWork](https://joywork.ru/parser-nedvizhimosti.php); Casafari — ежедневные алерты — [Casafari Alerts](https://www.casafari.com/insights/gather-more-property-leads-with-casafari-get-to-know-alerts/); PropertyData — ежедневные email — [PropertyData](https://propertydata.co.uk/whats-new)
- Подбор под заявки: JoyWork автопоиск — [JoyWork](https://joywork.ru/parser-nedvizhimosti.php)
- Оценка цены: Idealista «Mapa de captación» даёт оценку рыночной цены и историю цены объектов частников — [Idealista](https://www.idealista.com/tools/centrodeayuda/articulos/mapa-de-captacion-de-particulares/); Casafari — фильтр цены за м², AVM — [Casafari Alerts](https://www.casafari.com/insights/gather-more-property-leads-with-casafari-get-to-know-alerts/); PropertyData — стратегия «ниже рынка»-подобные (unmodernised, quick sale) — [PropertyData sourcing](https://propertydata.co.uk/sourcing)
- Deal score + Telegram в Грузии — open-source — [GitHub georent](https://github.com/romangalaxys10-spec/georent)

### Inferences
- Дифференциатор — не парсинг и не скорость, а (а) скоринг «ниже медианы района» и (б) матчинг с конкретной заявкой покупателя с готовым «почему звонить» в Telegram; и (в) покрытие рынков, где нет ни Casafari, ни РФ-CRM (Армения, Грузия, KZ/UZ, Балканы).
- Риск: РФ-сервисы (JoyWork, INPARS) могут добавить скоринг и выйти на KZ; open-source боты снижают барьер входа.

### Gaps
- Не проверено, есть ли у JoyWork/«Квадратных метров» скоринг цены против рынка (на страницах не упомянут).
- Не проверено наличие аналогов в Турции/Сербии/Узбекистане.
