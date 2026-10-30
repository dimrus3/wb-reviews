"""
Анализатор данных и генератор постов через LLM
"""
import json
import logging
from typing import Optional, Dict, Any
import httpx
from .models import ProductData, Analysis
from .config import Settings

logger = logging.getLogger(__name__)


class EmptyLLMResponse(Exception):
    """LLM ответил 200 OK, но без текста — повторяем запрос."""


class Analyzer:
    """Анализ данных и генерация постов"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=settings.llm_timeout)

    async def check_daily_limit(self, db) -> bool:
        """Проверить дневной лимит запросов"""
        # Раньше читался system_state["llm_requests_<дата>"], а счётчик пишется
        # в таблицу llm_requests — проверка всегда видела 0 и лимит не срабатывал.
        return db.get_llm_requests_today() < self.settings.llm_daily_limit

    async def increment_request_count(self, db):
        """Увеличить счётчик запросов"""
        return db.increment_llm_requests()

    def _today_str(self) -> str:
        """Сегодняшняя дата ДД.ММ.ГГГГ в часовом поясе публикаций."""
        from datetime import datetime
        import pytz
        tz = pytz.timezone(getattr(self.settings, "publication_timezone", "Asia/Novosibirsk"))
        return datetime.now(tz).strftime("%d.%m.%Y")

    async def _call_llm(self, system_prompt: str, user_prompt: str,
                       max_retries: int = 2) -> Optional[str]:
        """Вызов LLM: основная модель, при сбое — запасная (LLM_FALLBACK_MODEL).

        Запасная включается, только если основная не ответила за все попытки
        (HTTP-ошибка, таймаут, пустой ответ). Без LLM_FALLBACK_MODEL поведение
        прежнее: ошибка основной модели уходит наверх.
        """
        fallback = (getattr(self.settings, "llm_fallback_model", "") or "").strip()
        try:
            return await self._call_model(self.settings.llm_model, system_prompt,
                                          user_prompt, max_retries)
        except Exception as e:
            if not fallback or fallback == self.settings.llm_model:
                raise
            logger.warning(f"Основная модель {self.settings.llm_model} не ответила ({e}) — "
                           f"пробуем запасную {fallback}")
            return await self._call_model(fallback, system_prompt, user_prompt, max_retries)

    async def _call_model(self, model: str, system_prompt: str, user_prompt: str,
                          max_retries: int = 2) -> Optional[str]:
        """
        Вызов LLM API конкретной моделью
        """
        for attempt in range(max_retries):
            try:
                response = await self.client.post(
                    f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.settings.llm_api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "max_tokens": self.settings.llm_max_tokens,
                        "temperature": 0.7
                    }
                )

                response.raise_for_status()
                data = response.json()

                choice = (data.get("choices") or [{}])[0]
                content = (choice.get("message") or {}).get("content")
                if not content or not content.strip():
                    # OpenRouter иногда отдаёт 200 OK с content=null (например,
                    # модель израсходовала max_tokens на рассуждения). Раньше это
                    # падало дальше как «'NoneType' object has no attribute 'strip'».
                    reason = choice.get("finish_reason")
                    raise EmptyLLMResponse(f"LLM вернул пустой ответ (finish_reason={reason})")
                return content

            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP ошибка при вызове LLM (попытка {attempt + 1}): {e.response.status_code}")
                if attempt < max_retries - 1:
                    continue
                raise Exception(f"LLM API вернул ошибку: {e.response.status_code}")

            except Exception as e:
                logger.error(f"Ошибка при вызове LLM (попытка {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    continue
                raise

    async def analyze_product(self, product_data: ProductData,
                             analyze_prompt: str) -> Analysis:
        """
        Первый шаг: структурированный анализ
        """
        logger.info(f"Анализируем товар {product_data.article}")

        # Подготовка данных для анализа
        data_summary = {
            "название": product_data.name,
            "бренд": product_data.brand,
            "артикул": product_data.article,
            "цена": product_data.price,
            "рейтинг": product_data.overall_rating,
            "количество_оценок": product_data.ratings_count,
            "характеристики": product_data.characteristics,
            "описание": product_data.description,
            "количество_отзывов": product_data.reviews_collected,
            "количество_вопросов": product_data.questions_collected
        }

        # Подготовка отзывов
        reviews_text = []
        for idx, review in enumerate(product_data.reviews):
            review_parts = [f"Отзыв #{idx + 1}:"]
            if review.rating:
                review_parts.append(f"Оценка: {review.rating}/5")
            if review.text:
                review_parts.append(f"Текст: {review.text}")
            if review.pros:
                review_parts.append(f"Достоинства: {review.pros}")
            if review.cons:
                review_parts.append(f"Недостатки: {review.cons}")
            if review.color:
                review_parts.append(f"Цвет: {review.color}")
            if review.size:
                review_parts.append(f"Размер: {review.size}")
            if review.seller_reply:
                review_parts.append(f"Ответ продавца: {review.seller_reply}")
            reviews_text.append("\n".join(review_parts))

        # Подготовка вопросов
        questions_text = []
        for idx, q in enumerate(product_data.questions):
            q_parts = [f"Вопрос #{idx + 1}: {q.question}"]
            if q.answer:
                q_parts.append(f"Ответ: {q.answer}")
            questions_text.append("\n".join(q_parts))

        user_content = f"""
