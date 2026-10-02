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
                    browser = await p.chromium.launch(headless=self.headless)
                    context = await browser.new_context(
                        viewport={'width': 1920, 'height': 1080},
                        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
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
            response = await page.goto(url, wait_until='networkidle')

            # Проверка капчи
            if await page.locator('text=проверка').count() > 0:
                raise Exception("Обнаружена капча - требуется ручная проверка")

            # Проверка доступности
            if response.status == 404:
                raise Exception("Товар не найден (404)")

            # Ждём загрузки основного контента
            await page.wait_for_selector('.product-page__title, .product-page__header', timeout=10000)

            # Название
            title_elem = await page.query_selector('.product-page__title')
            if title_elem:
                product_data.name = (await title_elem.text_content()).strip()

            # Бренд
            brand_elem = await page.query_selector('.product-page__header a')
            if brand_elem:
                product_data.brand = (await brand_elem.text_content()).strip()

            # Извлекаем imtId из данных страницы
            # imtId обычно содержится в JSON внутри скриптов или data-атрибутах
            content = await page.content()
            imt_match = re.search(r'"imtId"[:\s]+(\d+)', content)
            if imt_match:
                product_data.imtId = imt_match.group(1)
                logger.info(f"Найден imtId: {product_data.imtId}")

            # Цена
            price_elem = await page.query_selector('.price-block__final-price, ins.price-block__final-price')
            if price_elem:
                price_text = (await price_elem.text_content()).strip()
                price_match = re.search(r'([\d\s]+)', price_text.replace('\xa0', ''))
                if price_match:
                    product_data.price = float(price_match.group(1).replace(' ', ''))
                    product_data.currency = "RUB"
                    product_data.price_checked_at = datetime.utcnow()

            # Рейтинг
            rating_elem = await page.query_selector('.product-review__rating')
            if rating_elem:
                rating_text = await rating_elem.get_attribute('aria-label')
                if rating_text:
                    rating_match = re.search(r'([\d.]+)', rating_text)
                    if rating_match:
                        product_data.overall_rating = float(rating_match.group(1))

            # Количество оценок
            reviews_count_elem = await page.query_selector('.product-review__count-review')
            if reviews_count_elem:
                count_text = (await reviews_count_elem.text_content()).strip()
                count_match = re.search(r'(\d+)', count_text)
                if count_match:
                    product_data.ratings_count = int(count_match.group(1))

            # Характеристики
            characteristics = {}
            char_rows = await page.query_selector_all('.product-params__row')
            for row in char_rows:
                name_elem = await row.query_selector('.product-params__cell:first-child')
                value_elem = await row.query_selector('.product-params__cell:last-child')
                if name_elem and value_elem:
                    name = (await name_elem.text_content()).strip()
                    value = (await value_elem.text_content()).strip()
                    characteristics[name] = value

            if characteristics:
                product_data.characteristics = characteristics

            # Описание
            desc_elem = await page.query_selector('.product-page__description-text, .collapsable__text')
            if desc_elem:
                product_data.description = (await desc_elem.text_content()).strip()

            logger.info(f"Карточка собрана: {product_data.name} ({product_data.brand})")

        except Exception as e:
            logger.error(f"Ошибка при сборе карточки: {e}")
            raise

    async def _scrape_reviews(self, page: Page, article: str, product_data: ProductData):
        """Собрать отзывы"""
        if not product_data.imtId:
            logger.warning("imtId не найден, пытаемся собрать отзывы через прямую ссылку")
            # Попробуем перейти по ссылке отзывов на странице
            reviews_link = await page.query_selector('a[href*="/feedbacks"]')
            if reviews_link:
                await reviews_link.click()
                await page.wait_for_load_state('networkidle')
            else:
                logger.warning("Не удалось найти ссылку на отзывы")
                return
        else:
            url = f"https://www.wildberries.ru/catalog/{article}/feedbacks?imtId={product_data.imtId}"
            logger.info(f"Загружаем отзывы: {url}")
            await page.goto(url, wait_until='networkidle')

        try:
            # Ждём загрузки отзывов
            await page.wait_for_selector('.feedback, .comments__item', timeout=10000)

            reviews_collected = 0
            seen_texts = set()  # Для отсеивания дублей

            while reviews_collected < self.max_reviews:
                # Собираем видимые отзывы
                review_elements = await page.query_selector_all('.feedback, .comments__item')

                for elem in review_elements[reviews_collected:]:
                    if reviews_collected >= self.max_reviews:
                        break

                    try:
                        review = Review()

                        # Раскрыть "Читать полностью" если есть
                        read_more = await elem.query_selector('button:has-text("полностью"), .btn-show-more')
                        if read_more:
                            await read_more.click()
                            await asyncio.sleep(0.5)

                        # Текст отзыва
                        text_elem = await elem.query_selector('.feedback__text, .comment__text')
                        if text_elem:
                            review.text = (await text_elem.text_content()).strip()

                        # Дубликат?
                        if review.text and review.text in seen_texts:
                            continue
                        if review.text:
                            seen_texts.add(review.text)

                        # Достоинства
                        pros_elem = await elem.query_selector('.feedback-text__pros, text="Достоинства:"')
                        if pros_elem:
                            pros_parent = await pros_elem.evaluate_handle('el => el.parentElement')
                            review.pros = (await pros_parent.text_content()).replace('Достоинства:', '').strip()

                        # Недостатки
                        cons_elem = await elem.query_selector('.feedback-text__cons, text="Недостатки:"')
                        if cons_elem:
                            cons_parent = await cons_elem.evaluate_handle('el => el.parentElement')
                            review.cons = (await cons_parent.text_content()).replace('Недостатки:', '').strip()

                        # Оценка
                        rating_elem = await elem.query_selector('.star-line, [class*="rating"]')
                        if rating_elem:
                            rating_class = await rating_elem.get_attribute('class')
                            rating_match = re.search(r'star-line--(\d)', rating_class or '')
                            if rating_match:
                                review.rating = int(rating_match.group(1))

                        # Дата
                        date_elem = await elem.query_selector('.feedback__date, .comment__date')
                        if date_elem:
                            review.date = (await date_elem.text_content()).strip()

                        # Характеристики варианта (цвет, размер)
                        params = await elem.query_selector_all('.feedback__param')
                        for param in params:
                            text = (await param.text_content()).strip().lower()
                            if 'цвет' in text or 'color' in text:
                                review.color = text.split(':')[-1].strip()
                            elif 'размер' in text or 'size' in text:
                                review.size = text.split(':')[-1].strip()

                        # Статус покупки
                        verified = await elem.query_selector('.feedback__verified, text="Verified Purchase"')
                        review.is_verified_purchase = verified is not None

                        # Ответ продавца
                        seller_reply_elem = await elem.query_selector('.feedback__reply, .seller-answer')
                        if seller_reply_elem:
                            review.seller_reply = (await seller_reply_elem.text_content()).strip()

                        product_data.reviews.append(review)
                        reviews_collected += 1

                    except Exception as e:
                        logger.warning(f"Ошибка при парсинге отзыва: {e}")
                        continue

                # Проверяем, есть ли кнопка "Показать ещё"
                show_more = await page.query_selector('button:has-text("Показать ещё"), .show-more')
                if show_more and reviews_collected < self.max_reviews:
                    await show_more.click()
                    await asyncio.sleep(2)  # Даём время на загрузку
                else:
                    break

            product_data.reviews_collected = reviews_collected
            product_data.reviews_complete = reviews_collected < self.max_reviews
            logger.info(f"Собрано отзывов: {reviews_collected}")

        except PlaywrightTimeout:
            logger.warning("Таймаут при загрузке отзывов - возможно, их нет")
        except Exception as e:
            logger.error(f"Ошибка при сборе отзывов: {e}")

    async def _scrape_questions(self, page: Page, article: str, product_data: ProductData):
        """Собрать вопросы и ответы"""
        if not product_data.imtId:
            logger.warning("imtId не найден для сбора вопросов")
            return

        url = f"https://www.wildberries.ru/catalog/{article}/questions?imtId={product_data.imtId}"
        logger.info(f"Загружаем вопросы: {url}")

        try:
            await page.goto(url, wait_until='networkidle')
            await page.wait_for_selector('.question, .qa-item', timeout=10000)

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
