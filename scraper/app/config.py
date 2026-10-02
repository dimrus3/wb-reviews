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
    llm_timeout: int = Field(default=120, description="Таймаут запроса в секундах")
    llm_max_tokens: int = Field(default=2000, description="Максимум токенов в ответе")
    llm_daily_limit: int = Field(default=100, description="Дневной лимит запросов к модели")

    # Playwright
    playwright_headless: bool = True
    playwright_timeout: int = 60000  # мс
    scraper_max_reviews: int = 100
    scraper_max_questions: int = 50
    scraper_retry_attempts: int = 3
    scraper_retry_delay: int = 5  # секунд

    # Публикации
    publication_timezone: str = "Asia/Novosibirsk"
    publication_times: list = ["11:00", "19:00"]
    max_publications_per_day: int = 2
    publications_paused: bool = False

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
