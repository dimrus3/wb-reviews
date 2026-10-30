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
                    notified_at TIMESTAMP,
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
                    date DATE PRIMARY KEY,
                    count INTEGER DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
                CREATE INDEX IF NOT EXISTS idx_products_article ON products(article);
                CREATE INDEX IF NOT EXISTS idx_posts_approved ON posts(approved, published_at);
                CREATE INDEX IF NOT EXISTS idx_publications_date ON publications(published_at);
                CREATE INDEX IF NOT EXISTS idx_llm_requests_date ON llm_requests(date);
            """)
            self._migrate_llm_requests(conn)
            self._migrate_posts_notified(conn)

    def _migrate_posts_notified(self, conn):
        """Добавить posts.notified_at в старые БД.

        Без этого флага Post Approval каждые 5 минут присылает владельцу
        один и тот же пост заново, пока тот не одобрен.
        """
        cols = [row[1] for row in conn.execute("PRAGMA table_info(posts)")]
        if "notified_at" not in cols:
            conn.execute("ALTER TABLE posts ADD COLUMN notified_at TIMESTAMP")

    def _migrate_llm_requests(self, conn):
        """Привести старую схему llm_requests (id + неуникальная date) к date PRIMARY KEY.

        Без UNIQUE(date) запрос `INSERT ... ON CONFLICT(date)` падал с
        sqlite3.OperationalError и ронял пайплайн на шаге анализа.
        """
        cols = [row[1] for row in conn.execute("PRAGMA table_info(llm_requests)")]
        if "id" not in cols:
            return
        conn.execute("ALTER TABLE llm_requests RENAME TO llm_requests_legacy")
        conn.execute(
            """CREATE TABLE llm_requests (
                   date DATE PRIMARY KEY,
                   count INTEGER DEFAULT 1
               )"""
        )
        conn.execute(
            """INSERT INTO llm_requests (date, count)
               SELECT date, SUM(count) FROM llm_requests_legacy GROUP BY date"""
        )
        conn.execute("DROP TABLE llm_requests_legacy")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_requests_date ON llm_requests(date)"
        )

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

    def get_all_articles(self) -> List[str]:
        """Все артикулы, которые уже есть в базе (для автоподбора)."""
        with self.get_connection() as conn:
            return [row["article"] for row in
                    conn.execute("SELECT article FROM products")]

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

    def claim_for_processing(self, product_id: int) -> bool:
        """Атомарно перевести товар queued -> collecting.

        Товар может одновременно попасть в фоновую задачу /products/add и в
        queue_worker. Обрабатывает только тот, кто успел захватить, иначе
        один артикул собирался и анализировался дважды (двойной расход LLM).
        """
        with self.get_connection() as conn:
            cursor = conn.execute(
                """UPDATE products SET status = ?, last_updated = CURRENT_TIMESTAMP
                   WHERE id = ? AND status = ?""",
                (ProductStatus.COLLECTING.value, product_id, ProductStatus.QUEUED.value)
            )
            return cursor.rowcount == 1

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

    def get_post(self, post_id: int) -> Optional[Dict[str, Any]]:
        """Получить пост по id (для дублирования в MAX и ручных проверок)."""
        with self.get_connection() as conn:
            row = conn.execute(
                """SELECT p.*, pr.article FROM posts p
                   JOIN products pr ON p.product_id = pr.id
                   WHERE p.id = ?""",
                (post_id,)
            ).fetchone()
            return dict(row) if row else None

    def approve_post(self, post_id: int):
        """Одобрить пост, сняв одобрение с остальных версий того же товара.

        Без снятия одобрения v1 остаётся approved=1 и /publish может взять
        именно её вместо свежей v2 — товар уходит в канал дважды.
        """
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT product_id FROM posts WHERE id = ?", (post_id,)
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE posts SET approved = 0, approved_at = NULL
                       WHERE product_id = ? AND id <> ?""",
                    (row["product_id"], post_id)
                )
            conn.execute(
                "UPDATE posts SET approved = 1, approved_at = CURRENT_TIMESTAMP WHERE id = ?",
                (post_id,)
            )

    def get_publications_on(self, utc_date: str) -> int:
        """Успешные публикации за дату UTC (YYYY-MM-DD) — так же, как считает лимит."""
        with self.get_connection() as conn:
            row = conn.execute(
                """SELECT COUNT(*) FROM publications
                   WHERE DATE(published_at) = ? AND status = 'success'""",
                (utc_date,)
            ).fetchone()
            return row[0] if row else 0

    def get_queue_snapshot(self) -> Dict[str, Any]:
        """Сколько постов ждут одобрения и какие стоят в очереди на публикацию.

        Порядок очереди совпадает с get_next_approved_post (по approved_at),
        чтобы прогноз времени выхода сходился с тем, что реально опубликуется.
        """
        with self.get_connection() as conn:
            awaiting = conn.execute(
                "SELECT COUNT(*) FROM products WHERE status = ?",
                (ProductStatus.AWAITING_APPROVAL.value,)
            ).fetchone()[0]
            queued = [r[0] for r in conn.execute(
                """SELECT p.product_id FROM posts p
                   JOIN products pr ON p.product_id = pr.id
                   WHERE p.approved = 1
                   AND p.published_at IS NULL
                   AND pr.status = ?
                   AND p.version = (SELECT MAX(p2.version) FROM posts p2
                                    WHERE p2.product_id = p.product_id)
                   ORDER BY p.approved_at ASC""",
                (ProductStatus.APPROVED.value,)
            )]
            return {"awaiting": awaiting, "queued_product_ids": queued}

    def get_next_approved_post(self, claim: bool = True) -> Optional[Dict[str, Any]]:
        """
        Получить следующий одобренный пост для публикации
        С защитой от параллельных запусков

        claim=False — только посмотреть, НЕ помечать товар как publishing.
        Нужно для dry-run: иначе пробная публикация «съедает» одобренный пост
        и настоящая публикация его уже не находит.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                """SELECT p.*, pr.article FROM posts p
                   JOIN products pr ON p.product_id = pr.id
                   WHERE p.approved = 1
                   AND p.published_at IS NULL
                   AND pr.status = ?
                   AND p.version = (SELECT MAX(p2.version) FROM posts p2
                                    WHERE p2.product_id = p.product_id)
                   ORDER BY p.approved_at ASC
                   LIMIT 1""",
                (ProductStatus.APPROVED.value,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            if not claim:
                return dict(row)

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

    def get_llm_requests_today(self) -> int:
        """Сколько запросов к LLM сделано сегодня (та же таблица, что пишет increment)."""
        today = date.today().isoformat()
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT count FROM llm_requests WHERE date = ?", (today,)
            ).fetchone()
            return row["count"] if row else 0

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

    def get_awaiting_approval(self, unnotified_only: bool = False) -> List[Dict[str, Any]]:
        """Получить товары, ожидающие одобрения.

        unnotified_only=True — только те, что владельцу ещё не отправляли:
        n8n опрашивает эндпоинт каждые 5 минут, без этого фильтра один и тот
        же пост приходит заново до одобрения.
        """
        query = """SELECT pr.id as product_id, pr.article, p.id as post_id, p.text, p.version,
                          pd.data_json
                   FROM products pr
                   JOIN posts p ON pr.id = p.product_id
                   LEFT JOIN product_data pd
                          ON pd.id = (SELECT MAX(id) FROM product_data WHERE product_id = pr.id)
                   WHERE pr.status = ?
                   AND p.id = (SELECT MAX(id) FROM posts WHERE product_id = pr.id)"""
        if unnotified_only:
            query += " AND p.notified_at IS NULL"
        query += " ORDER BY p.created_at ASC"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (ProductStatus.AWAITING_APPROVAL.value,))
            posts = []
            for row in cursor.fetchall():
                item = dict(row)
                # image_url тянем из последнего снимка карточки — n8n вставляет
                # его в Telegram-сообщение как фото с подписью-постом.
                data_json = item.pop("data_json", None)
                item["image_url"] = None
                if data_json:
                    try:
                        item["image_url"] = json.loads(data_json).get("image_url")
                    except (ValueError, TypeError):
                        pass
                posts.append(item)
            return posts

    def mark_posts_notified(self, post_ids: List[int]) -> int:
        """Отметить посты как отправленные владельцу на одобрение."""
        if not post_ids:
            return 0
        placeholders = ",".join("?" * len(post_ids))
        with self.get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE posts SET notified_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                post_ids,
            )
            return cursor.rowcount
