# SpotBot SaaS — Журнал разработки

## 1. Цель проекта

Перенести локального торгового бота **SpotBot‑RAV‑Trade (Python + Tkinter)** в SaaS‑сервис с веб‑интерфейсом и сервисной архитектурой.

Целевая архитектура:

Browser
→ portal-ui
→ portal-api
→ trader service
→ binance-gateway
→ Binance

Ключевой принцип:

**Торговый алгоритм нельзя переписывать.**

Мы заменяем только GUI на SaaS‑оболочку.

---

# 2. Архитектура системы

## Сервисы

### portal-ui

Frontend кабинет пользователя.

Технологии:

* Nginx
* HTML
* Vanilla JS
* TradingView widget

Основные файлы:

portal-ui/site/index.html
portal-ui/site/js/app.js
portal-ui/site/assets/rt-app.css

Функции:

* Dashboard
* TradingView анализ
* Drawer Create / Manage
* управление торговыми парами
* отображение баланса
* статус API

---

### portal-api

Backend сервис.

Технологии:

* FastAPI
* PostgreSQL

Файлы:

portal-api/app/main.py
portal-api/app/pairs.py
portal-api/app/pairs_schemas.py
portal-api/app/models.py
portal-api/app/db.py

Функции:

* авторизация
* роли пользователей
* хранение Binance API ключей
* управление парами
* запуск/остановка торговли

---

### binance-gateway

Прокси‑слой между системой и Binance API.

Технологии:

* FastAPI

Файл:

binance-gateway/app/main.py

Основные endpoints:

GET /price
POST /usdc-balance
POST /order

Проверка:

