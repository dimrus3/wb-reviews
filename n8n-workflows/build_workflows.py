"""
Генератор исправленных n8n-воркфлоу для wb-reviews.

Зачем: импортированные из репозитория JSON содержали ошибки, из-за которых
бот не работал:
  * ссылка на credential id "1", а реальный id созданного Telegram-credential другой;
  * inline-кнопки в post-approval заданы как reply_markup (такого поля в узле нет) —
    сообщение уходило без кнопок, callback-handler никогда не срабатывал;
  * три воркфлоу с telegramTrigger конкурируют за один webhook бота;
  * Code-узлы возвращали объект вместо массива items — n8n падал;
  * chatId брался из ответа HTTP-узла ($json.message.chat.id), а не из триггера;
  * revision-handler не передавал product_id и терял revision_note.

Запуск:  python n8n-workflows/build_workflows.py
Результат: n8n-workflows/fixed/*.json — импортировать через n8n import:workflow.
"""
import json
import os
import uuid

CRED_ID = os.environ.get("N8N_TELEGRAM_CRED_ID", "9hvA7Tk3lvUpnZ9y")
CRED_NAME = "Telegram Bot"
SCRAPER = "http://scraper:8000"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixed")

TG_CRED = {"telegramApi": {"id": CRED_ID, "name": CRED_NAME}}


def node(name, ntype, params, pos, creds=False, extra=None):
    n = {
        "parameters": params,
        "name": name,
        "type": ntype,
        "typeVersion": {
            "n8n-nodes-base.telegramTrigger": 1.2,
            "n8n-nodes-base.telegram": 1.2,
            "n8n-nodes-base.httpRequest": 3,
            # If v1 не понимает operation "equals" — n8n 2.x бросает
            # "compareOperationFunctions[compareData.operation] is not a function".
            # Берём v2.2 и формат фильтра v2.
            "n8n-nodes-base.if": 2.2,
            "n8n-nodes-base.code": 2,
            "n8n-nodes-base.scheduleTrigger": 1,
            "n8n-nodes-base.splitOut": 1,
        }[ntype],
        "position": pos,
        # Стабильный id узла: при пересборке JSON id не меняется, поэтому
        # webhook Telegram остаётся на том же URL, а не пересоздаётся.
        "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "wb-reviews/" + name)),
    }
    if creds:
        n["credentials"] = TG_CRED
    if extra:
        n.update(extra)
    return n


def _filter(conds):
    """Формат фильтра If v2.2 (то, что реально ждёт n8n 2.x)."""
    return {
        "conditions": {
            "options": {
                "caseSensitive": True,
                "leftValue": "",
                "typeValidation": "strict",
                "version": 2,
            },
            "conditions": conds,
            "combinator": "and",
        }
    }


def cond_str(value1, operation, value2):
    return _filter([{
        "id": str(uuid.uuid4()),
        "leftValue": value1,
        "rightValue": value2,
        "operator": {"type": "string", "operation": operation},
    }])


def cond_num_gt(value1, value2):
    return _filter([{
        "id": str(uuid.uuid4()),
        "leftValue": value1,
        "rightValue": value2,
        "operator": {"type": "number", "operation": "gt"},
    }])


def cond_bool_true(value1):
    return _filter([{
        "id": str(uuid.uuid4()),
        "leftValue": value1,
        "rightValue": "",
        "operator": {"type": "boolean", "operation": "true", "singleValue": True},
    }])


def cond_bool_false(value1):
    return {
        "id": str(uuid.uuid4()),
        "leftValue": value1,
        "rightValue": "",
        "operator": {"type": "boolean", "operation": "false", "singleValue": True},
    }


def cond_all(*conds):
    return _filter(list(conds))


def if_node(name, value1, operation, value2, pos):
    return node(name, "n8n-nodes-base.if", cond_str(value1, operation, value2), pos)


