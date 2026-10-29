"""Автоподбор товаров Wildberries по сезонным нишам.

Задача: не ждать `/add` от владельца, а каждый день самому брать 5-10 товаров
«по смыслу» — из категорий, которые сейчас в сезоне.

Поисковый API WB (search.wb.ru) отдаёт 403, поэтому ищем через Playwright:
страница поиска переживает антибот так же, как карточка товара, нужно лишь
дождаться появления article.product-card (иногда с перезагрузкой).
"""
import asyncio
import logging
import random
import re
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright

from .niches import YEAR_ROUND, current_season

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CARD_SELECTOR = "article.product-card"


class ProductDiscovery:
    """Поиск товаров на WB по текстовому запросу."""

    def __init__(self, headless: bool = True, timeout_ms: int = 60000):
        self.headless = headless
        self.timeout_ms = timeout_ms

    async def _search_once(self, page, query: str) -> List[Dict[str, Any]]:
        """Открыть поиск и вытащить карточки из выдачи."""
        url = ("https://www.wildberries.ru/catalog/0/search.aspx?search="
               + query.replace(" ", "+"))
        for attempt in range(3):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                found = 0
                for _ in range(30):
                    await page.wait_for_timeout(1000)
                    try:
                        found = await page.evaluate(
                            f"() => document.querySelectorAll('{CARD_SELECTOR}').length")
                    except Exception:
                        # Навигация во время антибот-челленджа — ждём дальше.
                        found = 0
                    if found:
                        break
                if not found:
                    continue

                # Классы в разметке WB меняются между релизами, поэтому берём
                # весь текст карточки и разбираем его регулярками — так надёжнее.
                return await page.evaluate(f"""() => {{
                    return Array.from(document.querySelectorAll('{CARD_SELECTOR}')).map(card => {{
                        const href = (card.querySelector('a') || {{}}).href || '';
                        const nm = card.getAttribute('data-nm-id')
                            || (href.match(/catalog\\/(\\d+)/) || [])[1] || null;
                        const text = (card.innerText || '').replace(/\\n+/g, ' | ');
                        // Название надёжнее всего в aria-label ссылки карточки:
                        // innerText содержит бейджи «АКЦИЯ 11.11», «с WB Кошельком».
                        const link = card.querySelector('a[aria-label]');
                        return {{
                            article: nm,
                            title: link ? (link.getAttribute('aria-label') || '').trim() : '',
                            text: text
                        }};
                    }});
                }}""")
            except Exception as e:
                logger.debug(f"Поиск «{query}» попытка {attempt + 1}: {type(e).__name__}: {e}")
                await asyncio.sleep(2)
        return []

    @staticmethod
    def _parse_from_text(text: str) -> Dict[str, Any]:
        """Достать цену, рейтинг, число отзывов и название из текста карточки."""
        t = text or ""
        # Цена: последнее число перед ₽ (в карточке бывает старая и новая цена)
        prices = re.findall(r"(\d[\d\s\u00a0]*)\s*₽", t)
        price = None
        if prices:
            digits = re.sub(r"[^\d]", "", prices[-1])
            price = float(digits) if digits else None
        rating = None
        m = re.search(r"([45][.,]\d)", t)
        if m:
            rating = float(m.group(1).replace(",", "."))
        feedbacks = None
        m = re.search(r"(\d[\d\s\u00a0]*)\s*(?:отзыв|оцен)", t, re.I)
        if m:
            digits = re.sub(r"[^\d]", "", m.group(1))
            feedbacks = int(digits) if digits else None
        return {"price": price, "rating": rating, "feedbacks": feedbacks}

    @staticmethod
    def _parse_price(raw: str) -> Optional[float]:
        digits = re.sub(r"[^\d]", "", raw or "")
        return float(digits) if digits else None

    @staticmethod
    def _parse_rating(raw: str) -> Optional[float]:
        m = re.search(r"(\d[.,]\d)", raw or "")
        return float(m.group(1).replace(",", ".")) if m else None

    @staticmethod
    def _parse_count(raw: str) -> Optional[int]:
        m = re.search(r"([\d\s\u00a0]+)", raw or "")
        if not m:
            return None
        digits = re.sub(r"[^\d]", "", m.group(1))
        return int(digits) if digits else None

    async def find(
        self,
        queries: List[str],
        wanted: int = 8,
        min_rating: float = 4.5,
        min_feedbacks: int = 50,
        min_price: float = 200,
        max_price: float = 8000,
        max_per_query: int = 2,
        exclude: Optional[set] = None,
    ) -> List[Dict[str, Any]]:
        """Пройти по запросам и вернуть до `wanted` подходящих товаров."""
        exclude = exclude or set()
        picked: List[Dict[str, Any]] = []
        seen: set = set()

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            ctx = await browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent=UA,
                locale="ru-RU",
                extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
            )
            page = await ctx.new_page()

            try:
                for query in queries:
                    if len(picked) >= wanted:
                        break
                    raw = await self._search_once(page, query)
                    if not raw:
                        continue
                    # Перемешиваем: иначе каждый день одни и те же лидеры выдачи.
                    random.shuffle(raw)
                    per_query = 0
                    for item in raw:
                        if len(picked) >= wanted or per_query >= max_per_query:
                            break
                        nm = item.get("article")
                        if not nm or nm in seen or nm in exclude:
                            continue
                        parsed = self._parse_from_text(item.get("text", ""))
                        rating = parsed["rating"]
                        feedbacks = parsed["feedbacks"]
                        price = parsed["price"]
                        if rating is not None and rating < min_rating:
                            continue
                        if feedbacks is not None and feedbacks < min_feedbacks:
                            continue
                        if price is not None and not (min_price <= price <= max_price):
                            continue
                        seen.add(nm)
                        per_query += 1
                        picked.append({
                            "article": nm,
                            "name": (item.get("title") or "").strip(),
                            "brand": "",
                            "price": price,
                            "rating": rating,
                            "feedbacks": feedbacks,
                            "query": query,
                        })
            finally:
                await browser.close()

        return picked


def build_queries(limit: int = 12) -> List[str]:
    """Случайная выборка запросов: сезон + круглогодичный пул."""
    season = current_season()
    pool = list(season["queries"]) + list(YEAR_ROUND)
    # 70% сезонных, 30% круглогодичных — канал остаётся сезонным, но не зацикливается
    seasonal_n = min(len(season["queries"]), max(4, int(limit * 0.7)))
    picked = random.sample(season["queries"], seasonal_n) if season["queries"] else []
    rest = [q for q in YEAR_ROUND if q not in picked]
    picked += random.sample(rest, min(len(rest), max(0, limit - len(picked))))
    random.shuffle(picked)
    return picked
