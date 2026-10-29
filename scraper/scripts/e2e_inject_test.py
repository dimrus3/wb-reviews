"""
E2E-тест пути LLM+БД без скрапинга Wildberries.

Зачем: с текущего IP WB отдаёт анти-бот (страница 498, card.wb.ru 403),
поэтому сбор данных невозможен. Этот скрипт проверяет ВТОРУЮ половину
пайплайна (анализ -> генерация поста -> БД -> статус awaiting_approval),
подставляя подготовленный фикстурный ProductData вместо результата скрапера.

Запуск внутри контейнера:
    docker exec -w /app wb_reviews_scraper python /tmp/e2e_inject_test.py
"""
import asyncio
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, "/app")

from app.config import get_settings  # noqa: E402
from app.database import Database  # noqa: E402
from app.analyzer import Analyzer  # noqa: E402
from app.models import ProductData, Review, Question, ProductStatus  # noqa: E402

DB_PATH = "/app/data/wb_reviews.db"
PROMPTS_DIR = "/app/prompts"
TEST_ARTICLE = "999000111"


def load_prompt(name: str) -> str:
    with open(f"{PROMPTS_DIR}/{name}", encoding="utf-8") as f:
        return f.read()


# Фикстура: структура 1-в-1 как отдаёт scraper.py, значения — тестовые.
FIXTURE = {
    "article": TEST_ARTICLE,
    "name": "Кроссовки женские демисезонные City Walk",
    "brand": "TESTBRAND",
    "variant": "белый / 38",
    "characteristics": {
        "Цвет": "белый",
        "Материал верха": "эко-кожа",
        "Подошва": "резина",
        "Сезон": "весна-осень",
        "Страна производства": "Китай",
        "Пол": "женский",
    },
    "description": "Лёгкие демисезонные кроссовки на резиновой подошве. "
                   "Съёмная стелька, шнуровка, мягкий кант по заднику.",
    "price": 2790.0,
    "price_conditions": "цена с учётом скидки продавца, без промокода",
    "currency": "RUB",
    "price_checked_at": datetime.utcnow().isoformat(),
    "overall_rating": 4.4,
    "ratings_count": 1843,
    "reviews_collected": 14,
    "reviews_complete": True,
    "questions_collected": 6,
    "questions_complete": True,
    "imtId": "TESTIMT0001",
    "reviews": [
        {"text": "Ношу месяц, всё отлично, не развалились.", "pros": "удобные, лёгкие",
         "cons": "чуть маломерят", "rating": 5, "date": "2026-09-14", "color": "белый", "size": "38",
         "is_verified_purchase": True, "seller_reply": None},
        {"text": "Взяла 38, обычно ношу 37, сели идеально.", "pros": "соответствие размеру при +1",
         "cons": None, "rating": 5, "date": "2026-09-12", "color": "белый", "size": "38",
         "is_verified_purchase": True, "seller_reply": "Спасибо за отзыв!"},
        {"text": "Через две недели начала отклеиваться подошва справа.", "pros": "красивые",
         "cons": "клей слабый, подошва отходит", "rating": 2, "date": "2026-09-10", "color": "белый",
         "size": "39", "is_verified_purchase": True, "seller_reply": "Напишите нам в чат, заменим пару."},
        {"text": "На узкую ногу отлично, на широкую будет жать.", "pros": "лёгкие", "cons": "узкая колодка",
         "rating": 4, "date": "2026-09-08", "color": "белый", "size": "37", "is_verified_purchase": True},
        {"text": "За такие деньги нормально, но стелька тонкая.", "pros": "цена", "cons": "тонкая стелька",
         "rating": 4, "date": "2026-09-05", "color": "белый", "size": "38", "is_verified_purchase": True},
        {"text": "Промокла под дождём, внутри сыро, сохнет долго.", "pros": None, "cons": "не водостойкие",
         "rating": 3, "date": "2026-09-02", "color": "белый", "size": "38", "is_verified_purchase": True},
        {"text": "Ребёнку в школу берём второй раз, довольны.", "pros": "износостойкие", "cons": None,
         "rating": 5, "date": "2026-08-30", "color": "белый", "size": "36", "is_verified_purchase": True},
        {"text": "Пришли с пятном на боку, вернула без проблем.", "pros": None, "cons": "брак, пятно",
         "rating": 3, "date": "2026-08-27", "color": "белый", "size": "38", "is_verified_purchase": True,
         "seller_reply": "Приносим извинения, брак компенсирован."},
        {"text": "Подошва скользит на мокром асфальте.", "pros": "мягкие", "cons": "скользят",
         "rating": 3, "date": "2026-08-25", "color": "белый", "size": "39", "is_verified_purchase": True},
        {"text": "Отличные на каждый день, хожу по 10 км.", "pros": "не натирают", "cons": None,
         "rating": 5, "date": "2026-08-22", "color": "белый", "size": "38", "is_verified_purchase": True},
        {"text": "Швы аккуратные, запаха нет.", "pros": "качество пошива", "cons": None, "rating": 5,
         "date": "2026-08-20", "color": "белый", "size": "37", "is_verified_purchase": True},
        {"text": "Через месяц разошёлся шов у язычка.", "pros": None, "cons": "шов разошёлся",
         "rating": 2, "date": "2026-08-18", "color": "белый", "size": "40", "is_verified_purchase": True,
         "seller_reply": "Готовы принять возврат по гарантии."},
        {"text": "Цвет как на фото, размер в размер.", "pros": "цвет совпадает", "cons": None, "rating": 5,
         "date": "2026-08-15", "color": "белый", "size": "38", "is_verified_purchase": True},
        {"text": "Заказывала две пары, одна пришла с царапиной.", "pros": "быстрая доставка",
         "cons": "царапина на одной паре", "rating": 4, "date": "2026-08-12", "color": "белый", "size": "38",
         "is_verified_purchase": True},
    ],
    "questions": [
        {"question": "Натуральная кожа или эко?", "answer": "Эко-кожа.", "date": "2026-09-01"},
        {"question": "Маломерят?", "answer": "Берите на размер больше.", "date": "2026-08-29"},
        {"question": "Подходят на широкую стопу?", "answer": "Колодка средняя, на широкую лучше +1.", "date": "2026-08-26"},
        {"question": "Есть ли супинатор?", "answer": "Нет, стелька плоская.", "date": "2026-08-23"},
        {"question": "Можно ли стирать?", "answer": "Рекомендуем только ручную чистку.", "date": "2026-08-19"},
        {"question": "На какой сезон?", "answer": "Весна-осень.", "date": "2026-08-14"},
    ],
}


