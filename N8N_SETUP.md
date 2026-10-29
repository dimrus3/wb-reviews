# n8n ↔ wb-reviews: настройка, состояние, публикация

- Сервис-скрапер: `http://localhost:8100` (внутри docker-сети — `http://scraper:8000`).
- n8n: `http://localhost:5678`, публичный HTTPS-адрес — в `.env` → `N8N_PUBLIC_URL`.
- Токен бота — в credential `Telegram Bot` внутри n8n; `TELEGRAM_CHANNEL_ID` и `ALLOWED_USER_ID` — в `.env`.

---

## 0. Состояние на этой машине

| Пункт | Состояние |
|---|---|
| Telegram credential `Telegram Bot` (id `9hvA7Tk3lvUpnZ9y`) | создан, привязан ко всем Telegram-узлам всех трёх воркфлоу |
| Три воркфлоу с `telegramTrigger` слиты в один `WB Reviews - Bot` | сделано: 28 узлов, один триггер, маршрутизация цепочкой If |
| Активность воркфлоу | `WB Reviews - Bot`, `WB Reviews - Post Approval`, `WB Reviews - Publisher` — все `active` |
| Telegram webhook | зарегистрирован на публичный HTTPS из `N8N_PUBLIC_URL` |
| Прогон бота | `/status`, `/queue`, кнопка «✏️ Переделать» — апдейты доходят, ответы уходят |
| `DRY_RUN` | `true` — публикация в канал выключена, пока не переключишь |

id воркфлоу фиксированы (иначе импорт плодит дубли):

```
WB Reviews - Bot            LPxyHeGVEGPrDB9Y
WB Reviews - Post Approval  0EAItJQKk5Dy5tWK
WB Reviews - Publisher      j9OyML8ewmxiYLNU
```

---

## 1. ⚠️ Публичный HTTPS обязателен

Telegram Trigger работает через webhook: при активации n8n вызывает у Telegram
`setWebhook(url)`, и дальше Telegram **сам** присылает апдейты на этот URL.
Telegram принимает только **HTTPS** и только публично доступный адрес.

Отсюда:

- `WEBHOOK_URL=http://localhost:5678` → `setWebhook` падает с
  `Bad request - please check your parameters`, триггер не активируется,
  в таблице `workflow_publication_trigger_status` появляется `failed`.
- Без публичного адреса бот не получает сообщения вообще — даже когда сервис и
  расписание работают.

Поэтому в `docker-compose.yml` n8n получает:

```yaml
- WEBHOOK_URL=${N8N_PUBLIC_URL:-http://localhost:5678}
- N8N_EDITOR_BASE_URL=${N8N_PUBLIC_URL:-http://localhost:5678}
```

а сам адрес лежит в `.env`.

---

## 2. Туннель для локальных тестов

Cloudflare Tunnel провайдер режет (порт 7844 закрыт и по UDP, и по TCP),
поэтому используется `localhost.run` по SSH:

```bash
ssh -T -o StrictHostKeyChecking=no -o ServerAliveInterval=30 \
    -o ExitOnForwardFailure=yes -R 80:localhost:5678 nokey@localhost.run
```

В выводе появится адрес вида `https://xxxxxxxx.lhr.life` — это и есть
`N8N_PUBLIC_URL`.

Туннель нестабилен: иногда отдаёт `502` при живом SSH-процессе. Лечится
перезапуском SSH (новый поддомен → новый `N8N_PUBLIC_URL` → переактивация).

### Альтернатива: cloudflared по HTTP/2 (устойчивее)

Обычный cloudflared падает с `ERROR: Allow outbound QUIC traffic on port 7844`
(провайдер режет UDP). С `--protocol http2` он ходит по TCP 443 и работает:

```bash
docker run -d --name wb_reviews_tunnel \
  --network wb-reviews_wb_reviews_network --restart unless-stopped \
  cloudflare/cloudflared:latest tunnel --no-autoupdate --protocol http2 \
  --url http://wb_reviews_n8n:5678
docker logs wb_reviews_tunnel 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1
```

Адрес `*.trycloudflare.com` меняется при перезапуске контейнера — тот же
порядок действий: `.env` → `N8N_PUBLIC_URL` → `--force-recreate n8n` →
переактивировать воркфлоу.

**Смена адреса** (бесплатный туннель даёт новый поддомен каждый раз):

1. Поднять туннель, скопировать URL.
2. `.env` → `N8N_PUBLIC_URL=https://...`.
3. `docker compose --profile n8n up -d --force-recreate n8n`
4. Переактивировать воркфлоу — при активации n8n заново вызовет `setWebhook`
   уже с новым адресом (см. п. 5).
5. В логах должно появиться `Added webhook "Telegram Update"` без ошибок.

> Туннель — только для тестов: он живёт, пока жив SSH-процесс. Для постоянной
> работы нужен домен (п. 10).

---

## 3. Аккаунт n8n и credential

1. Владелец n8n создан (`connect@ruvideos.net`), вход в UI по нему.
2. `Credentials` → `Telegram Bot` — токен от [@BotFather](https://t.me/BotFather).
   Имя должно быть ровно `Telegram Bot`: JSON воркфлоу ссылается на
   `{"telegramApi":{"id":"9hvA7Tk3lvUpnZ9y","name":"Telegram Bot"}}`.
   Пересоздал credential с другим id → поправь `CRED_ID` в
   `n8n-workflows/build_workflows.py` и пересобери.
3. Токен живёт только в credential. Python-сервис в Telegram не ходит:
   отправляет всё n8n.

---

## 4. Один триггер на бота (почему слито)

У Telegram на бота **один** webhook. В репозитории было три воркфлоу с
`telegramTrigger` (`telegram-bot`, `callback-handler`, `revision-handler`):
активируешь второй — первый перестаёт получать апдейты. Симптом: `/add`
работает, кнопки — нет (или наоборот).

Решение: один `WB Reviews - Bot`, один `telegramTrigger`
(updates: `message` + `callback_query`), затем Code-узел `Normalize`
разворачивает апдейт в плоский item с полем `action`, дальше цепочка If:

```
approve / skip / revise_ask / revise_apply / add / queue / status / pause / resume
```

Каждый If: true → HTTP-вызов к сервису + ответ в чат; false → следующий If.

---

## 5. Активация воркфлоу

**Через UI:** открыть воркфлоу → тумблер `Active` справа сверху.

**Через REST API** (без браузера):

```bash
# 1) логин, cookie в файл
curl -s -c cookies.txt -H 'Content-Type: application/json' \
  -d '{"emailOrLdapLoginId":"<email>","password":"<пароль>"}' \
  http://localhost:5678/rest/login > /dev/null

# 2) текущая версия (activate требует versionId)
VID=$(curl -s -b cookies.txt http://localhost:5678/rest/workflows/LPxyHeGVEGPrDB9Y \
      | python -c "import sys,json;print(json.load(sys.stdin)['data']['versionId'])")

# 3) включить / выключить
curl -s -b cookies.txt -X POST http://localhost:5678/rest/workflows/LPxyHeGVEGPrDB9Y/activate \
     -H 'Content-Type: application/json' -d "{\"versionId\":\"$VID\"}"
```

Подводные камни:

- `n8n update:workflow --active=true` ставит флаг в БД, но триггеры не
  регистрирует: воркфлоу «активен», а webhook нет.
- Если воркфлоу уже «активен», повторный `activate` ничего не делает:
  сначала `deactivate`, потом `activate`.
- `n8n import:workflow` **деактивирует** перезаписываемые воркфлоу.
  После каждого импорта — активировать заново.

---

## 6. Как публиковать

### Автоматически по расписанию

`WB Reviews - Publisher` активен, cron `0 11,19 * * *` (Asia/Novosibirsk) =
11:00 и 19:00 по Новосибирску (04:00 и 12:00 UTC).

Цепочка: `POST /publish` → если есть одобренный пост, отправка текста в канал
`TELEGRAM_CHANNEL_ID` → `POST /publish/confirm` (пишет `message_id`) →
при ошибке отправки: `POST /publish/uncertain` + сообщение владельцу.

### Вручную, не дожидаясь слота

1. **Из n8n:** открыть `WB Reviews - Publisher` → `Execute Workflow`.
2. **Из сервиса** (посмотреть текст, ничего не отправляя):
   ```bash
   curl -X POST http://localhost:8100/publish -H 'Content-Type: application/json' -d '{"dry_run":true}'
   ```
   Ответ: `dry_run: true`, `post_id`, `article`, `text_preview`.

### Условия, при которых пост реально уйдёт в канал

1. Товар прошёл `queued → collecting → analyzing → awaiting_approval`.
2. Пост одобрен: нажата кнопка «✅ Одобрить» в Telegram либо
   `POST /posts/approve` с `{"product_id": N}`.
3. `DRY_RUN=false` в `.env` — иначе `/publish` отдаёт только превью, а узел
   `Has Post?` (условие `success=true И dry_run=false`) не пускает в канал.
4. Публикации не на паузе (`/pause` в боте).
5. Не превышен `MAX_PUBLICATIONS_PER_DAY`.

### Перевод в боевой режим

```bash
cd F:/ai/wb-reviews
sed -i 's/^DRY_RUN=true/DRY_RUN=false/' .env
docker compose up -d --force-recreate scraper      # .env читается при старте
docker exec wb_reviews_scraper printenv DRY_RUN    # → false
```

### Ручная публикация конкретного поста

```bash
# 1. получить текст и занять пост
curl -X POST http://localhost:8100/publish -H 'Content-Type: application/json' -d '{"dry_run":false}'
# → {"success":true,"post_id":3,"product_id":3,"article":"...","text":"..."}

# 2. отправить text в канал (в n8n это узел Publish To Channel)

# 3. подтвердить, иначе пост останется в статусе publishing
curl -X POST "http://localhost:8100/publish/confirm?post_id=3&product_id=3&channel_id=-1004440141572&message_id=<id>"

# если отправка не удалась:
curl -X POST "http://localhost:8100/publish/uncertain?post_id=3&product_id=3&error=send%20failed"
```

---

## 7. Переменные окружения для воркфлоу

Узлы используют `$env.ALLOWED_USER_ID` и `$env.TELEGRAM_CHANNEL_ID`. Они
проброшены в контейнер n8n из `.env`, доступ к env включён
(`N8N_BLOCK_ENV_ACCESS_IN_NODE=false`) — без этого `$env` вернёт пустоту и
сообщения уйдут в никуда.

```bash
docker exec wb_reviews_n8n printenv ALLOWED_USER_ID TELEGRAM_CHANNEL_ID WEBHOOK_URL
```

Меняешь ID → правишь `.env` → перезапускаешь **n8n**:
```bash
docker compose --profile n8n up -d --force-recreate n8n
```

---

## 8. Диагностика

```bash
docker exec wb_reviews_n8n n8n list:workflow
docker logs --tail 40 wb_reviews_n8n          # триггеры/webhook видны при N8N_LOG_LEVEL=debug
docker logs --tail 40 wb_reviews_scraper

docker exec wb_reviews_scraper python -c "
import sqlite3;c=sqlite3.connect('/app/data/wb_reviews.db')
print('products:',list(c.execute('select id,article,status from products')))
print('posts:',list(c.execute('select id,version,approved,published_at,notified_at from posts')))
print('publications:',list(c.execute('select id,post_id,status from publications')))
"
```

Проверить, что Telegram знает про webhook:

```bash
curl "https://api.telegram.org/bot<ТОКЕН>/getWebhookInfo"
# url = https://<N8N_PUBLIC_URL>/webhook/... ; pending_update_count небольшой
```

Если бот молчит, смотреть по порядку: `url` в `getWebhookInfo` →
`Added webhook "Telegram Update"` в логах n8n → статус последнего execution
(`Overview` → `Executions`).

Секрет webhook (для локального теста апдейта):
`<workflowId>_<nodeId>`, Telegram присылает его в заголовке
`X-Telegram-Bot-Api-Secret-Token`. Без заголовка n8n отвечает
`403 Provided secret is not valid`.

---

## 9. Грабли (всё ниже уже исправлено в коде)

| Проблема | Симптом | Что сделано |
|---|---|---|
| `llm_requests` без `UNIQUE(date)` | `ON CONFLICT clause does not match` — пайплайн падал на анализе, товар в `failed` | схема `date PRIMARY KEY` + авто-миграция |
| `/publish` в dry-run «съедал» пост | реальная публикация не находила пост | в dry-run пост только читается (`claim=False`) |
| `approve_post` оставлял одобренной другую версию | в канал могли уйти две версии | при одобрении снимается `approved` с остальных, публикатор берёт максимальную версию |
| `/publications/pause|resume` принимал `user_id` только в query | `422`, команды `/pause` `/resume` не работали | принимается и из тела, и из query |
| `LLM_TIMEOUT=120` при `LLM_MAX_TOKENS=8000` | пустой `ReadTimeout` на длинной генерации | поднято до 300 с |
| If-узлы в формате v1 с `operation: "equals"` | `compareOperationFunctions[...] is not a function` — падало первое же нажатие кнопки | генератор переведён на If **v2.2** и формат фильтра v2 |
| Три `telegramTrigger` в трёх воркфлоу | бот отвечает на команды, но не на кнопки | слито в один воркфлоу |
| `WEBHOOK_URL` = localhost | `setWebhook` → `Bad request`, триггер не активируется | публичный HTTPS через туннель |
| `n8n update:workflow --active=true` | «активен» в БД, но триггеры не работают | активировать через UI или REST `/activate` |
| `import:workflow` | после импорта воркфлоу выключаются | после импорта активировать заново |
| `SQLITE_READONLY: attempt to write a readonly database` после `docker cp` | n8n падает в цикле при старте | файлы БД внутри контейнера попали под чужого владельца: `docker exec -u root wb_reviews_n8n chown node:node /home/node/.n8n/database.sqlite*`, затем `docker restart wb_reviews_n8n`. Проще не копировать БД в контейнер вообще |
| `n8n login` через REST | `401` после перезапуска n8n | cookie `n8n-auth` привязан к процессу: после каждого `--force-recreate` логиниться заново |
| сплит-туннель в VPN не помогает скраперу | в браузере карточка WB открывается, а скрапер ловит `Timeout` / `498` | Docker Desktop ходит наружу через WSL2 и **игнорирует** посайтовые исключения VPN. Работает исключение **по приложению**: добавить Docker в исключения Amnezia — тогда контейнер выходит с домашнего IP (проверено: `data-req-ip=5.128.199.88`) |
| WB отдаёт антибот-челлендж вместо карточки | `HTTP 498`, страница 1.2 КБ, `X-Wbaas-Token: get`, `/__wbaas/challenges/antibot/` | JS-челлендж решается сам за 8–20 с в Chromium. Скрапер ждёт появления title с «купить за», до 3 перезагрузок, с реалистичным UA и `--disable-blink-features=AutomationControlled` |
| обрезанный User-Agent | челлендж крутится бесконечно, карточка не появляется | UA должен быть полным: `Chrome/131.0.0.0 Safari/537.36`, плюс `locale=ru-RU` и `Accept-Language` |
| DOM-селекторы карточки устарели | `.product-page__title`, `.price-block__final-price`, `.feedback` дают 0 элементов | имя берётся из `document.title` (до «купить за»), бренд — `a[href*="/brands/"]`, рейтинг — `[class*=Rating]` («4,9 · 327 оценок»), отзывы — API `feedbacks1/2.wb.ru/feedbacks/v2/<imtId>` |
| `imtId` не находился | отзывы и вопросы пустые | регекс с кавычками то срабатывал, то нет — заменили на поиск первого числа (≥6 цифр) после подстроки `imtId` |
| товары в `queued` висели навсегда | `/add` после `failed` не запускал сбор | добавлен фоновый `queue_worker`: каждые 20 с забирает один товар в статусе `queued` |

---

## 10. Когда выносишь на сервер

1. Сервис — на VPS в РФ: оттуда Wildberries не отдаёт антибот 498, реальные
   товары соберутся (с этой машины — нет, см. п. 12).
2. n8n на сервере уже есть (`n8n.anonimayzer.site`) — отдельный контейнер
   поднимать не обязательно. В воркфлоу заменить `http://scraper:8000` на
   реальный адрес сервиса; если n8n в другой docker-сети — внешний адрес
   (`http://<IP>:8100`) или общая сеть (`docker network connect`).
3. `N8N_PUBLIC_URL` = реальный домен n8n, туннель не нужен.
4. Пробросить `ALLOWED_USER_ID` и `TELEGRAM_CHANNEL_ID` в окружение n8n.
5. `DRY_RUN=false` — только после ручной проверки публикации.
6. Убрать `N8N_LOG_LEVEL=debug` из `.env`.

---

## 11. Модель LLM

В `.env`: `LLM_MODEL=grok-4.6`, `LLM_BASE_URL=https://api.x.ai/v1`,
`LLM_MAX_TOKENS=8000`, `LLM_TIMEOUT=300`.
Модели `grok-4.7` у ключа нет — API отвечает `404 not-found`. Доступны:
`grok-4.3`, `grok-4.5`, `grok-4.6`, `grok-4.20-*`, `grok-build-0.1`.

---

## 12. Почему не собираются реальные товары с этой машины

Wildberries отдаёт антибот с текущего IP:

- `https://www.wildberries.ru/...` → **HTTP 498** (и в браузере, и в Playwright с живым User-Agent)
- `https://card.wb.ru/cards/v2/detail?...` → **403**
- `https://search.wb.ru/...` → **403**
- basket-хосты (`basket-01..15.wbbasket.ru/...`) для проверенных артикулов → **404**
- при этом `feedbacks1/2.wb.ru` и `static-basket-01.wb.ru` отвечают **200**

В `scraper.py` поддержки прокси нет. Варианты: резидентный/мобильный прокси РФ,
запуск на сервере в РФ, либо наполнение БД извне (ручной импорт).

Вторая половина пайплайна проверена целиком скриптом
`scraper/scripts/e2e_inject_test.py` на фикстуре из 14 отзывов и 6 вопросов:
анализ 54 с, генерация поста 33 с, пост 1843 символа, переделка по замечанию —
854 символа.

---

## 13. Пересборка воркфлоу

JSON в `n8n-workflows/fixed/` — генерируемые, руками не правь:

```bash
python n8n-workflows/build_workflows.py
docker exec wb_reviews_n8n n8n import:workflow --separate --input=/workflows/fixed
# затем активировать все три заново (п. 5)
```

---

## 14. Полезные команды

```bash
docker compose up -d                                          # сервис
docker compose --profile n8n up -d                            # + n8n
docker logs -f wb_reviews_n8n
docker logs -f wb_reviews_scraper
docker exec wb_reviews_n8n n8n list:workflow
docker exec wb_reviews_scraper sh -c 'cat /app/data/wb_reviews.db' > backup_$(date +%F).db
```