def link(conns, src, dst, out=0, dst_in=0):
    conns.setdefault(src, {"main": []})
    main = conns[src]["main"]
    while len(main) <= out:
        main.append([])
    main[out].append({"node": dst, "type": "main", "index": dst_in})


def http(name, method, path, pos, body=None, query=None):
    p = {"url": SCRAPER + path, "method": method, "options": {}}
    if body is not None:
        p["sendBody"] = True
        p["specifyBody"] = "json"
        p["jsonBody"] = body
        p["options"] = {"response": {"response": {"neverError": False}}}
    if query:
        p["sendQuery"] = True
        p["queryParameters"] = {"parameters": [
            {"name": k, "value": v} for k, v in query.items()]}
    return node(name, "n8n-nodes-base.httpRequest", p, pos)


def tg(name, pos, chat, text, markdown=True, keyboard=None):
    p = {"chatId": chat, "text": text}
    af = {}
    if markdown:
        af["parse_mode"] = "Markdown"
    p["additionalFields"] = af
    if keyboard:
        p["replyMarkup"] = "inlineKeyboard"
        p["inlineKeyboard"] = {"rows": [
            {"row": {"buttons": [
                {"text": t, "additionalFields": {"callback_data": cb}}
            ]}} for t, cb in keyboard]}
    return node(name, "n8n-nodes-base.telegram", p, pos, creds=True)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Единый бот: команды + кнопки + замечания. Один telegramTrigger на бота.
# ─────────────────────────────────────────────────────────────────────────────
NORMALIZE_JS = r"""
// Приводим апдейт Telegram к плоскому item с полем action —
// дальше маршрутизация цепочкой IF (Switch v1 в n8n ненадёжен по параметрам).
const out = [];
for (const item of $input.all()) {
  const u = item.json || {};
  const cb = u.callback_query;
  const msg = u.message || u.edited_message;

  if (cb) {
    const data = cb.data || '';
    let action = 'unknown';
    let productId = null;
    if (data.startsWith('approve_')) { action = 'approve'; productId = parseInt(data.slice(8), 10); }
    else if (data.startsWith('skip_')) { action = 'skip'; productId = parseInt(data.slice(5), 10); }
    else if (data.startsWith('revise_')) { action = 'revise_ask'; productId = parseInt(data.slice(7), 10); }
    out.push({ json: {
      kind: 'callback', action, product_id: productId,
      chat_id: cb.message && cb.message.chat ? cb.message.chat.id : null,
      message_id: cb.message ? cb.message.message_id : null,
      user_id: cb.from ? cb.from.id : null,
      text: '', reply_text: '', callback_id: cb.id || null
    }});
    continue;
  }

  if (msg) {
    const text = (msg.text || '').trim();
    const replyText = msg.reply_to_message ? (msg.reply_to_message.text || '') : '';
    let action = 'unknown';
    if (replyText.indexOf('Замечание для переделки поста #') !== -1) action = 'revise_apply';
    else if (/^\/add( |$)/.test(text)) action = 'add';
    else if (text === '/queue' || text === '/queue@' + '') action = 'queue';
    else if (text.startsWith('/queue')) action = 'queue';
    else if (text.startsWith('/status')) action = 'status';
    else if (text.startsWith('/pause')) action = 'pause';
    else if (text.startsWith('/resume')) action = 'resume';

    // /add@MyBot 123 — отрезаем суффикс команды с именем бота
    const addArg = text.replace(/^\/add(@\S+)?\s*/, '').trim();
    const m = replyText.match(/#(\d+)/);

    out.push({ json: {
      kind: 'message', action, text, add_arg: addArg,
      chat_id: msg.chat ? msg.chat.id : null,
      user_id: msg.from ? msg.from.id : null,
      message_id: msg.message_id,
      reply_text: replyText,
      reply_product_id: m ? parseInt(m[1], 10) : null
    }});
  }
}
return out;
"""

