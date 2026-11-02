"""Optional market-data credentials for the local research adapter."""
from pydantic import BaseModel, ConfigDict


class ResearchSourceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    birdeye_api_key: str = ""
