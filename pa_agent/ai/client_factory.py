"""Construct a generic HTTP API client without local desktop dispatch."""
from pa_agent.ai.deepseek_client import DeepSeekClient

def create_ai_client(settings, logger_=None):
    return DeepSeekClient(settings, logger_=logger_)