ДАННЫЕ КАРТОЧКИ:
{json.dumps(data_summary, ensure_ascii=False, indent=2)}

ОТЗЫВЫ ({len(product_data.reviews)} из {product_data.reviews_collected} собранных):
{chr(10).join(reviews_text[:50])}

ВОПРОСЫ И ОТВЕТЫ ({len(product_data.questions)} собранных):
{chr(10).join(questions_text[:30])}

Проведи анализ и верни JSON строго в следующем формате:
{{
  "product_characteristics": "краткое описание того, что заявлено в карточке",
  "customer_experience": "обобщение опыта покупателей из отзывов",
  "seller_claims": "что утверждает продавец в ответах",
  "pros_summary": ["достоинство 1", "достоинство 2", "достоинство 3"],
  "cons_summary": ["замечание 1", "замечание 2", "замечание 3"],
  "questions_insights": ["полезное уточнение 1", "полезное уточнение 2"]
}}
"""

        response = await self._call_llm(analyze_prompt, user_content)

        # Парсинг JSON
        try:
            # Извлекаем JSON из ответа (может быть обёрнут в ```json```)
            json_match = response
            if "```json" in response:
                json_match = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                json_match = response.split("```")[1].split("```")[0].strip()

            analysis_data = json.loads(json_match)

            return Analysis(
                product_characteristics=analysis_data.get("product_characteristics", ""),
                customer_experience=analysis_data.get("customer_experience", ""),
                seller_claims=analysis_data.get("seller_claims", ""),
                pros_summary=analysis_data.get("pros_summary", []),
                cons_summary=analysis_data.get("cons_summary", []),
                questions_insights=analysis_data.get("questions_insights", []),
                evidence_ids={}
            )

        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON от LLM: {e}\nОтвет: {response}")
            raise Exception(f"LLM вернул некорректный JSON: {e}")

    async def generate_post(self, product_data: ProductData, analysis: Analysis,
                           post_prompt: str, revision_note: Optional[str] = None) -> str:
        """
        Второй шаг: генерация Telegram-поста
        """
        logger.info(f"Генерируем пост для товара {product_data.article}")

        context = {
            "название": product_data.name,
            "бренд": product_data.brand,
            "артикул": product_data.article,
            "цена": product_data.price,
            "рейтинг": product_data.overall_rating,
            "количество_оценок": product_data.ratings_count,
            # Без реальной даты LLM подставлял в «(проверена ДД.ММ.ГГГГ)» выдуманную
            # («проверена 2024», «15.01.2025»). Цену собрали только что — сегодня.
            "дата_проверки_цены": self._today_str(),
            "отзывов_проанализировано": product_data.reviews_collected,
            "вопросов_проанализировано": product_data.questions_collected
        }

        user_content = f"""
