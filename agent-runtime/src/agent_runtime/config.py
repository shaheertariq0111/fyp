from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentCoreRuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore")

    agentcore_memory_id: str = ""
    log_level: str = "INFO"
    aws_region: str = Field(default="us-east-1", min_length=1)


@lru_cache
def get_agentcore_runtime_settings() -> AgentCoreRuntimeSettings:
    return AgentCoreRuntimeSettings()