def build_bot():
    N = []
    C = {}

    N.append(node("Telegram Update", "n8n-nodes-base.telegramTrigger",
                  {"updates": ["message", "callback_query"], "additionalFields": {}},
                  [220, 420], creds=True))
    N.append(node("Normalize", "n8n-nodes-base.code", {"jsCode": NORMALIZE_JS}, [440, 420]))

    # цепочка IF: false-ветвь каждого идёт в следующий
    chain = [
        ("Action: Approve", "approve", 100),
        ("Action: Skip", "skip", 240),
        ("Action: Revise ask", "revise_ask", 380),
        ("Action: Revise apply", "revise_apply", 520),
        ("Action: Add", "add", 660),
        ("Action: Queue", "queue", 800),
        ("Action: Status", "status", 940),
        ("Action: Pause", "pause", 1080),
        ("Action: Resume", "resume", 1220),
    ]
    for name, act, y in chain:
        N.append(if_node(name, "={{ $json.action }}", "equals", act, [660, y]))

    X = 900
    N.append(http("API Approve", "POST", "/posts/approve", [X, 100],
                  body="={{ JSON.stringify({ product_id: $json.product_id }) }}"))
    N.append(http("API Skip", "POST", "/posts/skip", [X, 240],
                  query={"product_id": "={{ $json.product_id }}"}))
    N.append(http("API Add", "POST", "/products/add", [X, 660],
                  body="={{ JSON.stringify({ input: $json.add_arg, user_id: $json.user_id }) }}"))
    N.append(http("API Queue", "GET", "/queue/status", [X, 800]))
    N.append(http("API Status", "GET", "/system/status", [X, 940]))
    N.append(http("API Pause", "POST", "/publications/pause", [X, 1080],
                  body="={{ JSON.stringify({ user_id: $json.user_id }) }}"))
    N.append(http("API Resume", "POST", "/publications/resume", [X, 1220],
                  body="={{ JSON.stringify({ user_id: $json.user_id }) }}"))
    N.append(http("API Revise", "POST", "/posts/revise", [X, 520],
                  body="={{ JSON.stringify({ product_id: $json.reply_product_id, revision_note: $json.text }) }}"))

    CHAT = "={{ $('Normalize').item.json.chat_id }}"
    N.append(tg("Reply Approved", [1120, 100], CHAT,
                "✅ Пост одобрен и уйдёт в канал в ближайший слот публикации."))
    N.append(tg("Reply Skipped", [1120, 240], CHAT, "⏭ Товар пропущен."))
    N.append(tg("Ask Revision Note", [1120, 380], CHAT,
                "✏️ Замечание для переделки поста #{{ $json.product_id }}\n\n"
                "Ответьте (Reply) на это сообщение и напишите, что поправить в посте."))
    N.append(tg("Reply Revised", [1120, 520], CHAT,
                "✏️ Пост отправлен на переделку. Новая версия придёт на одобрение "
                "в течение пары минут."))
    N.append(tg("Reply Add", [1120, 660], CHAT,
                "={{ $json.success ? '✅ ' + $json.message : '❌ ' + $json.message }}"))
    N.append(tg("Reply Queue", [1120, 800], CHAT,
                "📊 *Статус очереди*\n\n"
                "📥 В очереди: {{ $json.queued }}\n"
                "🔄 Собирается: {{ $json.collecting }}\n"
                "🧠 Анализируется: {{ $json.analyzing }}\n"
                "⏳ Ожидает одобрения: {{ $json.awaiting_approval }}\n"
                "✅ Одобрено: {{ $json.approved }}\n"
                "❌ Ошибки: {{ $json.failed }}\n\n"
                "📦 Всего: {{ $json.total }}"))
    N.append(tg("Reply Status", [1120, 940], CHAT,
                "⚙️ *Статус системы*\n\n"
                "{{ $json.publications_paused ? '⏸ Публикации приостановлены' : '▶️ Публикации активны' }}\n\n"
                "📅 Опубликовано сегодня: {{ $json.today_published }}\n\n"
                "⏰ Следующие слоты:\n{{ $json.next_publication_slots.join('\\n') }}\n\n"
                "📊 Очередь: ожидают одобрения {{ $json.queue.awaiting_approval }}, "
                "одобрено {{ $json.queue.approved }}"))
    N.append(tg("Reply Paused", [1120, 1080], CHAT, "⏸ Публикации приостановлены."))
    N.append(tg("Reply Resumed", [1120, 1220], CHAT, "▶️ Публикации возобновлены."))

    link(C, "Telegram Update", "Normalize")
    link(C, "Normalize", chain[0][0])
    for i, (name, _, _) in enumerate(chain):
        if i + 1 < len(chain):
            link(C, name, chain[i + 1][0], out=1)   # false -> следующий IF

    link(C, "Action: Approve", "API Approve");        link(C, "API Approve", "Reply Approved")
    link(C, "Action: Skip", "API Skip");              link(C, "API Skip", "Reply Skipped")
    link(C, "Action: Revise ask", "Ask Revision Note")
    link(C, "Action: Revise apply", "API Revise");    link(C, "API Revise", "Reply Revised")
    link(C, "Action: Add", "API Add");                link(C, "API Add", "Reply Add")
    link(C, "Action: Queue", "API Queue");            link(C, "API Queue", "Reply Queue")
    link(C, "Action: Status", "API Status");          link(C, "API Status", "Reply Status")
    link(C, "Action: Pause", "API Pause");            link(C, "API Pause", "Reply Paused")
    link(C, "Action: Resume", "API Resume");          link(C, "API Resume", "Reply Resumed")

    return {"name": "WB Reviews - Bot", "nodes": N, "connections": C,
            "settings": {"executionOrder": "v1"}, "active": False}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Post Approval — без повторов одного и того же поста
