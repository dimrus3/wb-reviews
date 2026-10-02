"""
Модели данных для системы обзоров Wildberries
"""
from enum import Enum
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ProductStatus(str, Enum):
    """Статусы обработки товара"""
    QUEUED = "queued"
    COLLECTING = "collecting"
    ANALYZING = "analyzing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    SKIPPED = "skipped"


class Review(BaseModel):
    """Модель отзыва"""
    text: Optional[str] = None
    pros: Optional[str] = None
    cons: Optional[str] = None
    rating: Optional[int] = None
    date: Optional[str] = None
    color: Optional[str] = None
    size: Optional[str] = None
    variant_article: Optional[str] = None
    is_verified_purchase: Optional[bool] = None
    is_extended: Optional[bool] = None
    seller_reply: Optional[str] = None


class Question(BaseModel):
    """Модель вопроса"""
    question: str
    answer: Optional[str] = None
    date: Optional[str] = None
    variant_article: Optional[str] = None


class ProductData(BaseModel):
    """Собранные данные о товаре"""
    article: str
    name: Optional[str] = None
    brand: Optional[str] = None
    variant: Optional[str] = None
    characteristics: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    price: Optional[float] = None
    price_conditions: Optional[str] = None
    currency: Optional[str] = "RUB"
    price_checked_at: Optional[datetime] = None
    overall_rating: Optional[float] = None
    ratings_count: Optional[int] = None
    reviews: List[Review] = Field(default_factory=list)
    reviews_collected: int = 0
    reviews_complete: bool = False
    questions: List[Question] = Field(default_factory=list)
    questions_collected: int = 0
    questions_complete: bool = False
    collected_at: datetime = Field(default_factory=datetime.utcnow)
    imtId: Optional[str] = None


class Analysis(BaseModel):
    """Результат анализа товара"""
    product_characteristics: str
    customer_experience: str
    seller_claims: str
    pros_summary: List[str]
    cons_summary: List[str]
    questions_insights: List[str]
    evidence_ids: Dict[str, List[int]] = Field(default_factory=dict)
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)


class Post(BaseModel):
    """Telegram-пост"""
    text: str
    version: int = 1
    created_at: datetime = Field(default_factory=datetime.utcnow)
    approved: bool = False
    approved_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    channel_message_id: Optional[str] = None


class AddProductRequest(BaseModel):
    """Запрос на добавление товара"""
    input: str = Field(..., description="Артикул или ссылка Wildberries")
    user_id: int = Field(..., description="Telegram user ID")


class QueueStatusResponse(BaseModel):
    """Статус очереди"""
    queued: int
    collecting: int
    analyzing: int
    awaiting_approval: int
    approved: int
    failed: int
    total: int


class SystemStatusResponse(BaseModel):
    """Статус системы"""
    publications_paused: bool
    next_publication_slots: List[str]
    today_published: int
    queue: QueueStatusResponse
