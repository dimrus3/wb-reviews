# Система обзоров товаров Wildberries для Telegram

Автоматизированная система сбора данных о товарах Wildberries, анализа отзывов и публикации обзоров в Telegram-канал через n8n.

## Архитектура

- **Python-сервис** (FastAPI + Playwright): сбор данных, анализ, генерация постов
- **SQLite**: хранение товаров, очереди, анализов и постов
- **n8n workflows**: управляющий Telegram-бот, расписание публикаций
- **OpenAI-совместимый API**: генерация текстов обзоров

## Структура проекта

```
wb-reviews-system/
├── scraper/                   # Python-сервис
│   ├── app/
│   │   ├── main.py           # FastAPI приложение
│   │   ├── database.py       # Работа с SQLite
│   │   ├── scraper.py        # Playwright сборщик
│   │   ├── analyzer.py       # LLM анализ и генерация
│   │   ├── models.py         # Модели данных
│   │   └── config.py         # Конфигурация
│   ├── prompts/
│   │   ├── analyze.txt       # Промпт для анализа
│   │   └── generate_post.txt # Промпт для генерации поста
│   ├── Dockerfile
│   └── requirements.txt
├── n8n-workflows/            # Экспорты workflow
│   ├── telegram-bot.json     # Управляющий бот
│   ├── callback-handler.json # Обработка кнопок
│   ├── post-approval.json    # Отправка постов на одобрение
│   └── publisher.json        # Публикация по расписанию
├── docker-compose.yml
├── .env.example
└── README.md
```

## Требования

- Docker и Docker Compose
- Работающий n8n (в Docker или отдельно)
- Telegram бот (создать через @BotFather)
- OpenAI-совместимый API (OpenAI, Azure OpenAI, локальные модели и т.д.)

## Установка

### 1. Клонирование и настройка

```bash
# Если проект не на сервере, скопируйте всю директорию wb-reviews на сервер
# Например через scp:
# scp -r C:\Users\anekd\wb-reviews user@server:/path/to/

cd /path/to/wb-reviews

# Создать .env из шаблона
cp .env.example .env
```

### 2. Настройка переменных окружения

Отредактируйте `.env` и заполните обязательные параметры:

```bash
nano .env
```

**Обязательные параметры:**

