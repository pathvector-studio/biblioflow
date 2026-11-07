from pydantic import BaseModel


class ChatStatus(BaseModel):
    enabled: bool
    provider: str  # none | anthropic | openai | gemini | ollama | openai_compatible
    model: str | None  # the configured model name; never a key or base URL
    vision_available: bool
    daily_requests_remaining: int | None  # None when chat is disabled
