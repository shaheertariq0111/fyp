from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentRequestContext:
    user_id: str
    agent_session_id: str
    branch_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


_current_context: ContextVar[AgentRequestContext | None] = ContextVar("agent_request_context", default=None)


def get_request_context() -> AgentRequestContext:
    context = _current_context.get()
    if context is None:
        raise RuntimeError("Agent request context has not been injected")
    return context


@contextmanager
def request_context(context: AgentRequestContext):
    token = _current_context.set(context)
    try:
        yield context
    finally:
        _current_context.reset(token)
