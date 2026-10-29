"""
Сборщик данных с Wildberries через Playwright
"""
import asyncio
import re
from typing import Optional, List, Tuple
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout
from .models import ProductData, Review, Question
import logging

logger = logging.getLogger(__name__)


class WildberriesScraper:
    """Сборщик данных Wildberries"""

    def __init__(self, headless: bool = True, timeout: int = 60000,
                 max_reviews: int = 100, max_questions: int = 50,
                 retry_attempts: int = 3, retry_delay: int = 5):
        self.headless = headless
        self.timeout = timeout
        self.max_reviews = max_reviews
        self.max_questions = max_questions
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay

    def extract_article_from_url(self, url: str) -> Optional[str]:
        """Извлечь артикул из URL Wildberries"""
        # https://www.wildberries.ru/catalog/913340343/detail.aspx
        match = re.search(r'/catalog/(\d+)', url)
        return match.group(1) if match else None

    def validate_article(self, article: str) -> bool:
        """Проверить корректность артикула"""
        return bool(re.match(r'^\d{6,12}$', article))

    def validate_url(self, url: str, allowed_domains: List[str]) -> bool:
        """Проверить безопасность URL"""
        try:
            parsed = urlparse(url)
            return parsed.netloc in allowed_domains and parsed.scheme in ['http', 'https']
        except Exception:
            return False

    async def scrape_product(self, article: str) -> ProductData:
        """
        Собрать данные о товаре
        """
        logger.info(f"Начинаем сбор данных для артикула {article}")

        product_data = ProductData(article=article)

        for attempt in range(self.retry_attempts):
            try:
                async with async_playwright() as p:
                    # Антибот WB смотрит на UA и на признаки автоматизации:
                    # обрезанный UA без версии Chrome он крутит по кругу и
                    # карточка так и не отрисовывается.
                    browser = await p.chromium.launch(
                        headless=self.headless,
                        args=['--no-sandbox', '--disable-blink-features=AutomationControlled'],
                    )
                    context = await browser.new_context(
                        viewport={'width': 1366, 'height': 900},
                        user_agent=(
                            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                            'AppleWebKit/537.36 (KHTML, like Gecko) '
                            'Chrome/131.0.0.0 Safari/537.36'
                        ),
                        locale='ru-RU',
                        extra_http_headers={'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8'},
                    )
                    page = await context.new_page()
                    page.set_default_timeout(self.timeout)

                    # Собрать данные карточки
                    await self._scrape_product_card(page, article, product_data)

                    # Собрать отзывы
                    await self._scrape_reviews(page, article, product_data)

                    # Собрать вопросы
                    await self._scrape_questions(page, article, product_data)

                    await browser.close()
                    logger.info(f"Сбор завершён: {product_data.reviews_collected} отзывов, "
                               f"{product_data.questions_collected} вопросов")
                    return product_data

            except PlaywrightTimeout as e:
                logger.warning(f"Таймаут при сборе (попытка {attempt + 1}/{self.retry_attempts}): {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)
                else:
                    raise Exception(f"Превышено число попыток сбора: таймаут")

            except Exception as e:
                logger.error(f"Ошибка при сборе (попытка {attempt + 1}/{self.retry_attempts}): {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)
                else:
                    raise

    async def _scrape_product_card(self, page: Page, article: str, product_data: ProductData):
        """Собрать данные карточки товара"""
        url = f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
        logger.info(f"Загружаем карточку: {url}")

        try:
            # WB отдаёт антибот-челлендж: первый ответ — HTTP 498 и заглушка
            # ~1.2 КБ, JS решает челлендж за 8-20 с и SPA дорисовывает карточку.
            # Поэтому networkidle не ждём (он не наступает) и не полагаемся на
            # один жёсткий таймаут — крутим ожидание h1 с перезагрузкой.
            response = await page.goto(url, wait_until='domcontentloaded')

            # Ждём, пока антибот отработает и SPA дорисует карточку.
            # Признак готовности: title содержит «купить за» либо в HTML
            # появился imtId. Просто h1 не годится — заглушка антибота тоже
            # отдаёт документ, и мы ловили имя «...» через 6 секунд.
            ready = False
            for attempt in range(3):
                for _ in range(30):  # до 30 с на попытку
                    title = (await page.title()).strip()
                    # Признак именно карточки товара — «купить за» в title.
                    # По «imtId в HTML» проверять нельзя: главная страница WB
                    # тоже содержит эту строку в JS-бандлах, и мы ловили
                    # заголовок главной вместо товара.
                    if "купить за" in title.lower():
                        ready = True
                        break
                    await page.wait_for_timeout(1000)
                if ready:
                    break
                logger.info(f"Антибот WB не пройден (попытка {attempt + 1}/3), перезагружаем карточку")
                response = await page.goto(url, wait_until='domcontentloaded')
            if not ready:
                raise Exception("Не удалось дождаться карточки товара (антибот WB не пройден)")

            # Проверка доступности
            if response and response.status == 404:
                raise Exception("Товар не найден (404)")

            # Название: в новом лэйауте .product-page__title больше нет,
            # зато title документа содержит имя товара.
            page_title = (await page.title()).strip()
            product_data.name = (re.split(r'\s+купить за', page_title)[0].strip() or None)
            if not product_data.name:
                h1_text = ((await page.locator('h1').first.text_content()) or '').strip()
                product_data.name = h1_text or None

            # Бренд: первая ссылка на /brands/ в хлебных крошках
            brand_elem = await page.query_selector('a[href*="/brands/"]')
            if brand_elem:
                product_data.brand = ((await brand_elem.text_content()) or '').strip() or None

            # Извлекаем imtId из HTML: он нужен для API отзывов.
            # Без регексов — в разметке imtId встречается в разных видах
            # ("imtId":123, imtId\":123, imtId:123), и шаблон с кавычками
            # то срабатывал, то нет.
            content = await page.content()
            _imt = None
            _pos = content.find('imtId')
            while _pos != -1:
                _digits = ''
                for _ch in content[_pos:_pos + 60]:
                    if _ch.isdigit():
                        _digits += _ch
                    elif _digits:
                        break
                if len(_digits) >= 6:
                    _imt = _digits
                    break
                _pos = content.find('imtId', _pos + 1)
            if _imt:
                product_data.imtId = _imt
                logger.info(f"Найден imtId: {product_data.imtId}")

            # Цена: в новом лэйауте финальная цена лежит в единственном <ins>,
            # старых классов .price-block__final-price больше нет.
            price_elem = await page.query_selector('ins')
            if price_elem:
                price_text = (await price_elem.text_content()).strip()
                price_match = re.search(r'([\d\s]+)', price_text.replace('\xa0', ''))
                if price_match:
                    product_data.price = float(price_match.group(1).replace(' ', ''))
                    product_data.currency = "RUB"
                    product_data.price_checked_at = datetime.utcnow()

            # Рейтинг и число оценок: в новом лэйауте они в одном блоке
            # вида "4,9 · 327 оценок"
            rating_block = await page.query_selector('[class*=Rating]')
            if rating_block:
                rating_text = ((await rating_block.text_content()) or '').strip()
                m = re.search(r'(\d[.,]\d)', rating_text)
                if m:
                    product_data.overall_rating = float(m.group(1).replace(',', '.'))
                m = re.search(r'([\d\s\u00a0]+)\s*оцен', rating_text)
                if m:
                    cleaned = re.sub(r'[^\d]', '', m.group(1))
                    if cleaned:
                        product_data.ratings_count = int(cleaned)

            # Характеристики: старых .product-params__row больше нет,
            # в новом лэйауте это обычная таблица из двух колонок.
            characteristics = {}
            char_rows = await page.query_selector_all('table tr')
            for row in char_rows:
                cells = await row.query_selector_all('td')
                if len(cells) == 2:
                    key = ((await cells[0].text_content()) or '').strip()
                    value = ((await cells[1].text_content()) or '').strip()
                    if key and value:
                        characteristics[key] = value

            if characteristics:
                product_data.characteristics = characteristics

            # Описание
            desc_elem = await page.query_selector('.product-page__description-text, .collapsable__text')
            if desc_elem:
                product_data.description = (await desc_elem.text_content()).strip()

            # Фото товара. og:image надёжнее DOM-селекторов: отдаёт big-версию
            # и не зависит от ленивой подгрузки галереи. Хост basket-NN бывает
            # любым (для 1195565901 это basket-44), поэтому ничего не угадываем —
            # берём готовый URL прямо из мета-тега.
            try:
                og_image = await page.evaluate(
                    "() => { const m = document.querySelector('meta[property=\"og:image\"]');"
                    " return m ? m.content : null; }"
                )
                if og_image:
                    product_data.image_url = og_image
                    gallery = await page.evaluate(
                        "() => Array.from(document.querySelectorAll('img'))"
                        ".map(i => i.currentSrc || i.src)"
                        ".filter(u => u && u.indexOf('/images/') !== -1)"
                    )
                    # Дедуп + нормализация к big-размеру: c246x328/tm — превью.
                    urls = []
                    for u in [og_image] + list(gallery or []):
                        big = re.sub(r'/images/(tm|c\d+x\d+)/', '/images/big/', u.split('?')[0])
                        if big not in urls:
                            urls.append(big)
                    product_data.image_urls = urls[:10]
                    logger.info(f"Фото найдено: {len(product_data.image_urls)} (og:image)")
            except Exception as e:
                logger.warning(f"Не удалось получить фото товара: {e}")

            logger.info(f"Карточка собрана: {product_data.name} ({product_data.brand})")

        except Exception as e:
            logger.error(f"Ошибка при сборе карточки: {e}")
            raise

    async def _scrape_reviews(self, page: Page, article: str, product_data: ProductData):
        """Собрать отзывы через публичный API WB.

        DOM-парсинг страницы отзывов больше не работает: WB перешёл на SPA и
        отдаёт разметку через JS, а классы .feedback/.comments__item исчезли.
        Зато API feedbacks1/2.wb.ru отдаёт готовый JSON и не требует антибота —
        проверено: HTTP 200 без cookies, есть text/pros/cons/productValuation.
        """
        if not product_data.imtId:
            logger.warning("imtId не найден — отзывы собрать нельзя")
            product_data.reviews_complete = True
            return

        payload = None
        for host in ("feedbacks1.wb.ru", "feedbacks2.wb.ru"):
            url = f"https://{host}/feedbacks/v2/{product_data.imtId}"
            try:
                logger.info(f"Загружаем отзывы через API: {url}")
                data = await page.evaluate(
                    """async (u) => {
                        const res = await fetch(u, {headers: {'Accept': 'application/json'}});
                        if (!res.ok) return {__status: res.status};
                        return await res.json();
                    }""",
                    url,
                )
                if isinstance(data, dict) and data.get("__status") is None:
                    payload = data
                    break
                logger.warning(f"{host} ответил {data.get('__status') if isinstance(data, dict) else 'ошибка'}")
            except Exception as e:
                logger.warning(f"Не удалось получить отзывы с {host}: {e}")

        if not payload:
            logger.warning("Отзывы не получены ни с одного хоста — оставляем пустыми")
            product_data.reviews_complete = True
            return

        valuation = payload.get("valuation")
        if valuation is not None:
            try:
                product_data.overall_rating = float(str(valuation).replace(",", "."))
            except (TypeError, ValueError):
                pass
        count = payload.get("feedbackCount")
        if isinstance(count, int):
            product_data.ratings_count = count

        seen = set()
        collected = 0
        for fb in (payload.get("feedbacks") or []):
            if collected >= self.max_reviews:
                break
            text = (fb.get("text") or "").strip()
            pros = (fb.get("pros") or "").strip()
            cons = (fb.get("cons") or "").strip()
            if not text and not pros and not cons:
                continue
            key = (text, pros, cons)
            if key in seen:
                continue
            seen.add(key)

            reply = fb.get("answer") or {}
            product_data.reviews.append(
                Review(
                    text=text or None,
                    pros=pros or None,
                    cons=cons or None,
                    rating=fb.get("productValuation"),
                    date=fb.get("createdDate"),
                    color=fb.get("color") or None,
                    size=fb.get("size") or None,
                    variant_article=str(fb.get("nmId")) if fb.get("nmId") else None,
                    is_verified_purchase=bool(fb.get("matchingSize") or fb.get("matchingDescription")),
                    seller_reply=(reply.get("text") if isinstance(reply, dict) else None),
                )
            )
            collected += 1

        product_data.reviews_collected = collected
        product_data.reviews_complete = collected < self.max_reviews
        logger.info(f"Собрано отзывов: {collected} (всего у товара {count})")

    async def _scrape_questions(self, page: Page, article: str, product_data: ProductData):
        """Собрать вопросы и ответы"""
        if not product_data.imtId:
            logger.warning("imtId не найден для сбора вопросов")
            return

        url = f"https://www.wildberries.ru/catalog/{article}/questions?imtId={product_data.imtId}"
        logger.info(f"Загружаем вопросы: {url}")

        try:
            # networkidle на SPA WB не наступает — грузим domcontentloaded и ждём
            # любой из селекторов вопросов подольше.
            await page.goto(url, wait_until='domcontentloaded')
            await page.wait_for_selector('.question, .qa-item', timeout=12000)

            questions_collected = 0
            seen_questions = set()

            while questions_collected < self.max_questions:
                question_elements = await page.query_selector_all('.question, .qa-item')

                for elem in question_elements[questions_collected:]:
                    if questions_collected >= self.max_questions:
                        break

                    try:
                        question_obj = Question(question="")

                        # Текст вопроса
                        q_elem = await elem.query_selector('.question__text, .qa-question')
                        if q_elem:
                            question_obj.question = (await q_elem.text_content()).strip()

                        # Дубликат?
                        if question_obj.question in seen_questions:
                            continue
                        seen_questions.add(question_obj.question)

                        # Ответ
                        a_elem = await elem.query_selector('.question__answer, .qa-answer')
                        if a_elem:
                            question_obj.answer = (await a_elem.text_content()).strip()

                        # Дата
                        date_elem = await elem.query_selector('.question__date, .qa-date')
                        if date_elem:
                            question_obj.date = (await date_elem.text_content()).strip()

                        product_data.questions.append(question_obj)
                        questions_collected += 1

                    except Exception as e:
                        logger.warning(f"Ошибка при парсинге вопроса: {e}")
                        continue

                # Проверяем догрузку
                show_more = await page.query_selector('button:has-text("Показать ещё")')
                if show_more and questions_collected < self.max_questions:
                    await show_more.click()
                    await asyncio.sleep(2)
                else:
                    break

            product_data.questions_collected = questions_collected
            product_data.questions_complete = questions_collected < self.max_questions
            logger.info(f"Собрано вопросов: {questions_collected}")

        except PlaywrightTimeout:
            logger.warning("Таймаут при загрузке вопросов - возможно, их нет")
        except Exception as e:
            logger.error(f"Ошибка при сборе вопросов: {e}")
