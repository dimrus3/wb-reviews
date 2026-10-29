@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

rem ============================================================
rem  WB Reviews — запуск всей системы одним кликом
rem  1) Docker Desktop    3) туннель для Telegram
rem  2) scraper + n8n     4) проверка и открытие панели
rem ============================================================

set "PROJECT=F:\ai\wb-reviews"
set "COMPOSE=C:\Program Files\Docker\Docker\resources\cli-plugins\docker-compose.exe"
set "NETWORK=wb-reviews_wb_reviews_network"

title WB Reviews

echo.
echo ============================================
echo   WB Reviews - запуск
echo ============================================
echo.

if not exist "%PROJECT%" (
    echo [ОШИБКА] Нет папки проекта: %PROJECT%
    pause & exit /b 1
)
cd /d "%PROJECT%"

rem ---------- 1. Docker ----------
docker info >nul 2>&1
if errorlevel 1 (
    echo [1/4] Запускаю Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    set /a W=0
    :waitdocker
    timeout /t 5 /nobreak >nul
    docker info >nul 2>&1
    if not errorlevel 1 goto dockerok
    set /a W+=5
    if !W! GEQ 180 (
        echo [ОШИБКА] Docker не поднялся за 3 минуты.
        pause & exit /b 1
    )
    goto waitdocker
    :dockerok
    echo       Docker готов
) else (
    echo [1/4] Docker уже работает
)

rem ---------- 2. Сервисы ----------
echo [2/4] Поднимаю scraper + n8n...
"%COMPOSE%" --profile n8n up -d
if errorlevel 1 (
    echo [ОШИБКА] Контейнеры не поднялись.
    pause & exit /b 1
)

rem ---------- 3. Туннель ----------
echo [3/4] Туннель для Telegram...
docker inspect wb_reviews_tunnel >nul 2>&1
if errorlevel 1 (
    docker run -d --name wb_reviews_tunnel --restart unless-stopped ^
        --network %NETWORK% cloudflare/cloudflared:latest ^
        tunnel --no-autoupdate --protocol http2 --url http://wb_reviews_n8n:5678 >nul
) else (
    docker start wb_reviews_tunnel >nul 2>&1
)

echo       Ищу адрес туннеля...
set "URL="
for /l %%i in (1,1,15) do (
    if not defined URL (
        timeout /t 4 /nobreak >nul
        for /f "delims=" %%u in ('docker logs --tail 150 wb_reviews_tunnel 2^>^&1 ^| findstr /r /c:"trycloudflare.com"') do (
            for %%a in (%%u) do (
                echo %%a | findstr /r /c:"^https://.*trycloudflare.com$" >nul && set "URL=%%a"
            )
        )
    )
)

if not defined URL (
    echo       [ВНИМАНИЕ] Адрес туннеля не найден.
    echo       Кнопки в Telegram не заработают, пока туннель не поднимется.
    echo       Проверьте: docker logs wb_reviews_tunnel
    goto aftertunnel
)

echo       Адрес: !URL!
findstr /c:"N8N_PUBLIC_URL=!URL!" .env >nul 2>&1
if errorlevel 1 (
    echo       Обновляю .env и перезапускаю n8n...
    powershell -NoProfile -Command "(Get-Content '.env') -replace '^N8N_PUBLIC_URL=.*', 'N8N_PUBLIC_URL=!URL!' | Set-Content -Encoding UTF8 '.env'"
    "%COMPOSE%" --profile n8n up -d --force-recreate n8n >nul
    echo       Жду n8n 30 сек...
    timeout /t 30 /nobreak >nul
) else (
    echo       Адрес в .env уже актуальный
)

:aftertunnel

rem ---------- 4. Проверка ----------
echo [4/4] Проверяю сервисы...
timeout /t 8 /nobreak >nul

curl -s -m 10 http://localhost:8100/health >nul 2>&1 && (echo       scraper: OK) || (echo       scraper: НЕ отвечает)
curl -s -m 10 http://localhost:5678/healthz >nul 2>&1 && (echo       n8n:     OK) || (echo       n8n:     НЕ отвечает)

echo.
echo ============================================
echo   Панель n8n: http://localhost:5678
echo   API/docs:   http://localhost:8100/docs
if defined URL echo   Публично:   !URL!
echo ============================================
echo.

start "" http://localhost:5678
echo Сервисы работают в фоне, окно можно закрыть.
timeout /t 6 /nobreak >nul
endlocal
