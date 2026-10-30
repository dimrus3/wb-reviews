"""
Конфигурация системы
"""
import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Настройки приложения"""

    # База данных
    database_url: str = "sqlite:///./data/wb_reviews.db"

    # API сервера
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Telegram
    allowed_user_id: int = Field(..., description="Telegram user ID владельца")
    telegram_channel_id: str = Field(..., description="ID канала для публикации")

    # OpenAI-совместимый API для генерации текста
    llm_base_url: str = Field(..., description="Base URL API (например, https://api.openai.com/v1)")
    llm_api_key: str = Field(..., description="API ключ")
    llm_model: str = Field(default="gpt-4", description="Название модели")
    llm_fallback_model: str = Field(default="", description="Запасная модель, если основная не ответила (пусто — выключено)")
    llm_timeout: int = Field(default=300, description="Таймаут запроса в секундах (8000 max_tokens на grok держит ответ дольше 120s)")
    llm_max_tokens: int = Field(default=2000, description="Максимум токенов в ответе")
    llm_daily_limit: int = Field(default=100, description="Дневной лимит запросов к модели")

    # Playwright
    playwright_headless: bool = True
    playwright_timeout: int = 60000  # мс
    scraper_max_reviews: int = 100
    scraper_max_questions: int = 50
    scraper_retry_attempts: int = 3
    scraper_retry_delay: int = 5  # секунд
    min_reviews_for_post: int = Field(default=5, description="Меньше отзывов — пост не генерируется")

    # Публикации
    publication_timezone: str = "Asia/Novosibirsk"
    publication_times: list = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00",
                               "15:00", "16:00", "17:00", "18:00", "19:00", "20:00", "21:00"]
    max_publications_per_day: int = 15
    publications_paused: bool = False

    # MAX (max.ru) — дублирование постов в мессенджер MAX
    max_bot_token: str = Field(default="", description="Токен бота MAX от @MasterBot")
    max_chat_id: str = Field(default="", description="ID канала/чата MAX (бот должен быть админом)")
    max_api_base: str = Field(default="https://platform-api2.max.ru", description="Базовый URL Bot API MAX")
    max_publish: bool = Field(default=True, description="Дублировать посты в MAX")

    # Безопасность
    allowed_domains: list = ["wildberries.ru", "www.wildberries.ru"]

    # Режим работы
    dry_run: bool = Field(default=True, description="Режим без реальной публикации")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


def get_settings() -> Settings:
    """Получить настройки"""
    return Settings()
