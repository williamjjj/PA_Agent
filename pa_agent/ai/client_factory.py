"""HTTP model client factory; desktop agent processes are not supported."""
from pa_agent.ai.deepseek_client import DeepSeekClient

def create_ai_client(settings, **kwargs):
    return DeepSeekClient(settings)
