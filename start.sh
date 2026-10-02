#!/bin/bash
# Скрипт быстрого старта системы WB Reviews

set -e

echo "=================================="
echo "WB Reviews System - Quick Start"
echo "=================================="
echo ""

# Проверка Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker не установлен. Установите Docker и повторите попытку."
    exit 1
fi

if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "❌ Docker Compose не установлен. Установите Docker Compose и повторите попытку."
    exit 1
fi

echo "✅ Docker найден"

# Проверка .env
if [ ! -f .env ]; then
    echo "❌ Файл .env не найден"
    echo "Создайте его из .env.example и заполните обязательные параметры:"
    echo "  cp .env.example .env"
    echo "  nano .env"
    exit 1
fi

echo "✅ Конфигурация найдена"

# Проверка обязательных переменных
source .env

if [ -z "$ALLOWED_USER_ID" ]; then
    echo "❌ ALLOWED_USER_ID не установлен в .env"
    exit 1
fi

if [ -z "$TELEGRAM_CHANNEL_ID" ]; then
    echo "❌ TELEGRAM_CHANNEL_ID не установлен в .env"
    exit 1
fi

if [ -z "$LLM_BASE_URL" ] || [ -z "$LLM_API_KEY" ]; then
    echo "❌ LLM_BASE_URL или LLM_API_KEY не установлены в .env"
    exit 1
fi

echo "✅ Обязательные переменные установлены"
echo ""

# Сборка и запуск
echo "🔨 Сборка Docker образа..."
docker-compose build

echo ""
echo "🚀 Запуск сервиса..."
docker-compose up -d

echo ""
echo "⏳ Ожидание готовности сервиса..."
sleep 10

# Проверка здоровья
for i in {1..30}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "✅ Сервис запущен и готов!"
        break
    fi
    echo "   Попытка $i/30..."
    sleep 2
done

if ! curl -s http://localhost:8000/health > /dev/null 2>&1; then
    echo "❌ Сервис не отвечает. Проверьте логи:"
    echo "   docker-compose logs scraper"
    exit 1
fi

echo ""
echo "=================================="
echo "✅ Система успешно запущена!"
echo "=================================="
echo ""
echo "Следующие шаги:"
echo ""
echo "1. Импортируйте workflows в n8n:"
echo "   - n8n-workflows/telegram-bot.json"
echo "   - n8n-workflows/callback-handler.json"
echo "   - n8n-workflows/post-approval.json"
echo "   - n8n-workflows/publisher.json"
echo "   - n8n-workflows/revision-handler.json"
echo ""
echo "2. Активируйте все workflows в n8n"
echo ""
echo "3. Отправьте боту команду: /status"
echo ""
echo "4. Добавьте тестовый товар: /add 913340343"
echo ""
echo "Полезные команды:"
echo ""
echo "  docker-compose logs -f scraper    # Просмотр логов"
echo "  docker-compose restart scraper    # Перезапуск"
echo "  docker-compose stop               # Остановка"
echo "  curl http://localhost:8000/health # Проверка здоровья"
echo ""
echo "Документация: README.md"
echo ""
