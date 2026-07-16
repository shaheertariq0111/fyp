from __future__ import annotations

from typing import Any


def agentcore_actor_id(*, customer_id: str | None, user_id: str) -> str:
    return customer_id or user_id


def require_agentcore_memory_id(settings: Any) -> str:
    memory_id = getattr(settings, "agentcore_memory_id", "")
    if not memory_id:
        raise RuntimeError("AGENTCORE_MEMORY_ID is required for AgentCore Runtime conversation memory")
    return memory_id
