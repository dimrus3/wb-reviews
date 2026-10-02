"""
FastAPI приложение для системы обзоров Wildberries
"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import get_settings
from .database import Database
from .scraper import WildberriesScraper
from .analyzer import Analyzer
from .models import (
    ProductStatus, AddProductRequest, QueueStatusResponse,
    SystemStatusResponse
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация
settings = get_settings()
app = FastAPI(title="Wildberries Reviews System")
db = Database("./data/wb_reviews.db")
scraper = WildberriesScraper(
    headless=settings.playwright_headless,
    timeout=settings.playwright_timeout,
    max_reviews=settings.scraper_max_reviews,
    max_questions=settings.scraper_max_questions,
    retry_attempts=settings.scraper_retry_attempts,
    retry_delay=settings.scraper_retry_delay
)
analyzer = Analyzer(settings)

# Загрузка промптов
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_prompt(filename: str) -> str:
    """Загрузить промпт из файла"""
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding='utf-8')
    logger.warning(f"Промпт {filename} не найден")
    return ""


ANALYZE_PROMPT = load_prompt("analyze.txt")
POST_PROMPT = load_prompt("generate_post.txt")


class RevisionRequest(BaseModel):
    """Запрос на переделку поста"""
    product_id: int
    revision_note: str


class ApproveRequest(BaseModel):
    """Запрос на одобрение поста"""
    product_id: int


class PublishRequest(BaseModel):
    """Запрос на публикацию"""
    dry_run: bool = True


@app.on_event("startup")
async def startup_event():
    """Инициализация при старте"""
    # Создаём директорию для данных
    Path("./data").mkdir(exist_ok=True)
    logger.info("Система запущена")
    logger.info(f"Режим dry-run: {settings.dry_run}")


@app.get("/health")
async def health_check():
    """Проверка здоровья системы"""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.post("/products/add")
async def add_product(request: AddProductRequest, background_tasks: BackgroundTasks):
    """
    Добавить товар в очередь
    """
    # Проверка прав
    if request.user_id != settings.allowed_user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещён")

    input_text = request.input.strip()

    # Извлечь артикул
    article = None
    if input_text.isdigit():
        article = input_text
    elif "wildberries.ru" in input_text.lower():
        # Проверка безопасности URL
        if not scraper.validate_url(input_text, settings.allowed_domains):
            raise HTTPException(status_code=400, detail="Недопустимый URL")
        article = scraper.extract_article_from_url(input_text)

    if not article or not scraper.validate_article(article):
        raise HTTPException(status_code=400, detail="Некорректный артикул или ссылка")

    # Добавление в БД
    product_id, is_new = db.add_product(article, request.user_id)

    if not is_new:
        # Проверка статуса
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM products WHERE id = ?", (product_id,))
            row = cursor.fetchone()
            status = row["status"] if row else None

            if status == ProductStatus.PUBLISHED.value:
                return {
                    "success": False,
                    "message": f"Товар {article} уже был опубликован. Для повторной обработки используйте отдельную команду.",
                    "product_id": product_id,
                    "article": article
                }
            elif status == ProductStatus.FAILED.value:
                return {
                    "success": True,
                    "message": f"Товар {article} был сброшен и добавлен в очередь повторно",
                    "product_id": product_id,
                    "article": article,
                    "is_new": False
                }
            else:
                return {
                    "success": True,
                    "message": f"Товар {article} уже в очереди (статус: {status})",
                    "product_id": product_id,
                    "article": article,
                    "is_new": False
                }

    # Запуск обработки в фоне
    background_tasks.add_task(process_product, product_id, article)

    return {
        "success": True,
        "message": f"Товар {article} добавлен в очередь",
        "product_id": product_id,
        "article": article,
        "is_new": True
    }


@app.get("/queue/status", response_model=QueueStatusResponse)
async def get_queue_status():
    """Получить статус очереди"""
    return db.get_queue_status()


@app.get("/system/status", response_model=SystemStatusResponse)
async def get_system_status():
    """Получить статус системы"""
    queue = db.get_queue_status()

    # Получаем статус паузы
    paused_state = db.get_system_state("publications_paused")
    paused = paused_state == "true" if paused_state else settings.publications_paused

    # Следующие слоты публикации
    from datetime import datetime, timedelta
    import pytz
    tz = pytz.timezone(settings.publication_timezone)
    now = datetime.now(tz)

    next_slots = []
    for time_str in settings.publication_times:
        hour, minute = map(int, time_str.split(':'))
        slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if slot_time <= now:
            slot_time += timedelta(days=1)
        next_slots.append(slot_time.strftime("%Y-%m-%d %H:%M %Z"))

    # Количество публикаций сегодня
    today_published = db.get_publications_today(settings.publication_timezone)

    return SystemStatusResponse(
        publications_paused=paused,
        next_publication_slots=next_slots[:2],
        today_published=today_published,
        queue=queue
    )


@app.post("/publications/pause")
async def pause_publications(user_id: int):
    """Приостановить публикации"""
    if user_id != settings.allowed_user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещён")

    db.set_system_state("publications_paused", "true")
    return {"success": True, "message": "Публикации приостановлены"}


@app.post("/publications/resume")
async def resume_publications(user_id: int):
    """Возобновить публикации"""
    if user_id != settings.allowed_user_id:
        raise HTTPException(status_code=403, detail="Доступ запрещён")

    db.set_system_state("publications_paused", "false")
    return {"success": True, "message": "Публикации возобновлены"}


@app.post("/posts/approve")
async def approve_post(request: ApproveRequest):
    """Одобрить пост"""
    # Получаем последний пост продукта
    post = db.get_latest_post(request.product_id)
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")

    db.approve_post(post["id"])
    db.update_product_status(request.product_id, ProductStatus.APPROVED)

    return {
        "success": True,
        "message": "Пост одобрен и готов к публикации"
    }


@app.post("/posts/revise")
async def revise_post(request: RevisionRequest, background_tasks: BackgroundTasks):
    """Запросить переделку поста"""
    # Запуск переделки в фоне
    background_tasks.add_task(regenerate_post, request.product_id, request.revision_note)

    return {
        "success": True,
        "message": "Пост будет переделан с учётом замечаний"
    }


@app.post("/posts/skip")
async def skip_post(product_id: int):
    """Пропустить товар"""
    db.update_product_status(product_id, ProductStatus.SKIPPED)
    return {
        "success": True,
        "message": "Товар пропущен"
    }


@app.get("/posts/awaiting-approval")
async def get_awaiting_approval():
    """Получить посты, ожидающие одобрения"""
    posts = db.get_awaiting_approval()
    return {"posts": posts}


@app.post("/publish")
async def publish_next(request: PublishRequest):
    """
    Опубликовать следующий одобренный пост
    Вызывается по расписанию из n8n
    """
    # Проверка паузы
    paused_state = db.get_system_state("publications_paused")
    if paused_state == "true":
        return {
            "success": False,
            "message": "Публикации приостановлены"
        }

    # Проверка дневного лимита
    today_count = db.get_publications_today(settings.publication_timezone)
    if today_count >= settings.max_publications_per_day:
        return {
            "success": False,
            "message": f"Достигнут дневной лимит публикаций ({settings.max_publications_per_day})"
        }

    # Получить следующий пост
    post = db.get_next_approved_post()
    if not post:
        return {
            "success": False,
            "message": "Нет готовых постов для публикации"
        }

    if request.dry_run or settings.dry_run:
        logger.info(f"DRY RUN: Публикация поста {post['id']} для товара {post['article']}")
        return {
            "success": True,
            "dry_run": True,
            "message": "Dry run: пост не был опубликован",
            "post_id": post["id"],
            "article": post["article"],
            "text_preview": post["text"][:200] + "..."
        }

    # Реальная публикация через n8n webhook будет обрабатываться отдельно
    return {
        "success": True,
        "post_id": post["id"],
        "product_id": post["product_id"],
        "article": post["article"],
        "text": post["text"]
    }


@app.post("/publish/confirm")
async def confirm_publication(post_id: int, product_id: int,
                             channel_id: str, message_id: str):
    """
    Подтвердить успешную публикацию
    Вызывается из n8n после отправки в Telegram
    """
    db.mark_post_published(post_id, product_id, channel_id, message_id)
    logger.info(f"Публикация подтверждена: post_id={post_id}, message_id={message_id}")

    return {"success": True}


@app.post("/publish/uncertain")
async def mark_publication_uncertain(post_id: int, product_id: int, error: str):
    """
    Отметить неопределённый результат публикации
    """
    db.mark_publication_uncertain(post_id, product_id, error)
    logger.warning(f"Неопределённый результат публикации post_id={post_id}: {error}")

    return {
        "success": True,
        "message": "Статус отмечен как неопределённый, требуется проверка владельцем"
    }


async def process_product(product_id: int, article: str):
    """
    Полная обработка товара: сбор → анализ → генерация поста
    """
    try:
        logger.info(f"Начинаем обработку товара {article} (ID: {product_id})")

        # 1. Сбор данных
        db.update_product_status(product_id, ProductStatus.COLLECTING)
        product_data = await scraper.scrape_product(article)
        db.save_product_data(product_id, product_data)
        logger.info(f"Данные собраны для {article}")

        # 2. Анализ
        db.update_product_status(product_id, ProductStatus.ANALYZING)

        # Проверка лимита LLM
        if not await analyzer.check_daily_limit(db):
            raise Exception("Достигнут дневной лимит запросов к LLM")

        analysis = await analyzer.analyze_product(product_data, ANALYZE_PROMPT)
        await analyzer.increment_request_count(db)
        db.save_analysis(product_id, analysis)
        logger.info(f"Анализ выполнен для {article}")

        # 3. Генерация поста
        if not await analyzer.check_daily_limit(db):
            raise Exception("Достигнут дневной лимит запросов к LLM")

        post_text = await analyzer.generate_post(product_data, analysis, POST_PROMPT)
        await analyzer.increment_request_count(db)

        post_id = db.save_post(product_id, post_text, version=1)
        db.update_product_status(product_id, ProductStatus.AWAITING_APPROVAL)

        logger.info(f"Пост создан для {article}, ожидает одобрения (post_id: {post_id})")

    except Exception as e:
        logger.error(f"Ошибка при обработке товара {article}: {e}")
        db.update_product_status(product_id, ProductStatus.FAILED, str(e))


async def regenerate_post(product_id: int, revision_note: str):
    """
    Переделать пост с учётом замечаний
    """
    try:
        logger.info(f"Переделываем пост для product_id={product_id}")

        # Получить данные и анализ
        product_data = db.get_product_data(product_id)
        analysis = db.get_analysis(product_id)

        if not product_data or not analysis:
            raise Exception("Данные или анализ не найдены")

        # Получить текущую версию
        current_post = db.get_latest_post(product_id)
        version = current_post["version"] + 1 if current_post else 1

        # Проверка лимита
        if not await analyzer.check_daily_limit(db):
            raise Exception("Достигнут дневной лимит запросов к LLM")

        # Генерация новой версии
        post_text = await analyzer.generate_post(
            product_data, analysis, POST_PROMPT, revision_note
        )
        await analyzer.increment_request_count(db)

        # Сохранение
        post_id = db.save_post(product_id, post_text, version, revision_note)
        db.update_product_status(product_id, ProductStatus.AWAITING_APPROVAL)

        logger.info(f"Новая версия поста создана (v{version}, post_id: {post_id})")

    except Exception as e:
        logger.error(f"Ошибка при переделке поста product_id={product_id}: {e}")
        db.update_product_status(product_id, ProductStatus.FAILED, str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