curl [http://127.0.0.1:8001/health](http://127.0.0.1:8001/health)

---

### trader (будущий сервис)

Расположение:

trader/spotbot-trader.py

Функции:

* запуск торговой стратегии
* управление сеткой ордеров
* обработка торговых циклов

Ключевое правило:

**Алгоритм должен остаться идентичным оригинальному GUI боту.**

---

# 3. Docker инфраструктура

Структура проекта:

spotbot-saas

binance-gateway
portal-api
portal-ui
trader
postgres
cloudflared

Проверка контейнеров:

```
docker compose ps
```

Ожидаемые сервисы:

spotbot-binance-gateway
spotbot-portal-api
spotbot-portal-ui
spotbot-postgres
spotbot-cloudflared

---

# 4. Команды проверки работы сервисов

Portal API:

```
curl http://127.0.0.1:8000/health
```

UI через nginx:

```
curl http://127.0.0.1:8080/api/health
```

Gateway:

```
curl http://127.0.0.1:8001/health
```

Получение цены:

```
curl "http://127.0.0.1:8000/market/price?symbol=BTCUSDC"
```

Проверка API пар:

```
curl -H "Cf-Access-Authenticated-User-Email: USER_EMAIL" \
http://127.0.0.1:8000/pairs
```

---

# 5. Авторизация

Используется **Cloudflare Access**.

Email пользователя передается в заголовке:

Cf-Access-Authenticated-User-Email

Использование в backend:

```
request.headers["Cf-Access-Authenticated-User-Email"]
```

---

# 6. Модель базы данных

Таблица: pairs

Поля:

id
owner_email
symbol
base_asset
quote_asset
strategy
status
is_enabled
config JSON
last_error

---

# 7. API endpoints

Pairs:

GET /pairs
POST /pairs
PATCH /pairs/{id}
DELETE /pairs/{id}

Управление торговлей:

POST /pairs/{id}/start
POST /pairs/{id}/stop

---

# 8. Структура Dashboard

Основные блоки:

TradingView
Volume
Pairs
API
Balance

---

# 9. Drawer Create

Используется для создания торговой пары.

Поля:

Пара
Первый ордер
Шаги %
Мартингейл %
Резерв в торги
Трейлинг старт %
Шаг трейлинга %
SL %
TP %

Вычисляемые значения:

Пример:

0.0002 BTC → 13.42 USDC

Расчет резерва учитывает:

* мартингейл
* количество шагов
* текущую цену

---

# 10. Обновление цены

Интервал обновления:

5 секунд

Механизм:

setInterval → fetch price → recalc reserve

---

# 11. Логика Live Dot

Красная пульсация:

Рынок доступен
но условия для запуска не выполнены

Зелёная пульсация:

Все параметры заполнены
Баланс достаточный

---

# 12. Кнопки Drawer Create

Save
Start ×1
Auto Trade

Условия активации:

Все поля заполнены
Баланс достаточный

Tooltip при блокировке:

"Недостаточно баланса или настройки незаполнены"

---

# 13. Drawer Manage

Отображает:

Symbol
Status
Настройки стратегии
Резерв

---

# 14. Отображение статусов

idle → Настройка
active → Активна
stopped → Остановлена

---

# 15. Кнопки Manage

Save
Start ×1
Auto Trade
Stop
Delete

---

# 16. Логика кнопок

### idle

Save → активна
Start → если хватает баланса
Auto → если хватает баланса
Stop → неактивна
Delete → активна

### active

Save → активна
Start → неактивна
Auto → неактивна
Stop → активна
Delete → неактивна

### stopped

Save → активна
остальные → неактивны

---

# 17. Важное CSS правило

Disabled кнопки должны переопределять системные стили:

.button--sys:disabled

Причина:

избежать ложного hover‑эффекта.

---

# 18. Интеграция TradingView

TradingView **не должен перезагружаться** при смене пары.

Причина:

Перезагрузка удаляет:

* линии пользователя
* фигуры
* анализ

Решение:

Меняем symbol без пересоздания виджета.

---

# 19. План масштабируемости

Будущая архитектура:

portal-ui → CDN
portal-api → горизонтальное масштабирование
trader → пул воркеров
binance-gateway → stateless API

Возможная схема:

Load balancer
→ несколько portal-api
→ несколько trader workers

Рекомендуемая очередь:

Redis
или
RabbitMQ

---

# 20. Отказоустойчивость

Рекомендуемые улучшения:

Retry для Binance
защита от API timeout
watchdog для trader
reconciliation ордеров

Каждый сервис должен иметь health endpoint.

---

# 21. Дополнения после создания журнала

## 21.1 Cloudflare Access и onboarding

Выявлено важное ограничение onboarding:

Если Cloudflare Access policy блокирует пользователя раньше входа в приложение, то пользователь:

* не доходит до portal-ui
* не вызывает /whoami
* не создаётся в БД автоматически
* не появляется в админке как запросивший доступ

Практический вывод:

**Одобрение пользователя в БД и допуск в Cloudflare — это два разных шага.**

### Текущая рабочая схема

1. Пользователь добавляется/одобряется во внутренней системе
2. Email пользователя должен быть разрешён в Cloudflare Access policy
3. Только после этого пользователь реально попадает в приложение

### Проверенный сценарий

Для пользователя `olesja.elwein@gmail.com`:

* пользователь успешно появился в `admin/users`
* имел статус `approved_no_keys`
* но всё равно видел экран Cloudflare `That account does not have access`
* после добавления email в Cloudflare policy вход начал проходить

### Важный архитектурный вывод

Для полноценного SaaS лучше перейти к модели:

Cloudflare → Authentication
portal-api → Authorization

То есть Cloudflare должен аутентифицировать пользователя, а статусы `requested / approved_no_keys / active / denied` должны управляться внутри системы.

### Рекомендованная будущая Cloudflare policy

Вместо ручного allowlist по email:

* Allow authenticated users via Google login

Тогда onboarding станет таким:

Login via Cloudflare
→ /whoami
→ если пользователя нет, создать `requested`
→ admin approve
→ доступ к SaaS

---

## 21.2 Проверка onboarding через терминал

Проверено, что добавление пользователя можно симулировать через терминал.

### Запрос доступа

```bash
curl -sS -X POST \
-H "Cf-Access-Authenticated-User-Email: test.user@email.com" \
http://127.0.0.1:8000/request-access | jq .
```

### Проверка пользователя

```bash
curl -sS \
-H "Cf-Access-Authenticated-User-Email: test.user@email.com" \
http://127.0.0.1:8000/whoami | jq .
```

### Список пользователей как admin

```bash
curl -sS \
-H "Cf-Access-Authenticated-User-Email: zapan.rozhkov@gmail.com" \
http://127.0.0.1:8000/admin/users | jq .
```

### Практический вывод

Admin endpoints требуют Cloudflare email header. Без него backend возвращает:

```json
{"detail":"No email from Cloudflare Access header"}
```

---

## 21.3 Рекомендованный future onboarding flow для SaaS

Рекомендована архитектура onboarding через landing page и request-access flow:

Landing / Trading page
→ Request Access form
→ таблица `access_requests`
→ Admin review
→ optional payment in USDC
→ допуск в Cloudflare
→ login в app
→ add API keys
→ verify keys
→ active

### Рекомендуемые статусы access request

* new
* reviewing
* approved_free
* approved_waiting_payment
* paid_waiting_verification
* invited
* denied

### Рекомендуемые внутренние user statuses

* requested
* approved_no_keys
* keys_pending_verification
* active
* denied

### Рекомендованные сущности

* `access_requests`
* `payments` (или payment fields внутри access_requests)

Это позволит позже подключить оплату доступа в USDC без ломки архитектуры.

---

## 21.4 Drawer mobile scrolling fix

После создания журнала была выявлена проблема мобильной версии:

* нижняя часть Drawer на телефоне не была видна
* кнопки Create/Manage уходили за экран
* Drawer не скроллился как единый контейнер

### Причина

В CSS одновременно существовали две разные реализации Drawer:

1. старая структура (`.rt-drawer__panel`, `.rt-drawer__left`, `.rt-drawer__right`)
2. новая реальная структура (`.rt-drawer`, `.rt-drawer__header`, `.rt-drawer__body`)

Старая реализация больше не использовалась HTML, но продолжала конфликтовать со стилями.

### Исправление

* удалена старая CSS-реализация Drawer
* оставлена одна активная схема
* скролл перенесён на `.rt-drawer__body`
* добавлена корректная мобильная высота (`100dvh`)
* добавлен нижний запас под кнопки и safe-area

### Практический результат

* Drawer Create и Drawer Manage начали корректно скроллиться на мобильном
* нижние кнопки стали доступны
* поведение стало одинаковым на desktop и mobile

### Важное правило

В проекте должна существовать только **одна** CSS-реализация Drawer.
Старые классы:

* `.rt-drawer__panel`
* `.rt-drawer__left`
* `.rt-drawer__right`

считаются legacy и больше не используются.

---

# 22. Дополнения из чата 3.0

## 22.1 System Map v2

Архитектурное описание проекта было приведено к более зрелому виду.

Система разложена на следующие плоскости:

* Presentation Plane
* Control Plane
* Persistence Plane
* Execution Plane
* Integration Plane

Также отдельно были зафиксированы:

* trust boundaries
* data ownership
* key flows
* source of truth

### Практический результат

Архитектура стала описана не как набор сервисов, а как зрелая SaaS-система, где:

* `portal-ui` = presentation plane
* `portal-api` = control plane
* `trader` = execution plane
* `binance-gateway` = integration layer
* Binance = внешний источник фактической торговой истины

---

## 22.2 Исправление Mermaid / схем документации

Была обнаружена syntax error в одной из Mermaid-схем.

Что сделано:

* выявлена причина ошибки
* проблемный синтаксис заменён
* подготовлена безопасная версия схемы для хранения в `docs`

### Результат

Схемы можно хранить в документации без проблем с рендерингом.

---

## 22.3 Tooltip-подсказки для Create Drawer

В Drawer Create были добавлены подсказки для disabled-кнопок:

* `Start ×1`
* `Auto Trade`

Используется объединённый текст:

`Недостаточно баланса или настройки незаполнены`

### Что сделано

В `app.js`:

* доработана `updateDrawerActionState()`
* добавлены `title` для disabled-кнопок

В `rt-app.css`:

* исправлен `pointer-events` для disabled-кнопок, чтобы браузер мог показывать tooltip

### Результат

Create Drawer теперь по UX ведёт себя так же понятно, как Manage Drawer.

---

## 22.4 Runtime status model: idle / active_cycle / waiting_close

Было зафиксировано важное различие между persisted status и runtime status.

### Проблема

`pair.status` в БД (`idle / active / stopped`) не равен реальному состоянию торгового цикла.

### Введена runtime-модель

* `idle`
* `active_cycle`
* `waiting_close`

### Семантика

* `idle` — можно запускать новый цикл
* `active_cycle` — цикл торговли активен
* `waiting_close` — stop уже запрошен, но бот ещё завершает сделку / ордера

### Результат

Эта модель стала основой для:

* логики кнопок
* live-dot индикации
* будущего блока средней цены
* будущего trading log
* будущей кнопки `+ Order`

---

## 22.5 Backend endpoint: GET /pairs/{id}/runtime

Для перехода на runtime-aware UI был добавлен backend-контракт:

`GET /pairs/{pair_id}/runtime`

### Реализовано в portal-api

* `RuntimeStatus`
* `PairRuntimeResponse`
* helper для сборки runtime response
* временный runtime bridge

### Временный mapping

* `pair.status = idle` → `runtime_status = idle`
* `pair.status = active` → `runtime_status = active_cycle`
* `pair.status = stopped` → `runtime_status = waiting_close`

### Результат

UI получил корректный runtime API ещё до полной интеграции с живым trader-service registry.

---

## 22.6 Подключение runtime в app.js

Во frontend были добавлены функции:

* `fetchPairRuntime(pairId)`
* `applyRuntimeToPair(pair, runtime)`
* `refreshManagePairRuntime(pair)`

### Результат

Manage Drawer стал работать уже не только по persisted status, но и по runtime-контракту.

---

## 22.7 Runtime-aware логика кнопок Manage Drawer

`updateManageActionState(pair)` была переведена на работу через `runtime_status` с fallback на `pair.status`.

### Правила

#### idle

* Save → enabled
* Start ×1 → enabled если валидны настройки и хватает баланса
* Auto Trade → enabled если валидны настройки и хватает баланса
* Stop → disabled
* Delete → enabled

#### active_cycle

* Save → enabled
* Start ×1 → disabled
* Auto Trade → disabled
* Stop → enabled
* Delete → disabled

#### waiting_close

* Save → enabled
* Start ×1 → disabled
* Auto Trade → disabled
* Stop → disabled
* Delete → disabled

### Результат

Manage Drawer стал runtime-aware, а не просто формой, завязанной на БД-статус.

---

## 22.8 Live-dot в Manage Drawer

В Manage Drawer рядом со `Status` добавлен runtime-индикатор:

```html
<span id="rt-m-runtime-dot" class="rt-live-dot is-off" title="Состояние недоступно"></span>
```

В `app.js` добавлена логика `applyRuntimeDotState(...)`.

В `rt-app.css` добавлены состояния:

* `is-on`
* `is-off`
* `is-wait`

### Семантика цвета

* зелёный = `active_cycle`
* красный = `idle`
* жёлтый = `waiting_close`

### Результат

Состояние пары стало видно глазами, а не только через текст.

---

## 22.9 Live-dot на кнопке пары в списке Pairs

Live-dot был добавлен не только в Drawer, но и на саму кнопку пары в блоке `Pairs`.

### Что сделано

В `renderPairsTiles(pairs)` кнопка пары стала рендериться как:

* label
* dot

с применением:

`applyRuntimeDotState(dot, p.runtime_status, p.status)`

В CSS доработан `.rt-tile`, чтобы:

* текст был слева
* точка была справа

### Результат

Каждая пара в списке получила собственный live indicator.

---

## 22.10 Исправление SAVE в Create Drawer

Была найдена ошибка: `SAVE` мог ломаться из-за отсутствующего DOM-элемента `rt-cfg-oco-enabled`.

### Причина

В payload использовалось:

`oco_enabled: !!$("rt-cfg-oco-enabled").checked`

При отсутствии элемента происходил сбой.

### Исправление

Применён безопасный вариант:

`oco_enabled: !!$("rt-cfg-oco-enabled")?.checked`

### Результат

Сохранение пары в Create Drawer снова стало стабильным.

---

## 22.11 Исправление рассинхрона кнопок Start/Auto в Create Drawer

Был найден баг:

Если пользователь сначала заполнил поля, кнопки стали активны, а потом удалил часть значений, reserve-dot уже краснел, но кнопки могли временно оставаться активными.

### Причина

В `recalcReserve()` на ветках раннего `return` не вызывался `updateDrawerActionState()`.

### Исправление

`updateDrawerActionState()` добавлен во все ветки раннего выхода.

### Результат

Create Drawer теперь всегда синхронно обновляет:

* reserve-dot
* доступность Start ×1 / Auto Trade

---

## 22.12 Санитайзинг полей 1-в-1 для Manage Drawer

В Manage Drawer были добавлены те же input sanitizers, что и в Create Drawer.

### Охваченные поля

* `rt-m-pair-base`
* `rt-m-cfg-qty`
* `rt-m-step-*`
* `rt-m-cfg-mart`
* `rt-m-cfg-oco-trigger`
* `rt-m-cfg-oco-step`
* `rt-m-cfg-sl`
* `rt-m-cfg-tp`

### Результат

Create и Manage теперь работают по одинаковым правилам ввода.

---

## 22.13 Исправление feedback-message в Drawer

Была приведена в порядок система сообщений типа:

* `Пара сохранена`
* `Настройки сохранены`

### Изначальные проблемы

* сообщение могло уезжать
* иногда отображалась одна буква
* сообщение мигало и исчезало

### Причины

1. message-контейнер был вставлен внутрь grid кнопок
2. сообщение стиралось повторным `openDrawerManage()` / `openDrawerCreate()`

### Исправления

В `index.html` разделены контейнеры:

* `rt-drawer-msg-create`
* `rt-drawer-msg-manage`

В `app.js` доработан `drawerEls()`:

* `msgCreate`
* `msgManage`
* корректный выбор `msg` по активному режиму

### Результат

Feedback сообщения в drawer получили корректный DOM-контекст и стали вести себя предсказуемо.

---

## 22.14 Разделение кнопок Manage Drawer на 2 ряда

Блок управления в Manage Drawer был переработан.

### Было

Один ряд из 5 кнопок:

* SAVE
* START ×1
* AUTO TRADE
* STOP
* DELETE

### Стало

Верхний ряд:

* SAVE
* DELETE

Нижний ряд:

* START ×1
* AUTO TRADE
* STOP

### Результат

Управление стало визуально чище и понятнее.

---

## 22.15 Новые валидационные правила для S/L % и T/P %

Были введены продуктовые правила прибыли.

### Зафиксированные правила

Для `S/L %`:

* `SL < Трейлинг старт`

Для `T/P %`:

* `TP > Трейлинг старт`

### Что добавлено

В `app.js`:

* `parseNum()`
* `validateCreateProfitRules()`
* `validateManageProfitRules()`

В `rt-app.css`:

* `.rt-input-error`

В логике кнопок:

* `validProfitRules` подключён в `updateDrawerActionState()`
* `validProfitRules` подключён в `updateManageActionState(pair)`

### Результат

Некорректные profit rules:

* подсвечиваются рамкой
* дают tooltip
* блокируют старт торговли

---

## 22.16 Исправление payload для sl_pct

Была найдена опасная ошибка: fallback для `sl_pct` случайно стал текстовой подсказкой, а не числом.

### Исправление

Возвращён нормальный числовой fallback:

`sl_pct: "0.5"`

### Результат

Payload снова стал корректным и числовым.

---

## 22.17 Синхронизация reserve-dot с profit rules

Был найден рассинхрон:

* кнопки уже учитывали profit rules
* reserve-dot в Create/Manage ещё не всегда использовал ту же логику

### Причина

В `recalcManageReserve()` использовался `validProfitRules`, но он не был объявлен внутри функции.

### Исправление

Добавлено:

`const validProfitRules = validateManageProfitRules();`

и включено в расчёт ready.

Аналогично синхронизировано и в Create Drawer.

### Результат

Теперь:

* reserve-dot
* доступность кнопок

используют одну и ту же readiness logic.

---

## 22.18 Улучшение входа в кабинет: авто-проверка сохранённых API ключей

Было зафиксировано улучшение onboarding/entry UX.

### Исходное поведение

Пользователь видел, что ключи сохранены, но всё равно должен был вручную нажимать `Проверить подключение`, чтобы получить баланс и зелёную точку.

### Решение

Если у пользователя уже есть сохранённый `api_key_hint`, UI должен автоматически запускать проверку подключения при входе.

### Подготовленный подход

Концептуально были спроектированы:

* `setCollapsibleState(...)`
* `tryAutoVerifyKeys(me)`
* встраивание этого сценария в `main()`

### Ожидаемый результат

После входа пользователь сразу видит живой кабинет:

* баланс загружается автоматически
* API card может автоматически сворачиваться
* балансовая точка становится зелёной без лишнего клика

---

## 22.19 Что особенно важно зафиксировать в документации

Обязательные правила проекта:

1. **Runtime vs Persisted state**
   `pair.status != runtime_status`

2. **Цвета live-dot**
   green = active_cycle
   red = idle
   yellow = waiting_close

3. **Profit rules**
   `SL < trailing start`
   `TP > trailing start`

4. **Drawer feedback**
   Create и Manage используют разные message containers

5. **UI readiness model**
   Кнопки и reserve-dot обязаны использовать одну и ту же readiness logic

6. **Auto key verification**
   Если сохранён `api_key_hint`, dashboard должен пытаться автоматически проверить подключение при загрузке

---

# 23. Дополнения из текущего этапа: runtime v2, trader service, gateway contract

## 23.1 PairRuntimeResponse v2

В `portal-api/app/pairs_schemas.py` расширен `PairRuntimeResponse`.

### Было

Только базовые поля:

* `pair_id`
* `runtime_status`
* `position_qty`
* `avg_price`
* `open_orders`
* `last_event`

### Стало

Добавлены runtime v2 поля:

* `is_running`
* `auto_cycle`
* `stop_cycle_requested`
* `oco_enabled`
* `session_position_qty`
* `avg_entry_price`
* `oco_active`
* `oco_step_index`
* `last_oco_order_list_id`
* `force_trailing_refresh`
* `grid_in_progress`
* `oco_busy`
* `grid_started_at_ms`
* `oco_gate_since_ts`
* `last_oco_set_at_ms`
* `updated_at_ms`
* `can_start_once`
* `can_start_auto`
* `can_stop`
* `can_delete`
* `can_add_order`
* `can_refresh_trailing`
* `last_error`

### Смысл

Schema подготовлена под:

* живой runtime из trader
* `+ Order`
* average price / position block
* trading log
* capability flags как source of truth для UI

---

## 23.2 Runtime bridge v2 в portal-api/app/pairs.py

Функция `build_runtime_response(p)` была расширена.

### Что делает сейчас

Пока ещё не читает живой runtime из trader, но уже строит `PairRuntimeResponse v2` на основе:

* `p.status`
* `p.config_json`
* bridge mapping

### Mapping

* `idle` → `runtime_status = idle`
* `active` → `runtime_status = active_cycle`
* `stopped` → `runtime_status = waiting_close`
* иначе → `error`

### Уже вычисляются capability flags

* `can_start_once`
* `can_start_auto`
* `can_stop`
* `can_delete`
* `can_add_order`
* `can_refresh_trailing`

### Важное изменение

`can_add_order` разрешён в:

* `active_cycle`
* `waiting_close`

Потому что пользователь должен иметь возможность усредниться даже после нажатия `Stop`, пока цикл ещё не завершён.

---

## 23.3 Endpoint /pairs/{id}/add-order в portal-api

Добавлен новый endpoint:

`POST /pairs/{pair_id}/add-order`

### Текущее состояние

Это пока контрактный endpoint.

Он:

* проверяет ownership пары
* строит runtime response
* проверяет `runtime.can_add_order`
* если нельзя → `409 Add order is not available for this pair state`
* если можно → возвращает runtime response

### Важная оговорка

Реального вызова trader service пока ещё нет.
Это endpoint-контракт и runtime-validation слой, но ещё не execution bridge.

---

## 23.4 UI: Manage Drawer переведён на runtime v2 capability flags

В `portal-ui/site/js/app.js`:

* `applyRuntimeToPair(pair, runtime)` теперь приклеивает не только старые runtime-поля, но и все новые v2 поля
* `updateManageActionState(pair)` переведён на использование backend capability flags (`can_*`) как primary source of truth
* локальная проверка `reserve/balance/profit-rules` оставлена как дополнительная UI-защита

### Принцип

Backend/runtime говорит UI:

* можно ли Start
* можно ли Stop
* можно ли Delete
* можно ли Add Order

UI сверху добавляет:

* tooltip
* визуальную блокировку
* локальную проверку готовности к старту

---

## 23.5 Runtime block в Manage Drawer

В `portal-ui/site/index.html` добавлен блок:

`Состояние сделки`

### Поля

* Режим цикла
* Stop запрошен
* Средняя цена
* Объём позиции
* Открытые ордера
* Последнее событие

В `app.js` добавлена функция `renderManageRuntimeInfo(pair)`.

### Поведение

Блок рендерится из runtime данных, но пока часть значений может быть `—`, потому что живой trader runtime ещё не подключён.

---

## 23.6 Runtime block visibility rule

Блок `Состояние сделки` должен быть виден только когда пара находится в runtime-active состояниях:

* `active_cycle`
* `waiting_close`

Не показывается в:

* `idle`

Это же правило зафиксировано как правило для будущих элементов:

* `+ Order`
* `Лог событий`

В `app.js` для этого добавлена логика `updateManageRuntimeVisibility(pair)`.

---

## 23.7 + Order UI contract

В Manage Drawer начата реализация `+ Order`.

### Бизнес-правило

Кнопка `+ Order` должна показываться, когда:

* `runtime_status === active_cycle` ИЛИ `runtime_status === waiting_close`
* `can_add_order === true`

Не показывается, когда:

* `idle`

### Причина

Даже если пользователь нажал `Stop`, цикл может ещё жить. Значит усреднение (`+ Order`) всё ещё может быть осмысленным.

---

## 23.8 Trader service поднят как отдельный контейнер

В `docker-compose.yml` сервис `trader` уже был предусмотрен.

В текущем этапе реально созданы файлы:

* `trader/Dockerfile`
* `trader/requirements.txt`
* `trader/app/main.py`

И сервис успешно поднят.

### Проверка

`GET /health` на `127.0.0.1:8002` отвечает корректно.

### Дополнительная проверка

`trader/app/main.py` уже умеет импортировать из `spotbot_trader.py`:

* `PairConfig`
* `SpotBotTrader`

Это подтверждает, что движок физически доступен внутри trader-container.

---

## 23.9 Что уже известно про engine contract из spotbot-trader.py

### PairConfig

Подтверждено, что `PairConfig` содержит все ключевые параметры стратегии:

* `base_asset`
* `quote_asset`
* `qty_start_base`
* `martingale_pct`
* `layers_pct`
* `tp_pct`
* `sl_pct`
* `oco_trigger_pct`
* `oco_step_pct`
* `add_order_pct`
* `poll_sec`
* `soft_cutoff_ms`
* `grid_start_delay_ms`
* `oco_enabled`
* `auto_cycle`
* `stop_cycle_requested`
* `max_open_orders`

### SpotBotTrader lifecycle

Подтверждено наличие:

* `start()`
* `stop()`
* `_run()`
* `_place_grid()`
* `_oco_monitor_tick()`
* `refresh_trailing()`

### Runtime flags внутри движка

Подтверждено наличие:

* `grid_started_at_ms`
* `oco_gate_since_ts`
* `oco_active`
* `oco_step_index`
* `last_oco_order_list_id`
* `_force_trailing_refresh`
* `_grid_in_progress`
* `_oco_busy`
* `_last_oco_set_at_ms`

Это означает, что живой runtime уже есть в engine и later должен стать source of truth для `/runtime/{pair_id}`.

---

## 23.10 ExchangeAdapter contract зафиксирован

Из `spotbot-trader.py` подтверждён точный integration contract.

### Minimum required for GRID parity

* `get_exchange_info(symbol)`
* `get_price(symbol)`
* `get_open_orders(symbol)`
* `get_asset_balance(asset)`
* `place_limit_buy(symbol, price, qty_base)`

### Required for full OCO parity

* `get_open_oco_orders()`
* `cancel_oco_order_list(order_list_id)`
* `place_oco_sell(symbol, qty, tp_stop, tp_limit, sl_stop, sl_limit)`
* `get_my_trades(symbol, start_time_ms)`

### Уже существует класс

`BinanceGatewayAdapter(ExchangeAdapter)`

Но его ожидаемый gateway contract на текущем этапе ещё не полностью совпадает с реальным `binance-gateway`.

---

## 23.11 Binance gateway: текущая зрелость и contract gap

Актуальный `binance-gateway/app/main.py` уже содержит:

* `GET /health`
* `GET /time`
* `POST /asset-balance`
* `POST /usdc-balance`
* `GET /price`

Позже в текущем этапе были добавлены / согласованы ещё:

* `GET /exchange-info`
* `GET /balance`

### Что это даёт

Теперь gateway уже покрывает часть Grid Minimum Contract:

* `GET /price` ✅
* `GET /exchange-info` ✅
* `GET /balance` ✅

### Что ещё нужно для первого реального grid start

* `GET /open-orders`
* `POST /order/limit-buy`

### Что нужно позже для полного OCO parity

* `GET /open-oco`
* `POST /oco/cancel`
* `POST /order/oco-sell`
* `GET /my-trades`

---

## 23.12 Главный bottleneck проекта на конец текущего этапа

Главная недоделка теперь уже не UI и не portal-api.

### Главный bottleneck

**Реальный execution bridge между `portal-api` → `trader` → `binance-gateway`.**

На конец текущего этапа:

* UI уже зрелый
* portal-api уже имеет runtime bridge и contract endpoints
* trader service уже жив как контейнер
* engine уже импортируется
* gateway partly покрывает engine contract

Но:

* `portal-api /pairs/{id}/start` пока ещё только меняет `pair.status`
* `/pairs/{id}/stop` пока ещё только меняет `pair.status`
* `/pairs/{id}/add-order` пока ещё contract-only
* `/pairs/{id}/runtime` пока derived из DB bridge, а не из live trader registry

---

## 23.13 Рекомендуемая next-step архитектура

Следующий слой нужно строить так:

### trader service v1

Внутри `trader/app/main.py`:

* in-memory registry активных трейдеров
* `TRADERS: dict[int, SpotBotTrader]`
* later optional storage for PairConfig / logs

### Trader API contract v1

* `GET /health`
* `POST /start`
* `POST /stop`
* `POST /add-order`
* `GET /runtime/{pair_id}`

### portal-api role

`portal-api` должен стать orchestration layer:

* достаёт и расшифровывает ключи пользователя из БД
* читает config пары
* вызывает trader service
* обновляет persisted pair status
* запрашивает runtime из trader
* fallback на DB bridge, если trader runtime недоступен

### Source of truth model

* **DB** = persisted pair config + persisted pair status
* **Trader memory** = live runtime truth

Это признано правильной промежуточной архитектурой.

---

# 24. Следующие этапы разработки

1 Tooltip для Create Drawer

2 Runtime состояния торговли

active_cycle
waiting_close
idle

3 Функция +Order

4 Средняя цена позиции

5 UI лог торгов

6 Интеграция trader service

---

# Главное правило разработки

Никогда не изменять торговый алгоритм.

Engine должен оставаться идентичным:

SpotBot-RAV-Trade_GUI_3.py

UI только управляет параметрами и запуском.

---


23.14 Trader service v1 реально подключён

В trader/app/main.py реализован первый рабочий API-контракт trader service.

Добавлено
in-memory registry активных runtime:
_registry: Dict[int, TraderHandle]
TraderHandle хранит:
pair_id
owner_email
timestamps
PairConfig
BinanceGatewayAdapter
SpotBotTrader
last_error
Реализованные endpoints
GET /health
POST /start
POST /stop
POST /add-order
GET /runtime/{pair_id}
Практический результат

trader перестал быть только контейнером с import-check и стал реальным execution service.

23.15 Portal API → Trader bridge подключён

В portal-api/app/pairs.py подключён реальный вызов trader service.

Что сделано

portal-api теперь:

читает pair config из БД
расшифровывает Binance API ключи пользователя
вызывает trader /start
вызывает trader /stop
вызывает trader /add-order
запрашивает trader /runtime/{pair_id}
Важный архитектурный результат

Execution path стал реальным:

Browser
→ portal-ui
→ portal-api
→ trader
→ binance-gateway
→ Binance

23.16 Реальный старт пары впервые успешно дошёл до Binance

Был выполнен первый реальный end-to-end тест.

Подтверждено

После вызова:

POST /pairs/{id}/start

система смогла:

создать runtime в trader registry
загрузить symbol filters через gateway
посчитать grid
выставить реальные BUY limit orders на Binance
Практический результат

Minimum grid execution path подтверждён как рабочий.

23.17 Binance Gateway доведён до minimum grid contract

В binance-gateway/app/main.py были добавлены недостающие endpoints для первого реального grid start.

Добавлено
GET /open-orders
POST /order/limit-buy
Также добавлены helper-функции
_signed_get(...)
_signed_post(...)
позже _signed_delete(...)
Практический результат

Grid-start path стал рабочим без mock/fallback логики.

23.18 Исправлен критический bug в trader loop

После первого реального старта был найден и исправлен баг:

TypeError: 'decimal.Decimal' object cannot be interpreted as an integer

Причина

В _run() использовалось:

time.sleep(max(Decimal("0.2"), _d(self.cfg.poll_sec)))
Исправление

Перед time.sleep(...) добавлено преобразование в float.

Практический результат

Runtime больше не падает сразу после первого OCO-monitor tick только из-за типа Decimal.

23.19 Binance Gateway доведён до OCO/trades contract

Для post-fill lifecycle были добавлены недостающие endpoints OCO и trades layer.

Добавлено
GET /my-trades
GET /open-oco
POST /oco/cancel
POST /order/oco-sell
Важное уточнение

Для POST /oco/cancel выяснилось, что Binance требует не только orderListId, но и symbol.

Исправление

Contract был уточнён:

{
  "symbol": "ADAUSDC",
  "orderListId": 123456,
  "api_key": "...",
  "api_secret": "..."
}

Также в trader engine был обновлён вызов cancel OCO, чтобы передавать symbol.

Практический результат

Gateway contract теперь покрывает не только старт сетки, но и OCO lifecycle.

23.20 Runtime truth окончательно стал primary source of truth для UI

Была устранена системная проблема рассинхрона между:

pair.status в БД
runtime_status из trader
Что сделано во frontend

В portal-ui/site/js/app.js добавлены и доработаны:

getEffectiveRuntimeStatus(pair)
humanRuntimeStatus(pair)
enrichPairsWithRuntime(pairs)
runtime-first rendering для списка пар
runtime-first rendering для Manage Drawer
Практический результат

UI перестал слепо доверять pair.status из /pairs.

Если runtime уже известен, именно он определяет:

визуальный статус пары
dot color/state
доступность действий
23.21 Исправлена ошибка GET /pairs/{id}/runtime при non-idle pair без runtime

После рестарта trader обнаружилась ошибка:

GET /pairs/{id}/runtime возвращал 500 Internal Server Error

Причина

В fallback-ветке PairRuntimeResponse поле:

open_orders

передавалось как None, тогда как schema ожидала int.

Исправление

Для fallback error-like runtime response установлено:

open_orders = 0
Практический результат

/pairs/{id}/runtime теперь честно возвращает:

runtime_status = error
last_event = Trader runtime not found for non-idle pair

вместо падения 500.

23.22 Добавлен runtime-status error как нормальное UI-состояние

После появления реальных runtime-failure кейсов был введён полноценный UI-state:

error
Что сделано

В app.js и CSS:

для runtime status error добавлена отдельная семантика
dot ошибки сделан:
тёмно-вишнёвым
без мигания
Семантика live-dot стала такой
зелёный мигающий = active_cycle
красный статичный = idle
жёлтый статичный = waiting_close
тёмно-вишнёвый статичный = error
23.23 Добавлен UX-reset пары из error обратно в idle

Обнаружен типовой operational сценарий:

pair упала в runtime error
trader runtime больше не существует
пользователь хочет исправить настройки и снова запустить pair
Что сделано

В saveManagePair(...) добавлена логика:

если runtimeStatus === "error", то в PATCH payload автоматически уходит:

status = "idle"
Практический результат

Обычный SAVE в Manage Drawer теперь может перевести pair из Ошибка обратно в Idle, без ручного редактирования БД.

23.24 Реализован operational workaround для “зависшей active pair”

Был обнаружен реальный сценарий:

pair уже не исполняется
но в БД остаётся status = active
в trader runtime уже нет handle
UI показывает error
Временный рабочий способ возврата pair в idle
docker compose restart trader
pair получает runtime_status = error
пользователь нажимает SAVE
pair возвращается в idle
Важная оговорка

Это временный operational workaround, а не финальное решение.

Отдельный backlog item

Нужно позже доработать POST /stop, чтобы пустая pair могла:

финализироваться
удаляться из trader registry
переходить в idle
без рестарта trader container
23.25 STOP пока реализован как soft stop, но не как finalize-to-idle

Выявлено важное ограничение текущей stop-логики.

Текущее поведение

POST /stop:

не завершает runtime немедленно
устанавливает stop_cycle_requested = true
предполагает естественное завершение цикла
Проблема

Если pair уже:

без позиции
без open orders
без активного OCO

то soft-stop не всегда способен сам корректно вернуть pair в idle.

Вывод

Нужен следующий refinement:

future stop/finalize logic

Если pair пустая:

остановить thread
удалить handle из registry
вернуть runtime в idle

Если pair непустая:

оставить мягкую остановку waiting_close
23.26 Balance UI переведён на free/locked/total модель

Выявлено, что кабинет показывал общий баланс, а не реально доступный для торговли.

Причина

portal-api /verify-keys возвращал только:

usdc_total
Исправление в portal-api

POST /verify-keys теперь возвращает:

usdc_free
usdc_locked
usdc_total
Исправление в UI

verifyKeys() в portal-ui/site/js/app.js теперь показывает:

доступный баланс = usdc_free
строкой ниже: В заявках: usdc_locked
Дополнительное улучшение

Числа форматируются до 2 знаков после точки.

Практический результат

Баланс в кабинете теперь соответствует торговой реальности Binance:

доступно отдельно
locked отдельно
total не маскируется под доступный баланс
23.27 Pair list тоже стал runtime-aware, а не только Manage Drawer

Изначально Manage Drawer уже показывал truth по runtime, но список пар в блоке Pairs ещё жил по /pairs.

Что сделано

loadPairs() был доработан:

загрузка /pairs
догрузка /pairs/{id}/runtime для каждой пары
применение applyRuntimeToPair(...)
только потом render tiles
Практический результат

Список пар и Manage Drawer перестали противоречить друг другу.

23.28 Реальный post-fill OCO lifecycle вышел на фазу интеграционной проверки

После закрытия OCO endpoints и trades endpoints проект вышел в новую фазу.

Что уже подтверждено
pair стартует
grid выставляется
runtime живёт после старта
один или несколько ордеров могут реально исполняться
Что проверяется дальше

После fill trader должен:

увидеть trades через /my-trades
посчитать:
session_position_qty
avg_entry_price
поставить OCO через /order/oco-sell
отслеживать его через /open-oco
при необходимости отменять через /oco/cancel
Текущий статус

Проект находится на стадии живой отладки post-fill OCO path.

Это уже не проблема архитектуры, а следующий слой runtime execution parity.

24. Обновлённый фактический статус проекта
Уже работает
зрелый runtime-aware UI
Create / Manage Drawer
runtime v2 schema
portal-api ↔ trader bridge
trader registry
trader start / stop / add-order / runtime endpoints
minimum grid execution
Binance gateway minimum grid contract
Binance gateway OCO/trades contract
free/locked balance UI
runtime-first status rendering
error-state rendering
reset error → idle через Save
Ещё не доведено до финала
finalize-to-idle для пустой pair через /stop
полная runtime truth по open_orders
устойчивый post-fill OCO lifecycle
trading log / average price block в зрелом виде
reconciliation persisted pair.status ↔ live runtime
полноценный cycle-complete / auto-cycle maturity

# Конец журнала разработки