# ─────────────────────────────────────────────────────────────────────────────
def build_post_approval():
    N, C = [], {}
    N.append(node("Every 5 Minutes", "n8n-nodes-base.scheduleTrigger",
                  {"rule": {"interval": [{"field": "cronExpression", "expression": "*/5 * * * *"}]}},
                  [220, 320]))
    # unnotified=true — иначе один и тот же пост приходит каждые 5 минут
    N.append(http("Get Awaiting", "GET", "/posts/awaiting-approval?unnotified=true", [440, 320]))
    N.append(node("Has Posts?", "n8n-nodes-base.if",
                  cond_num_gt("={{ $json.posts.length }}", 0), [660, 320]))
    N.append(node("Split Posts", "n8n-nodes-base.splitOut", {"fieldToSplitOut": "posts"}, [880, 320]))
    N.append(tg("Send To Owner", [1100, 320], "={{ $env.ALLOWED_USER_ID }}",
                "📝 *Новый пост на одобрение*\n\n"
                "*Артикул WB:* {{ $json.article }}\n"
                "*Версия:* {{ $json.version }}\n\n"
                "─────────────────\n\n{{ $json.text }}\n\n─────────────────",
                keyboard=[
                    ("✅ Одобрить", "approve_{{ $json.product_id }}"),
                    ("✏️ Переделать", "revise_{{ $json.product_id }}"),
                    ("⏭ Пропустить", "skip_{{ $json.product_id }}"),
                ]))
    N.append(http("Mark Notified", "POST", "/posts/notified", [1320, 320],
                  body="={{ JSON.stringify({ post_ids: [$('Split Posts').item.json.post_id] }) }}"))

    link(C, "Every 5 Minutes", "Get Awaiting")
    link(C, "Get Awaiting", "Has Posts?")
    link(C, "Has Posts?", "Split Posts")
    link(C, "Split Posts", "Send To Owner")
    link(C, "Send To Owner", "Mark Notified")
    return {"name": "WB Reviews - Post Approval", "nodes": N, "connections": C,
            "settings": {"executionOrder": "v1"}, "active": False}


