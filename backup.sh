#!/bin/bash
# Скрипт резервного копирования данных WB Reviews

set -e

BACKUP_DIR="./backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
CONTAINER_NAME="wb_reviews_scraper"

echo "=================================="
echo "WB Reviews - Backup"
echo "=================================="
echo ""

# Создать директорию для бэкапов
mkdir -p "$BACKUP_DIR"

# Проверка, что контейнер существует
if ! docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "❌ Контейнер $CONTAINER_NAME не найден"
    exit 1
fi

echo "📦 Создание резервной копии базы данных..."

# Бэкап базы данных
if docker exec "$CONTAINER_NAME" test -f /app/data/wb_reviews.db; then
    docker cp "$CONTAINER_NAME:/app/data/wb_reviews.db" "$BACKUP_DIR/wb_reviews_${TIMESTAMP}.db"
    echo "✅ База данных: $BACKUP_DIR/wb_reviews_${TIMESTAMP}.db"
else
    echo "⚠️  База данных не найдена в контейнере"
fi

echo ""
echo "📦 Создание резервной копии volume..."

# Бэкап volume
docker run --rm \
    -v wb-reviews_scraper_data:/data \
    -v "$(pwd)/$BACKUP_DIR:/backup" \
    alpine \
    tar czf "/backup/volume_${TIMESTAMP}.tar.gz" -C /data .

echo "✅ Volume: $BACKUP_DIR/volume_${TIMESTAMP}.tar.gz"

# Список бэкапов
echo ""
echo "📋 Доступные бэкапы:"
ls -lh "$BACKUP_DIR"

echo ""
echo "✅ Резервное копирование завершено"
echo ""
echo "Для восстановления из бэкапа используйте:"
echo "  ./restore.sh $BACKUP_DIR/wb_reviews_${TIMESTAMP}.db"
echo ""
