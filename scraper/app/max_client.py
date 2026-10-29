"""
Клиент Bot API мессенджера MAX (max.ru).

Документация: https://dev.max.ru/docs-api

Особенности, на которых легко потерять время:
  * Домен — https://platform-api2.max.ru (platform-api.max.ru отключают).
  * Токен передаётся СЫРОЙ строкой в заголовке Authorization, без "Bearer ".
  * Личный диалог адресуется по user_id, чат/канал — по chat_id.
  * Лимит: не больше 2 сообщений в секунду в один чат.
  * Фото: POST /uploads?type=image -> получить url -> загрузить файл ->
    отправить сообщение с attachments[].payload.token. Сразу после загрузки
    сервер может ответить "attachment.not.ready" — нужен повтор с паузой.

Клиент не бросает исключения наружу при штатных ошибках сети: возвращает
dict с success=False и текстом ошибки, чтобы публикация в Telegram не падала
из-за проблем на стороне MAX.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

MAX_TEXT_LIMIT = 4000


class MaxClient:
    """Отправка текста и фото в чат/канал MAX."""

    def __init__(
        self,
        token: str,
        chat_id: Optional[str] = None,
        base_url: str = "https://platform-api2.max.ru",
        timeout: float = 60.0,
    ):
        self.token = (token or "").strip()
        self.chat_id = str(chat_id).strip() if chat_id else ""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------ utils
    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _headers(self, json_body: bool = True) -> Dict[str, str]:
        headers = {"Authorization": self.token}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _error(resp: httpx.Response) -> str:
        try:
            payload = resp.json()
            if isinstance(payload, dict):
                return f"{payload.get('code', resp.status_code)}: {payload.get('message', '')}".strip()
        except Exception:
            pass
        return f"HTTP {resp.status_code}: {resp.text[:200]}"

    # --------------------------------------------------------------- sending
    async def send_text(self, text: str, fmt: Optional[str] = "html") -> Dict[str, Any]:
        """Отправить текст. fmt: 'html' | 'markdown' | None (plain)."""
        if not self.enabled:
            return {"success": False, "skipped": True, "error": "MAX не настроен (token/chat_id)"}
        body: Dict[str, Any] = {"text": text[:MAX_TEXT_LIMIT], "notify": True}
        if fmt:
            body["format"] = fmt

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/messages",
                    params={"chat_id": self.chat_id},
                    headers=self._headers(),
                    json=body,
                )
                if resp.status_code < 400:
                    return {"success": True, "response": resp.json()}
                # HTML/markdown-разметка MAX местами строже — падаем в plain text
                if fmt:
                    logger.warning(f"MAX отклонил {fmt}-текст ({self._error(resp)}), повтор без разметки")
                    plain = {"text": text[:MAX_TEXT_LIMIT], "notify": True}
                    resp2 = await client.post(
                        f"{self.base_url}/messages",
                        params={"chat_id": self.chat_id},
                        headers=self._headers(),
                        json=plain,
                    )
                    if resp2.status_code < 400:
                        return {"success": True, "plain_fallback": True, "response": resp2.json()}
                    return {"success": False, "error": self._error(resp2)}
                return {"success": False, "error": self._error(resp)}
        except Exception as e:
            logger.error(f"MAX send_text error: {e}")
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    async def _upload_image(self, client: httpx.AsyncClient, image_bytes: bytes) -> Optional[str]:
        """Загрузить JPEG в MAX, вернуть token вложения."""
        resp = await client.post(
            f"{self.base_url}/uploads",
            params={"type": "image"},
            headers={"Authorization": self.token},
        )
        if resp.status_code >= 400:
            logger.warning(f"MAX /uploads отказ: {self._error(resp)}")
            return None
        upload_url = (resp.json() or {}).get("url")
        if not upload_url:
            logger.warning("MAX /uploads не вернул url")
            return None

        up = await client.post(
            upload_url,
            headers={"Authorization": self.token},
            files={"data": ("image.jpg", image_bytes, "image/jpeg")},
            timeout=120.0,
        )
        if up.status_code >= 400:
            logger.warning(f"MAX upload отказ: {self._error(up)}")
            return None
        token = (up.json() or {}).get("token")
        if not token:
            # часть ответов кладёт токен в photos{}.token
            try:
                photos = (up.json() or {}).get("photos") or {}
                token = next(iter(photos.values()), {}).get("token")
            except Exception:
                token = None
        return token

    async def send_photo(
        self,
        image_bytes: bytes,
        caption: str = "",
        fmt: Optional[str] = "html",
    ) -> Dict[str, Any]:
        """Отправить фото с подписью. При неудаче — вернуть success=False."""
        if not self.enabled:
            return {"success": False, "skipped": True, "error": "MAX не настроен (token/chat_id)"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                token = await self._upload_image(client, image_bytes)
                if not token:
                    return {"success": False, "error": "upload failed"}

                body: Dict[str, Any] = {
                    "text": caption[:MAX_TEXT_LIMIT] if caption else None,
                    "attachments": [{"type": "image", "payload": {"token": token}}],
                    "notify": True,
                }
                if fmt and caption:
                    body["format"] = fmt
                if not caption:
                    body.pop("text", None)

                # файл обрабатывается на стороне MAX — до 4 попыток с растущей паузой
                delay = 2.0
                last_err = ""
                for attempt in range(1, 5):
                    resp = await client.post(
                        f"{self.base_url}/messages",
                        params={"chat_id": self.chat_id},
                        headers=self._headers(),
                        json=body,
                    )
                    if resp.status_code < 400:
                        return {"success": True, "attempts": attempt, "response": resp.json()}
                    last_err = self._error(resp)
                    if "attachment.not.ready" in last_err or "not.processed" in last_err:
                        logger.info(f"MAX ещё не обработал фото, попытка {attempt}, пауза {delay}s")
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    break
                return {"success": False, "error": last_err or "unknown"}
        except Exception as e:
            logger.error(f"MAX send_photo error: {e}")
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    async def send_post(self, text: str, image_bytes: Optional[bytes] = None) -> Dict[str, Any]:
        """Отправить пост: с фото, если оно есть, иначе текстом."""
        if image_bytes:
            caption = text if len(text) <= 900 else text[:890].rsplit(" ", 1)[0] + "…"
            res = await self.send_photo(image_bytes, caption=caption)
            if res.get("success"):
                # длинный текст не влез в подпись — досылаем остаток отдельным сообщением
                if len(text) > len(caption):
                    await asyncio.sleep(1.0)
                    rest = text[len(caption):].lstrip(" …\n")
                    if rest:
                        await self.send_text(rest)
                return res
            logger.warning(f"MAX фото не ушло ({res.get('error')}), отправляю текстом")
        return await self.send_text(text)

    async def me(self) -> Dict[str, Any]:
        """GET /me — проверка токена."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{self.base_url}/me", headers={"Authorization": self.token})
                if resp.status_code < 400:
                    return {"success": True, "bot": resp.json()}
                return {"success": False, "error": self._error(resp)}
        except Exception as e:
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    async def chats(self) -> List[Dict[str, Any]]:
        """GET /chats — список чатов бота (каналы тоже здесь, если бот админ)."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{self.base_url}/chats",
                    params={"count": 50},
                    headers={"Authorization": self.token},
                )
                if resp.status_code < 400:
                    return (resp.json() or {}).get("chats", [])
                logger.warning(f"MAX /chats отказ: {self._error(resp)}")
        except Exception as e:
            logger.error(f"MAX chats error: {e}")
        return []
