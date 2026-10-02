"""
Работа с базой данных SQLite
"""
import sqlite3
import json
from datetime import datetime, date
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager
from .models import ProductStatus, ProductData, Analysis, Post, QueueStatusResponse


class Database:
    """Управление базой данных"""

    def __init__(self, db_path: str = "./data/wb_reviews.db"):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def get_connection(self):
        """Контекстный менеджер для соединения"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Инициализация схемы БД"""
        with self.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    marketplace TEXT NOT NULL DEFAULT 'wildberries',
                    article TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    added_by_user_id INTEGER,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    collection_attempts INTEGER DEFAULT 0,
                    error_message TEXT,
                    UNIQUE(marketplace, article)
                );

                CREATE TABLE IF NOT EXISTS product_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    data_json TEXT NOT NULL,
                    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id)
                );

                CREATE TABLE IF NOT EXISTS analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    analysis_json TEXT NOT NULL,
                    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id)
                );

                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    approved BOOLEAN DEFAULT 0,
                    approved_at TIMESTAMP,
                    published_at TIMESTAMP,
                    channel_id TEXT,
                    message_id TEXT,
                    revision_note TEXT,
                    FOREIGN KEY (product_id) REFERENCES products(id)
                );

                CREATE TABLE IF NOT EXISTS publications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER NOT NULL,
                    published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    channel_id TEXT NOT NULL,
                    message_id TEXT,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    FOREIGN KEY (post_id) REFERENCES posts(id)
                );

                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS llm_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date DATE NOT NULL,
                    count INTEGER DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
                CREATE INDEX IF NOT EXISTS idx_products_article ON products(article);
                CREATE INDEX IF NOT EXISTS idx_posts_approved ON posts(approved, published_at);
                CREATE INDEX IF NOT EXISTS idx_publications_date ON publications(published_at);
                CREATE INDEX IF NOT EXISTS idx_llm_requests_date ON llm_requests(date);
            """)

    def add_product(self, article: str, user_id: int, marketplace: str = "wildberries") -> Tuple[int, bool]:
        """
        Добавить товар в очередь
        Returns: (product_id, is_new)
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Проверка существования
            cursor.execute(
                "SELECT id, status FROM products WHERE marketplace = ? AND article = ?",
                (marketplace, article)
            )
            existing = cursor.fetchone()

            if existing:
                product_id = existing["id"]
                status = existing["status"]

                if status == ProductStatus.PUBLISHED.value:
                    return product_id, False  # Уже опубликован

                # Если провалился - сбросить статус
                if status == ProductStatus.FAILED.value:
                    cursor.execute(
                        "UPDATE products SET status = ?, last_updated = CURRENT_TIMESTAMP WHERE id = ?",
                        (ProductStatus.QUEUED.value, product_id)
                    )

                return product_id, False

            # Добавить новый
            cursor.execute(
                """INSERT INTO products (marketplace, article, status, added_by_user_id)
                   VALUES (?, ?, ?, ?)""",
                (marketplace, article, ProductStatus.QUEUED.value, user_id)
            )
            return cursor.lastrowid, True

    def get_next_queued(self) -> Optional[Dict[str, Any]]:
        """Получить следующий товар для обработки"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM products
                   WHERE status = ?
                   ORDER BY added_at ASC LIMIT 1""",
                (ProductStatus.QUEUED.value,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_product_status(self, product_id: int, status: ProductStatus, error_message: Optional[str] = None):
        """Обновить статус товара"""
        with self.get_connection() as conn:
            if error_message:
                conn.execute(
                    """UPDATE products
                       SET status = ?, last_updated = CURRENT_TIMESTAMP, error_message = ?
                       WHERE id = ?""",
                    (status.value, error_message, product_id)
                )
            else:
                conn.execute(
                    """UPDATE products
                       SET status = ?, last_updated = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (status.value, product_id)
                )

    def save_product_data(self, product_id: int, data: ProductData):
        """Сохранить собранные данные"""
        with self.get_connection() as conn:
            conn.execute(
                "INSERT INTO product_data (product_id, data_json) VALUES (?, ?)",
                (product_id, data.model_dump_json())
            )

    def get_product_data(self, product_id: int) -> Optional[ProductData]:
        """Получить данные товара"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT data_json FROM product_data
                   WHERE product_id = ?
                   ORDER BY collected_at DESC LIMIT 1""",
                (product_id,)
            )
            row = cursor.fetchone()
            if row:
                return ProductData.model_validate_json(row["data_json"])
            return None

    def save_analysis(self, product_id: int, analysis: Analysis):
        """Сохранить анализ"""
        with self.get_connection() as conn:
            conn.execute(
                "INSERT INTO analyses (product_id, analysis_json) VALUES (?, ?)",
                (product_id, analysis.model_dump_json())
            )

    def get_analysis(self, product_id: int) -> Optional[Analysis]:
        """Получить анализ"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT analysis_json FROM analyses
                   WHERE product_id = ?
                   ORDER BY analyzed_at DESC LIMIT 1""",
                (product_id,)
            )
            row = cursor.fetchone()
            if row:
                return Analysis.model_validate_json(row["analysis_json"])
            return None

    def save_post(self, product_id: int, text: str, version: int = 1, revision_note: Optional[str] = None) -> int:
        """Сохранить пост"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO posts (product_id, version, text, revision_note)
                   VALUES (?, ?, ?, ?)""",
                (product_id, version, text, revision_note)
            )
            return cursor.lastrowid

    def get_latest_post(self, product_id: int) -> Optional[Dict[str, Any]]:
        """Получить последнюю версию поста"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM posts
                   WHERE product_id = ?
                   ORDER BY version DESC LIMIT 1""",
                (product_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def approve_post(self, post_id: int):
        """Одобрить пост"""
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE posts SET approved = 1, approved_at = CURRENT_TIMESTAMP WHERE id = ?",
                (post_id,)
            )

    def get_next_approved_post(self) -> Optional[Dict[str, Any]]:
        """
        Получить следующий одобренный пост для публикации
        С защитой от параллельных запусков
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """SELECT p.*, pr.article FROM posts p
                   JOIN products pr ON p.product_id = pr.id
                   WHERE p.approved = 1
                   AND p.published_at IS NULL
                   AND pr.status = ?
                   ORDER BY p.approved_at ASC
                   LIMIT 1""",
                (ProductStatus.APPROVED.value,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            post_id = row["id"]
            product_id = row["product_id"]

            # Атомарно установить статус publishing
            cursor.execute(
                "UPDATE products SET status = ? WHERE id = ? AND status = ?",
                (ProductStatus.PUBLISHING.value, product_id, ProductStatus.APPROVED.value)
            )

            if cursor.rowcount == 0:
                return None  # Уже захвачен другим процессом

            return dict(row)

    def mark_post_published(self, post_id: int, product_id: int, channel_id: str, message_id: str):
        """Отметить пост как опубликованный"""
        with self.get_connection() as conn:
            conn.execute(
                """UPDATE posts
                   SET published_at = CURRENT_TIMESTAMP, channel_id = ?, message_id = ?
                   WHERE id = ?""",
                (channel_id, message_id, post_id)
            )
            conn.execute(
                "UPDATE products SET status = ? WHERE id = ?",
                (ProductStatus.PUBLISHED.value, product_id)
            )
            conn.execute(
                """INSERT INTO publications (post_id, channel_id, message_id, status)
                   VALUES (?, ?, ?, 'success')""",
                (post_id, channel_id, message_id)
            )

    def mark_publication_uncertain(self, post_id: int, product_id: int, error: str):
        """Отметить неопределённый результат публикации"""
        with self.get_connection() as conn:
            conn.execute(
                """INSERT INTO publications (post_id, channel_id, message_id, status, error_message)
                   VALUES (?, '', NULL, 'uncertain', ?)""",
                (post_id, error)
            )
            # Вернуть в approved для проверки владельцем
            conn.execute(
                "UPDATE products SET status = ? WHERE id = ?",
                (ProductStatus.APPROVED.value, product_id)
            )

    def get_publications_today(self, timezone_name: str = "Asia/Novosibirsk") -> int:
        """Количество публикаций сегодня"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # SQLite не поддерживает timezone, используем UTC и сравнение по дате
            cursor.execute(
                """SELECT COUNT(*) as cnt FROM publications
                   WHERE DATE(published_at) = DATE('now')
                   AND status = 'success'"""
            )
            row = cursor.fetchone()
            return row["cnt"] if row else 0

    def get_queue_status(self) -> QueueStatusResponse:
        """Статус очереди"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT status, COUNT(*) as cnt
                   FROM products
                   GROUP BY status"""
            )
            counts = {row["status"]: row["cnt"] for row in cursor.fetchall()}

            return QueueStatusResponse(
                queued=counts.get(ProductStatus.QUEUED.value, 0),
                collecting=counts.get(ProductStatus.COLLECTING.value, 0),
                analyzing=counts.get(ProductStatus.ANALYZING.value, 0),
                awaiting_approval=counts.get(ProductStatus.AWAITING_APPROVAL.value, 0),
                approved=counts.get(ProductStatus.APPROVED.value, 0),
                failed=counts.get(ProductStatus.FAILED.value, 0),
                total=sum(counts.values())
            )

    def increment_llm_requests(self) -> int:
        """Увеличить счётчик запросов к LLM, вернуть текущее значение"""
        today = date.today().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO llm_requests (date, count) VALUES (?, 1)
                   ON CONFLICT(date) DO UPDATE SET count = count + 1""",
                (today,)
            )
            cursor.execute("SELECT count FROM llm_requests WHERE date = ?", (today,))
            row = cursor.fetchone()
            return row["count"] if row else 1

    def get_system_state(self, key: str) -> Optional[str]:
        """Получить системное состояние"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else None

    def set_system_state(self, key: str, value: str):
        """Установить системное состояние"""
        with self.get_connection() as conn:
            conn.execute(
                """INSERT INTO system_state (key, value, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = CURRENT_TIMESTAMP""",
                (key, value, value)
            )

    def get_awaiting_approval(self) -> List[Dict[str, Any]]:
        """Получить товары, ожидающие одобрения"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT pr.id as product_id, pr.article, p.id as post_id, p.text, p.version
                   FROM products pr
                   JOIN posts p ON pr.id = p.product_id
                   WHERE pr.status = ?
                   AND p.id = (SELECT MAX(id) FROM posts WHERE product_id = pr.id)
                   ORDER BY p.created_at ASC""",
                (ProductStatus.AWAITING_APPROVAL.value,)
            )
            return [dict(row) for row in cursor.fetchall()]
