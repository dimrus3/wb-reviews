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


class Analyzer:
    """Анализ данных и генерация постов"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=settings.llm_timeout)

    async def check_daily_limit(self, db) -> bool:
        """Проверить дневной лимит запросов"""
        from datetime import date
        today = date.today().isoformat()
        count = db.get_system_state(f"llm_requests_{today}")
        current = int(count) if count else 0
        return current < self.settings.llm_daily_limit

    async def increment_request_count(self, db):
        """Увеличить счётчик запросов"""
        return db.increment_llm_requests()

    async def _call_llm(self, system_prompt: str, user_prompt: str,
                       max_retries: int = 2) -> Optional[str]:
        """
        Вызов LLM API
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
                        "model": self.settings.llm_model,
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

                return data["choices"][0]["message"]["content"]

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

        return post_text

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
