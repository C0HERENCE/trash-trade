from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

from .config import load_settings
from .runtime import RuntimeEngine
from .api_server import app as api_app, status_store, stream_store, set_runtime_hooks


logger = logging.getLogger(__name__)

settings = load_settings()
engine = RuntimeEngine(settings, status_store, stream_store)
app: FastAPI = api_app
_engine_task = None


@app.on_event("startup")
async def _startup() -> None:
    global _engine_task
    set_runtime_hooks(state_cb=engine.runtime_state, alert_cb=engine.send_alert)
    _engine_task = asyncio.create_task(engine.start())


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _engine_task
    if _engine_task is not None:
        _engine_task.cancel()
    await engine.stop()
