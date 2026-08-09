from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.models.tool_responses import TransactionalEffect


@dataclass(frozen=True)
class AgentInvocationRequest:
    message: str
    user_id: str
    agent_session_id: str
    branch_id: str | None = None
    customer_id: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    channel: str = "web"
    request_id: str | None = None
    expected_write_tool: str | None = None
    required_effect: TransactionalEffect | None = None
    available_options: list[dict[str, str]] | None = None


@dataclass(frozen=True)
class AgentInvocationResult:
    text: str
    raw_result: Any
