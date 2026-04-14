"""
core/config.py
All settings loaded from .env file.
Import settings from here — never hardcode keys anywhere else.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # App
    app_env:        str = os.getenv("APP_ENV", "development")
    secret_key:     str = os.getenv("SECRET_KEY", "dev-secret-change-in-production")

    # LLM — Groq primary, OpenRouter fallback
    groq_api_key:       str = os.getenv("GROQ_API_KEY", "")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_model:   str = os.getenv("OPENROUTER_MODEL", "google/gemini-flash-1.5")

    # Supabase
    supabase_url:         str = os.getenv("SUPABASE_URL", "")
    supabase_key:         str = os.getenv("SUPABASE_KEY", "")
    supabase_service_key: str = os.getenv("SUPABASE_SERVICE_KEY", "")

    # Stripe
    stripe_secret_key:    str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret:str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    # Rate limits
    free_daily_signals: int = int(os.getenv("FREE_DAILY_SIGNALS", "5"))
    pro_daily_signals:  int = int(os.getenv("PRO_DAILY_SIGNALS", "999"))

    # CORS
    allowed_origins: list = None

    def __post_init__(self):
        raw = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
        self.allowed_origins = [o.strip() for o in raw.split(",")]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


settings = Settings()

from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
