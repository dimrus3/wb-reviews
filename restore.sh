#!/bin/bash
# Скрипт восстановления из резервной копии

set -e

CONTAINER_NAME="wb_reviews_scraper"

if [ -z "$1" ]; then
    echo "Использование: ./restore.sh <путь_к_бэкапу.db>"
    echo ""
    echo "Доступные бэкапы:"
    ls -lh ./backups/*.db 2>/dev/null || echo "  Нет бэкапов"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ Файл $BACKUP_FILE не найден"
    exit 1
fi

echo "=================================="
echo "WB Reviews - Restore"
echo "=================================="
echo ""
echo "⚠️  ВНИМАНИЕ: Это заменит текущую базу данных!"
echo ""
read -p "Продолжить? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Отменено"
    exit 0
fi

# Проверка контейнера
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "❌ Контейнер $CONTAINER_NAME не запущен"
    echo "Запустите его командой: docker-compose up -d"
    exit 1
fi

echo ""
echo "🔄 Остановка сервиса..."
docker-compose stop scraper

echo ""
echo "📦 Восстановление базы данных..."
docker cp "$BACKUP_FILE" "$CONTAINER_NAME:/app/data/wb_reviews.db"

echo ""
echo "🚀 Запуск сервиса..."
docker-compose start scraper

echo ""
echo "⏳ Ожидание готовности..."
sleep 5

if curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ Восстановление завершено успешно"
else
    echo "⚠️  Сервис не отвечает. Проверьте логи:"
    echo "   docker-compose logs scraper"
fi

echo ""