async def main() -> int:
    settings = get_settings()
    print(f"LLM: base={settings.llm_base_url} model={settings.llm_model} "
          f"max_tokens={settings.llm_max_tokens}")

    db = Database(DB_PATH)
    product_id, is_new = db.add_product(TEST_ARTICLE, settings.allowed_user_id)
    print(f"Товар {TEST_ARTICLE}: product_id={product_id}, новый={is_new}")

    product_data = ProductData(**FIXTURE)
    db.save_product_data(product_id, product_data)
    print(f"ProductData сохранён: отзывов={len(product_data.reviews)}, "
          f"вопросов={len(product_data.questions)}")

    analyzer = Analyzer(settings)
    db.update_product_status(product_id, ProductStatus.ANALYZING)

    if not await analyzer.check_daily_limit(db):
        print("ОШИБКА: дневной лимит LLM исчерпан")
        return 1

    t0 = time.time()
    analysis = await analyzer.analyze_product(product_data, load_prompt("analyze.txt"))
    t1 = time.time()
    await analyzer.increment_request_count(db)
    db.save_analysis(product_id, analysis)
    print(f"[1/2] Анализ получен за {t1 - t0:.1f}s")
    print("  характеристики:", analysis.product_characteristics[:120])
    print("  опыт покупателей:", analysis.customer_experience[:120])
    print("  плюсы:", analysis.pros_summary)
    print("  минусы:", analysis.cons_summary)
    print("  инсайты:", analysis.questions_insights)

    if not await analyzer.check_daily_limit(db):
        print("ОШИБКА: дневной лимит LLM исчерпан")
        return 1

    t2 = time.time()
    post_text = await analyzer.generate_post(product_data, analysis, load_prompt("generate_post.txt"))
    t3 = time.time()
    await analyzer.increment_request_count(db)
    print(f"[2/2] Пост сгенерирован за {t3 - t2:.1f}s ({len(post_text)} символов)")

    post_id = db.save_post(product_id, post_text, version=1)
    db.update_product_status(product_id, ProductStatus.AWAITING_APPROVAL)
    print(f"post_id={post_id}, статус=awaiting_approval")

    print("\n===== СГЕНЕРИРОВАННЫЙ ПОСТ =====")
    print(post_text)
    print("===== КОНЕЦ ПОСТА =====\n")

    awaiting = db.get_awaiting_approval()
    print(f"Товаров на одобрении: {len(awaiting)}")
    print(json.dumps(db.get_queue_status(), ensure_ascii=False, default=str))
    print(f"ИТОГО LLM-время: {t3 - t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
