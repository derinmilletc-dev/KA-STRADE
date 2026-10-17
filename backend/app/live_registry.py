"""Per-user LIVE engine state (multi-user LIVE).

Off by default (MULTI_USER_LIVE). While off nothing here is used and the single shared
engine behaves exactly as before. While on, ``app.state.v25_execution`` returns the plain
state dict that belongs to the user bound to the current request/task, so every existing
call site keeps working but each member only ever sees and changes their own state.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from starlette.datastructures import State

from .web_security import env_flag

logger = logging.getLogger(__name__)

SNAPSHOT_PREFIX = "v25_live:state:"
CURRENT_USER: ContextVar[str | None] = ContextVar("live_current_user", default=None)


def enabled() -> bool:
    """The flag only counts when a database exists: per-user states live in PostgreSQL."""
    return env_flag("MULTI_USER_LIVE") and bool(os.getenv("DATABASE_URL", "").strip())


def bind(user_id: str | None) -> Any:
    """Bind the current request/task to a member. Returns a token for reset."""
    return CURRENT_USER.set(str(user_id) if user_id else None)


def snapshot_key_for(user_id: str) -> str:
    return f"{SNAPSHOT_PREFIX}{user_id}"


class LiveRegistry:
    def __init__(self, application: Any, new_state: Callable[[Any, str], dict[str, Any]],
                 run_engine: Callable[[Any], Awaitable[None]] | None = None) -> None:
        self.application = application
        self._new_state = new_state
        self._run_engine = run_engine
        self.states: dict[str, dict[str, Any]] = {}
        self.engines: dict[str, asyncio.Task[None]] = {}
        # Used when no member is bound (health checks, startup): never belongs to any member.
        self.system: dict[str, Any] = new_state(application, "")

    def state_for(self, user_id: str | None) -> dict[str, Any]:
        uid = str(user_id or "")
        if not uid:
            return self.system
        state = self.states.get(uid)
        if state is None:
            state = self._new_state(self.application, uid)
            self.states[uid] = state
        return state

    def current(self) -> dict[str, Any]:
        return self.state_for(CURRENT_USER.get())

    def ensure_engine(self, user_id: str | None) -> bool:
        """Start this member's own control loops once. Safe to call repeatedly."""
        uid = str(user_id or "")
        if not uid or self._run_engine is None:
            return False
        task = self.engines.get(uid)
        if task is not None and not task.done():
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self.state_for(uid)
        run = self._run_engine

        async def runner() -> None:
            bind(uid)  # task-local: every loop below this point only sees this member's state
            await run(self.application)

        self.engines[uid] = loop.create_task(runner())
        return True

    async def stop_engines(self) -> None:
        tasks = [task for task in self.engines.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.engines.clear()


class UserAwareState(State):
    """app.state whose ``v25_execution`` is the bound member's own state."""

    def __getattr__(self, key: str) -> Any:
        if key == "v25_execution":
            registry = self._state.get("live_registry")
            if registry is not None:
                return registry.current()
        return super().__getattr__(key)


def registry_of(application: Any) -> LiveRegistry | None:
    value = getattr(getattr(application, "state", None), "live_registry", None)
    return value if isinstance(value, LiveRegistry) else None
