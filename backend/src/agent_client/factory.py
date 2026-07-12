from functools import lru_cache

from src.agent_client.agentcore import AgentCoreRuntimeClient
from src.agent_client.client import AgentRuntimeClient
from src.agent_client.local import LocalStrandsAgentRuntimeClient
from src.infrastructure.config import get_settings


@lru_cache
def get_agent_runtime_client() -> AgentRuntimeClient:
    settings = get_settings()
    if settings.agentcore_runtime_arn:
        return AgentCoreRuntimeClient(
            runtime_arn=settings.agentcore_runtime_arn,
            aws_region=settings.aws_region,
        )
    return LocalStrandsAgentRuntimeClient()
