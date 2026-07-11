from dataclasses import dataclass


@dataclass(frozen=True)
class AgentCoreMemoryConfig:
    memory_id: str
    actor_id: str
    session_id: str

    @property
    def enabled(self) -> bool:
        return bool(self.memory_id)