# ─────────────────────────────────────────────────────────────────────────────
# 3. Publisher — публикация в канал + обработка ошибки отправки
# ─────────────────────────────────────────────────────────────────────────────
def build_publisher():
    N, C = [], {}
    N.append(node("Schedule 11:00 / 19:00", "n8n-nodes-base.scheduleTrigger",
                  {"rule": {"interval": [{"field": "cronExpression",
                                          "expression": "0 11,19 * * *"}]}},
                  [220, 320], extra={"parameters": {
                      "rule": {"interval": [{"field": "cronExpression",
                                             "expression": "0 11,19 * * *"}]}}}))
    N.append(http("Get Next Post", "POST", "/publish", [440, 320],
                  body="={{ JSON.stringify({ dry_run: false }) }}"))
    # success = true И dry_run = false: при DRY_RUN=true в .env сервис отдаёт
    # только превью поста, и публиковать его в канал нельзя.
    N.append(node("Has Post?", "n8n-nodes-base.if",
                  cond_all(
                      {
                          "id": str(uuid.uuid4()),
                          "leftValue": "={{ $json.success }}",
                          "rightValue": "",
                          "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                      },
                      cond_bool_false("={{ $json.dry_run }}"),
                  ), [660, 320]))
    N.append(tg("Publish To Channel", [880, 220], "={{ $env.TELEGRAM_CHANNEL_ID }}",
                "={{ $json.text }}", keyboard=None))
    N[-1]["onError"] = "continueErrorOutput"

    N.append(http("Confirm Publication", "POST", "/publish/confirm", [1100, 120],
                  query={"post_id": "={{ $('Get Next Post').item.json.post_id }}",
                         "product_id": "={{ $('Get Next Post').item.json.product_id }}",
                         "channel_id": "={{ $env.TELEGRAM_CHANNEL_ID }}",
                         "message_id": "={{ $json.message_id }}"}))
    N.append(http("Mark Uncertain", "POST", "/publish/uncertain", [1100, 320],
                  query={"post_id": "={{ $('Get Next Post').item.json.post_id }}",
                         "product_id": "={{ $('Get Next Post').item.json.product_id }}",
                         "error": "={{ ($json.error && ($json.error.message || $json.error.description)) || 'send failed' }}"}))
    N.append(tg("Notify Owner", [1320, 320], "={{ $env.ALLOWED_USER_ID }}",
                "⚠️ Не удалось опубликовать пост по артикулу "
                "{{ $('Get Next Post').item.json.article }}.\n\n"
                "Проверьте канал вручную: если пост всё же появился, удалите дубликат."))

    link(C, "Schedule 11:00 / 19:00", "Get Next Post")
    link(C, "Get Next Post", "Has Post?")
    link(C, "Has Post?", "Publish To Channel")
    link(C, "Publish To Channel", "Confirm Publication", out=0)
    link(C, "Publish To Channel", "Mark Uncertain", out=1)
    link(C, "Mark Uncertain", "Notify Owner")
    return {"name": "WB Reviews - Publisher", "nodes": N, "connections": C,
            "settings": {"executionOrder": "v1"}, "active": False}


# id воркфлоу фиксированы: import:workflow обновляет их на месте, а не плодит дубли.
WF_IDS = {
    "WB Reviews - Bot": "LPxyHeGVEGPrDB9Y",
    "WB Reviews - Post Approval": "0EAItJQKk5Dy5tWK",
    "WB Reviews - Publisher": "j9OyML8ewmxiYLNU",
}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for wf in (build_bot(), build_post_approval(), build_publisher()):
        wf["id"] = WF_IDS[wf["name"]]
        wf["versionId"] = str(uuid.uuid4())
        path = os.path.join(OUT_DIR, wf["name"].lower().replace(" ", "-").replace("-–-", "-") + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(wf, f, ensure_ascii=False, indent=2)
        print("written:", path, "| nodes:", len(wf["nodes"]))


if __name__ == "__main__":
    main()
