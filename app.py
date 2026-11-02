"""Explicit ASGI entry point for Vercel and uvicorn."""
from pa_agent.web.app import app

__all__ = ["app"]
