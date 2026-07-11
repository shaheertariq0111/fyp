from functools import lru_cache

from src.agent_client.client import AgentRuntimeClient
from src.agent_client.local import LocalStrandsAgentRuntimeClient


@lru_cache
def get_agent_runtime_client() -> AgentRuntimeClient:
    return LocalStrandsAgentRuntimeClient()
