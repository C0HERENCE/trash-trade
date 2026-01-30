from __future__ import annotations

import logging
import time
from typing import Dict, Optional

import httpx

from .config import AlertsConfig
from .db import Database
from .models import Alert


logger = logging.getLogger(__name__)


class AlertManager:
    def __init__(self, db: Database, config: AlertsConfig) -> None:
        self._db = db
        self._config = config
        self._dedup: Dict[str, int] = {}

    async def alert(self, level: str, title: str, message: str, dedup_key: Optional[str] = None) -> None:
        now_ms = int(time.time() * 1000)

        if dedup_key:
            last = self._dedup.get(dedup_key)
            if last is not None and (now_ms - last) < self._config.dedup_ttl_ms:
                return
            self._dedup[dedup_key] = now_ms

        full_message = f"{title}: {message}" if title else message

        channels_sent = 0
        if not self._config.enabled:
            await self._insert_alert("disabled", level, full_message, dedup_key, now_ms)
            return

        if self._config.telegram.enabled:
            ok = await self._send_telegram(full_message)
            channels_sent += 1 if ok else 0
            await self._insert_alert("telegram", level, full_message, dedup_key, now_ms)

        if self._config.bark.enabled:
            ok = await self._send_bark(title, message)
            channels_sent += 1 if ok else 0
            await self._insert_alert("bark", level, full_message, dedup_key, now_ms)

        if self._config.wecom.enabled:
            ok = await self._send_wecom(full_message)
            channels_sent += 1 if ok else 0
            await self._insert_alert("wecom", level, full_message, dedup_key, now_ms)

        if channels_sent == 0:
            await self._insert_alert("none", level, full_message, dedup_key, now_ms)

    async def _insert_alert(
        self, channel: str, level: str, message: str, dedup_key: Optional[str], now_ms: int
    ) -> None:
        try:
            await self._db.insert_alert(
                Alert(
                    timestamp=now_ms,
                    channel=channel,
                    level=level,
                    message=message,
                    dedup_key=dedup_key,
                    created_at=now_ms,
                )
            )
        except Exception:
            logger.exception("Insert alert failed")

    async def _send_telegram(self, message: str) -> bool:
        token = self._config.telegram.token
        chat_id = self._config.telegram.chat_id
        if not token or not chat_id:
            logger.warning("Telegram alert enabled but token/chat_id missing")
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        return await self._post_json(url, payload, "telegram")

    async def _send_bark(self, title: str, message: str) -> bool:
        url = self._config.bark.url.rstrip("/")
        key = self._config.bark.key
        if not url or not key:
            logger.warning("Bark alert enabled but url/key missing")
            return False
        endpoint = f"{url}/{key}"
        payload = {"title": title, "body": message}
        return await self._post_json(endpoint, payload, "bark")

    async def _send_wecom(self, message: str) -> bool:
        webhook = self._config.wecom.webhook
        if not webhook:
            logger.warning("WeCom alert enabled but webhook missing")
            return False
        payload = {"msgtype": "text", "text": {"content": message}}
        return await self._post_json(webhook, payload, "wecom")

    async def _post_json(self, url: str, payload: dict, channel: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            return True
        except Exception:
            logger.exception("Alert send failed (%s)", channel)
            return False
