# Краткая справка по командам системы WB Reviews

## Команды Docker

```bash
# Сборка и запуск
docker-compose up -d --build

# Просмотр логов
docker-compose logs -f scraper

# Перезапуск
docker-compose restart scraper

# Остановка
docker-compose stop

# Полная остановка с удалением контейнеров
docker-compose down

# Проверка здоровья
curl http://localhost:8000/health
```

## Команды Telegram бота

```
/add <артикул>          - Добавить товар в очередь
/add <ссылка>           - Добавить товар по ссылке
/queue                  - Показать статус очереди
/status                 - Показать статус системы
/pause                  - Приостановить публикации
/resume                 - Возобновить публикации
```

## API Endpoints (для n8n)

```
POST   /products/add                 - Добавить товар
GET    /queue/status                 - Статус очереди
GET    /system/status                - Статус системы
POST   /posts/approve                - Одобрить пост
POST   /posts/revise                 - Переделать пост
POST   /posts/skip                   - Пропустить товар
GET    /posts/awaiting-approval      - Посты для одобрения
POST   /publish                      - Опубликовать пост
POST   /publish/confirm              - Подтвердить публикацию
POST   /publish/uncertain            - Неопределённый результат
POST   /publications/pause           - Приостановить
POST   /publications/resume          - Возобновить
```

## Скрипты

```bash
# Быстрый старт (Linux/Mac)
./start.sh

# Быстрый старт (Windows PowerShell)
.\start.ps1

# Резервное копирование
./backup.sh

# Восстановление
./restore.sh backups/wb_reviews_20261001_120000.db
```

## Работа с базой данных

```bash
# Подключиться к контейнеру
docker exec -it wb_reviews_scraper sh

# Открыть SQLite
sqlite3 /app/data/wb_reviews.db

# Полезные запросы
.tables
SELECT * FROM products ORDER BY added_at DESC LIMIT 10;
SELECT status, COUNT(*) FROM products GROUP BY status;
SELECT * FROM posts WHERE approved = 1;
.quit
```

## Резервное копирование

```bash
# Создать бэкап базы
docker cp wb_reviews_scraper:/app/data/wb_reviews.db ./backup_$(date +%Y%m%d).db

# Создать бэкап volume
docker run --rm -v wb-reviews_scraper_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/backup_$(date +%Y%m%d).tar.gz -C /data .
```

## Мониторинг

```bash
# Логи в реальном времени
docker-compose logs -f scraper

# Последние 100 строк
docker-compose logs --tail=100 scraper

# Статус контейнера
docker-compose ps

# Использование ресурсов
docker stats wb_reviews_scraper
```

## Устранение неполадок

```bash
# Перезагрузить конфигурацию
docker-compose down
docker-compose up -d --build

# Проверить сеть
docker network inspect wb-reviews_wb_reviews_network

# Проверить volumes
docker volume ls | grep wb-reviews

# Очистить логи Docker
docker-compose logs scraper > /dev/null

# Проверить доступность API
curl -v http://localhost:8000/health
```

## Обновление

```bash
# Остановить систему
docker-compose down

# Обновить код (git pull или скопировать файлы)

# Пересобрать и запустить
docker-compose up -d --build

# Проверить логи
docker-compose logs -f scraper
```

## Тестовый товар

Артикул: **913340343**

Ссылки для проверки:
- Карточка: https://www.wildberries.ru/catalog/913340343/detail.aspx
- Отзывы: https://www.wildberries.ru/catalog/913340343/feedbacks?imtId=3088493949
- Вопросы: https://www.wildberries.ru/catalog/913340343/questions?imtId=3088493949