КОНТЕКСТ ТОВАРА:
{json.dumps(context, ensure_ascii=False, indent=2)}

РЕЗУЛЬТАТЫ АНАЛИЗА:
Характеристики карточки: {analysis.product_characteristics}
Опыт покупателей: {analysis.customer_experience}
Утверждения продавца: {analysis.seller_claims}

Достоинства:
{chr(10).join('- ' + p for p in analysis.pros_summary)}

Замечания:
{chr(10).join('- ' + c for c in analysis.cons_summary)}

Полезные уточнения из вопросов:
{chr(10).join('- ' + q for q in analysis.questions_insights)}
"""

        if revision_note:
            user_content += f"\n\nЗАМЕЧАНИЕ К ПЕРЕДЕЛКЕ: {revision_note}"

        response = await self._call_llm(post_prompt, user_content)

        # Постобработка
        post_text = response.strip()

        # Проверка на наличие запрещённых ссылок
        if self._contains_wb_links(post_text):
            logger.warning("В сгенерированном посте обнаружены ссылки на WB - удаляем")
            post_text = self._remove_wb_links(post_text)

        # Проверка длины
        if len(post_text) > 4096:  # Лимит Telegram
            logger.warning(f"Пост слишком длинный ({len(post_text)} символов) - обрезаем")
            post_text = post_text[:4000] + "...\n\n🛍 Артикул WB: " + product_data.article

        # Убедимся, что артикул есть в конце
        if product_data.article not in post_text:
            post_text += f"\n\n🛍 Артикул WB: {product_data.article}"

        # Приводим markdown к Telegram-HTML. n8n отправляет пост с parse_mode=HTML,
        # потому что legacy Markdown не понимает **жирный** и печатает звёздочки
        # как есть, а MarkdownV2 требует экранировать почти всю пунктуацию.
        post_text = self._to_telegram_html(post_text)

        return post_text

    def _to_telegram_html(self, text: str) -> str:
        """Сконвертировать простой markdown из LLM в HTML, понятный Telegram.

        Поддерживаем только то, что реально генерирует промпт: **жирный**,
        `код`, заголовок и списки. Всё остальное экранируем, иначе Telegram
        вернёт «can't parse entities» на символе & или <.
        """
        import re

        # Сначала экранируем HTML-спецсимволы в исходном тексте.
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        # **жирный** -> <b>жирный</b>
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
        # одиночные *курсив* оставляем как есть — в постах почти не встречаются,
        # но если встретятся, лучше не ломаться
        text = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
        # `код` -> <code>
        text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
        # Заголовок-строку без markdown делаем жирной, если это первая строка
        lines = text.split("\n")
        if lines and lines[0] and not lines[0].startswith("<"):
            lines[0] = f"<b>{lines[0]}</b>"
        # Разделители из дефисов -> визуальная линия, дефисы в списках -> буллиты
        result = []
        for line in lines:
            stripped = line.strip()
            if set(stripped) == {"-"} and len(stripped) >= 3:
                result.append("━━━━━━━━━━━━━━━")
            elif stripped.startswith("- "):
                result.append("• " + stripped[2:])
            else:
                result.append(line)
        return "\n".join(result)

    def _contains_wb_links(self, text: str) -> bool:
        """Проверить наличие ссылок на Wildberries"""
        wb_patterns = [
            r'wildberries\.ru',
            r'wb\.ru',
            r'https?://.*wildberries',
        ]
        import re
        for pattern in wb_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _remove_wb_links(self, text: str) -> str:
        """Удалить ссылки на Wildberries"""
        import re
        # Удаляем markdown-ссылки
        text = re.sub(r'\[([^\]]+)\]\(https?://[^\)]*wildberries[^\)]*\)', r'\1', text, flags=re.IGNORECASE)
        # Удаляем обычные ссылки
        text = re.sub(r'https?://[^\s]*wildberries[^\s]*', '', text, flags=re.IGNORECASE)
        return text
