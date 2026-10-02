# Скрипт быстрого старта для Windows (PowerShell)

Write-Host "==================================" -ForegroundColor Cyan
Write-Host "WB Reviews System - Quick Start" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan
Write-Host ""

# Проверка Docker
try {
    docker --version | Out-Null
    Write-Host "✅ Docker найден" -ForegroundColor Green
} catch {
    Write-Host "❌ Docker не установлен. Установите Docker Desktop и повторите попытку." -ForegroundColor Red
    exit 1
}

# Проверка .env
if (-not (Test-Path .env)) {
    Write-Host "❌ Файл .env не найден" -ForegroundColor Red
    Write-Host "Создайте его из .env.example и заполните обязательные параметры:" -ForegroundColor Yellow
    Write-Host "  copy .env.example .env"
    Write-Host "  notepad .env"
    exit 1
}

Write-Host "✅ Конфигурация найдена" -ForegroundColor Green

# Загрузка .env
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
        $key = $matches[1].Trim()
        $value = $matches[2].Trim()
        Set-Variable -Name $key -Value $value -Scope Script
    }
}

# Проверка обязательных переменных
$required = @('ALLOWED_USER_ID', 'TELEGRAM_CHANNEL_ID', 'LLM_BASE_URL', 'LLM_API_KEY')
$missing = @()

foreach ($var in $required) {
    if (-not (Get-Variable -Name $var -ErrorAction SilentlyContinue)) {
        $missing += $var
    }
}

if ($missing.Count -gt 0) {
    Write-Host "❌ Не установлены обязательные переменные: $($missing -join ', ')" -ForegroundColor Red
    exit 1
}

Write-Host "✅ Обязательные переменные установлены" -ForegroundColor Green
Write-Host ""

# Сборка и запуск
Write-Host "🔨 Сборка Docker образа..." -ForegroundColor Yellow
docker-compose build

Write-Host ""
Write-Host "🚀 Запуск сервиса..." -ForegroundColor Yellow
docker-compose up -d

Write-Host ""
Write-Host "⏳ Ожидание готовности сервиса..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

# Проверка здоровья
$ready = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            Write-Host "✅ Сервис запущен и готов!" -ForegroundColor Green
            $ready = $true
            break
        }
    } catch {
        Write-Host "   Попытка $i/30..." -ForegroundColor Gray
        Start-Sleep -Seconds 2
    }
}

if (-not $ready) {
    Write-Host "❌ Сервис не отвечает. Проверьте логи:" -ForegroundColor Red
    Write-Host "   docker-compose logs scraper"
    exit 1
}

Write-Host ""
Write-Host "==================================" -ForegroundColor Cyan
Write-Host "✅ Система успешно запущена!" -ForegroundColor Green
Write-Host "==================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Следующие шаги:" -ForegroundColor Yellow
Write-Host ""
Write-Host "1. Импортируйте workflows в n8n:"
Write-Host "   - n8n-workflows/telegram-bot.json"
Write-Host "   - n8n-workflows/callback-handler.json"
Write-Host "   - n8n-workflows/post-approval.json"
Write-Host "   - n8n-workflows/publisher.json"
Write-Host "   - n8n-workflows/revision-handler.json"
Write-Host ""
Write-Host "2. Активируйте все workflows в n8n"
Write-Host ""
Write-Host "3. Отправьте боту команду: /status"
Write-Host ""
Write-Host "4. Добавьте тестовый товар: /add 913340343"
Write-Host ""
Write-Host "Полезные команды:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  docker-compose logs -f scraper    # Просмотр логов"
Write-Host "  docker-compose restart scraper    # Перезапуск"
Write-Host "  docker-compose stop               # Остановка"
Write-Host "  curl http://localhost:8000/health # Проверка здоровья"
Write-Host ""
Write-Host "Документация: README.md"
Write-Host ""