- `ALLOWED_USER_ID` - ваш Telegram user ID (получить через @userinfobot)
- `TELEGRAM_CHANNEL_ID` - ID канала для публикации (например: -1001234567890)
- `LLM_BASE_URL` - адрес API (например: https://api.openai.com/v1)
- `LLM_API_KEY` - ключ API
- `LLM_MODEL` - название модели (например: gpt-4, gpt-3.5-turbo)

**Важно:** 
- Сначала работайте в режиме `DRY_RUN=true` для тестирования
- Переключите на `DRY_RUN=false` только после проверки

### 3. Запуск сервиса

```bash
# Собрать и запустить контейнер
docker-compose up -d --build

# Проверить логи
docker-compose logs -f scraper

# Проверить здоровье
curl http://localhost:8000/health
```

### 4. Настройка n8n

#### 4.1. Создать Telegram Bot Credential

В n8n:
1. Credentials → Add Credential → Telegram API
2. Имя: "Telegram Bot"
3. Access Token: токен вашего бота от @BotFather
4. Save

#### 4.2. Импортировать workflows

В n8n:
1. Workflows → Import from File
2. Импортируйте по очереди:
   - `n8n-workflows/telegram-bot.json`
   - `n8n-workflows/callback-handler.json`
   - `n8n-workflows/post-approval.json`
   - `n8n-workflows/publisher.json`

3. Для каждого workflow:
   - Откройте workflow
   - Проверьте, что все узлы используют правильный Telegram credential
   - Активируйте workflow (кнопка Active)

#### 4.3. Настройка webhook (если нужно)

Если ваш n8n доступен извне и вы хотите использовать webhook вместо polling:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=<YOUR_N8N_WEBHOOK_URL>"
```

### 5. Проверка интеграции

Отправьте боту команды:

```
/status  - проверить статус системы
/queue   - посмотреть очередь
/add 913340343  - добавить тестовый товар
```

## Использование

### Команды бота

- `/add <артикул или ссылка>` - добавить товар в очередь
- `/queue` - показать статус очереди
- `/status` - показать статус системы и расписание
- `/pause` - приостановить публикации
- `/resume` - возобновить публикации

### Процесс обработки товара

1. **Добавление**: `/add 913340343` или `/add https://www.wildberries.ru/catalog/913340343/detail.aspx`
2. **Сбор данных**: автоматически собираются данные карточки, отзывы, вопросы
3. **Анализ**: LLM анализирует данные и генерирует структурированный анализ
4. **Генерация поста**: создаётся Telegram-пост на основе анализа
5. **Одобрение**: пост отправляется владельцу с кнопками:
   - ✅ Одобрить - пост готов к публикации
   - ✏️ Переделать - запросить переделку с замечаниями
   - ⏭ Пропустить - пропустить товар
6. **Публикация**: одобренные посты публикуются по расписанию (11:00 и 19:00 Новосибирск)

### Расписание публикаций

- **Время**: 11:00 и 19:00 (часовой пояс Asia/Novosibirsk)
- **Лимит**: максимум 2 публикации в день
- **Приоритет**: посты публикуются в порядке одобрения (FIFO)

## Управление

### Просмотр логов

```bash
# Логи сервиса
docker-compose logs -f scraper

# Последние 100 строк
docker-compose logs --tail=100 scraper
```

### Проверка базы данных

```bash
# Подключиться к контейнеру
docker exec -it wb_reviews_scraper sh

# Открыть SQLite
sqlite3 /app/data/wb_reviews.db

# Примеры запросов
SELECT * FROM products ORDER BY added_at DESC LIMIT 10;
SELECT status, COUNT(*) FROM products GROUP BY status;
SELECT * FROM posts WHERE approved = 1;
```

### Резервное копирование

```bash
# Создать бэкап базы данных
docker cp wb_reviews_scraper:/app/data/wb_reviews.db ./backup_$(date +%Y%m%d_%H%M%S).db

# Создать полный бэкап volume
docker run --rm -v wb-reviews_scraper_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/backup_$(date +%Y%m%d_%H%M%S).tar.gz -C /data .
```

### Восстановление из бэкапа

```bash
# Восстановить базу данных
docker cp ./backup_20261001_120000.db wb_reviews_scraper:/app/data/wb_reviews.db

# Восстановить volume
docker run --rm -v wb-reviews_scraper_data:/data -v $(pwd):/backup alpine \
  tar xzf /backup/backup_20261001_120000.tar.gz -C /data
```

### Перезапуск

```bash
# Перезапустить сервис
docker-compose restart scraper

# Пересобрать и перезапустить
docker-compose up -d --build
```

### Остановка

```bash
# Остановить без удаления данных
docker-compose stop

# Остановить и удалить контейнеры (данные сохраняются в volume)
docker-compose down

# ОПАСНО: Удалить всё включая данные
docker-compose down -v
```

## Тестирование

### Первый запуск (Dry Run)

При первом запуске система работает в режиме `DRY_RUN=true`:

1. Добавьте тестовый товар:
   ```
   /add 913340343
   ```

2. Наблюдайте за логами:
   ```bash
   docker-compose logs -f scraper
   ```

3. Проверьте статус:
   ```
   /queue
   ```

4. Когда пост будет готов, бот отправит его вам для одобрения

5. Одобрите пост и дождитесь времени публикации (11:00 или 19:00)

6. Проверьте логи - должно быть сообщение "DRY RUN: Публикация поста..."

### Переход на боевой режим

После успешного тестирования:

1. Убедитесь, что бот добавлен в канал как администратор
2. Измените `.env`:
   ```
   DRY_RUN=false
   ```
3. Перезапустите:
   ```bash
   docker-compose restart scraper
   ```

## Безопасность

### Защита секретов

- ✅ Секреты хранятся в `.env` (не коммитить в git)
- ✅ API сервиса не доступен извне (только для n8n)
- ✅ Проверка прав доступа по Telegram user ID
- ✅ Валидация входных данных (артикулы, URL)

### Ограничения

- Только разрешённые домены Wildberries
- Дневной лимит запросов к LLM
- Ограничение на количество отзывов/вопросов
- Тайм-ауты для предотвращения зависаний

### Рекомендации

1. Не выставляйте API сервиса в интернет
2. Используйте сложный API ключ для LLM
3. Регулярно делайте бэкапы базы данных
4. Мониторьте логи на наличие ошибок
5. Не повышайте лимиты без необходимости

## API Endpoints

Внутренние endpoints для n8n (не доступны извне):

- `POST /products/add` - добавить товар
- `GET /queue/status` - статус очереди
- `GET /system/status` - статус системы
- `POST /posts/approve` - одобрить пост
- `POST /posts/revise` - переделать пост
- `POST /posts/skip` - пропустить товар
- `GET /posts/awaiting-approval` - посты для одобрения
- `POST /publish` - опубликовать следующий пост
- `POST /publish/confirm` - подтвердить публикацию
- `POST /publish/uncertain` - неопределённый результат
- `POST /publications/pause` - приостановить публикации
- `POST /publications/resume` - возобновить публикации

## Устранение неполадок

### Товар не добавляется

**Проблема:** Ошибка "Некорректный артикул или ссылка"

**Решение:**
- Проверьте формат артикула (должны быть только цифры, 6-12 символов)
- Убедитесь, что ссылка начинается с https://www.wildberries.ru/

### Сбор данных завершается с ошибкой

**Проблема:** "Обнаружена капча" или "Таймаут"

**Решение:**
- Капча: требуется ручная проверка, возможна блокировка со стороны WB
- Таймаут: увеличьте `PLAYWRIGHT_TIMEOUT` в `.env`
- Проверьте доступность WB: `curl https://www.wildberries.ru`

### LLM не отвечает

**Проблема:** "Ошибка при вызове LLM"

**Решение:**
- Проверьте `LLM_BASE_URL` и `LLM_API_KEY`
- Убедитесь в доступности API: `curl -H "Authorization: Bearer $LLM_API_KEY" $LLM_BASE_URL/models`
- Проверьте дневной лимит: возможно достигнут `LLM_DAILY_LIMIT`

### Публикация не происходит

**Проблема:** Время публикации прошло, но пост не опубликован

**Решение:**
- Проверьте, что workflow "Publisher" активен в n8n
- Убедитесь, что `DRY_RUN=false`
- Проверьте логи n8n и scraper
- Убедитесь, что не достигнут дневной лимит (2 публикации)
- Проверьте статус: `/status`

### Бот не отвечает на команды

**Проблема:** Команды не работают

**Решение:**
- Проверьте, что workflow "Telegram Bot" активен в n8n
- Проверьте Telegram credential в n8n
- Убедитесь, что ваш user_id совпадает с `ALLOWED_USER_ID`
- Проверьте, доступен ли сервис: `curl http://localhost:8000/health`

### База данных повреждена

**Проблема:** Ошибки SQLite

**Решение:**
```bash
# Остановить сервис
docker-compose stop

# Проверить целостность базы
docker run --rm -v wb-reviews_scraper_data:/data alpine \
  sqlite3 /data/wb_reviews.db "PRAGMA integrity_check;"

# Восстановить из бэкапа если нужно
```

## Мониторинг

### Важные метрики

- Количество товаров в каждом статусе (`/queue`)
- Количество публикаций за день
- Количество запросов к LLM за день
- Ошибки в логах

### Регулярные проверки

```bash
# Проверка здоровья
curl http://localhost:8000/health

# Статус очереди
curl http://localhost:8000/queue/status

# Системный статус
curl http://localhost:8000/system/status
```

## Ограничения и известные проблемы

1. **imtId для других товаров**: imtId извлекается из страницы товара динамически, но может отсутствовать
2. **Капча**: при обнаружении капчи требуется ручная проверка
3. **Блокировки WB**: частые запросы могут привести к временной блокировке IP
4. **Формат отзывов**: селекторы могут измениться при обновлении сайта WB
5. **Транзакции Telegram**: при таймауте публикации статус может быть неопределённым

## Разработка и доработка

### Изменение промптов

Промпты находятся в `scraper/prompts/`:
- `analyze.txt` - анализ данных
- `generate_post.txt` - генерация поста

После изменения:
```bash
# Промпты монтируются как volume, изменения применяются сразу
# Но для уверенности можно перезапустить:
docker-compose restart scraper
```

### Изменение кода

После изменения кода Python:
```bash
docker-compose up -d --build
```

### Изменение расписания

В n8n workflow "Publisher" измените cron-выражение:
- Текущее: `0 11,19 * * *` (11:00 и 19:00)
- Например, для 10:00 и 18:00: `0 10,18 * * *`

## Контакты и поддержка

При возникновении проблем:
1. Проверьте логи: `docker-compose logs -f scraper`
2. Проверьте статус: `/status` в боте
3. Изучите раздел "Устранение неполадок"

---

**Версия:** 1.0  
**Дата:** 2026-10-01  
**Тестовый товар:** 913340343
