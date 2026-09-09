from agent_runtime.config import AgentCoreRuntimeSettings


def test_whatsapp_agentcore_memory_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("WHATSAPP_AGENTCORE_MEMORY_ENABLED", raising=False)

    assert AgentCoreRuntimeSettings().whatsapp_agentcore_memory_enabled is True


def test_whatsapp_agentcore_memory_can_be_disabled_from_environment(monkeypatch):
    monkeypatch.setenv("WHATSAPP_AGENTCORE_MEMORY_ENABLED", "false")

    assert AgentCoreRuntimeSettings().whatsapp_agentcore_memory_enabled is False
