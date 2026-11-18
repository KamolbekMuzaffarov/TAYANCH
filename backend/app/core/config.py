from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    APP_ENV: str = "development"  # development | test | staging | production
    APP_DEBUG: bool = False
    APP_BASE_URL: str = "http://localhost:3000"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173"

    # Live demo database (built by scripts/seed_national.py). The older perinatal_dev.db is left untouched.
    DATABASE_URL: str = f"sqlite:///{(BACKEND_DIR / 'perinatal_live.db').as_posix()}"
    # Separate owner role for DDL/migrations (least privilege); falls back to DATABASE_URL.
    MIGRATION_DATABASE_URL: str = ""
    REDIS_URL: str | None = None
    DATA_DIR: Path = PROJECT_DIR / "data" / "processed"
    MODEL_DIR: Path = BACKEND_DIR / "models_store"

    JWT_SECRET: str = "dev-only-change-me-dev-only-change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_MINUTES: int = 15
    REFRESH_TOKEN_DAYS: int = 7
    # Fernet key (urlsafe base64, 32 bytes). Dev default is derived from JWT secret if empty.
    ENCRYPTION_KEY: str = ""
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 10

    # Claude: non-clinical allowlisted assistant only
    CLAUDE_ENABLED: bool = True
    CLAUDE_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-haiku-5-5"
    CLAUDE_EXTERNAL_PATIENT_DATA: bool = False  # hard invariant: patient-level data never leaves the country
    CLAUDE_ALLOW_AGGREGATE_HEALTH_DATA: bool = False  # free-form aggregate registry data: still not allowed
    # The ONLY aggregate path: the closed, k-anonymised schema of the live "AI brief" (privacy_gateway.AggregateBriefPayload).
    CLAUDE_ALLOW_KANON_AGGREGATES: bool = True
    CLAUDE_BRIEF_TIMEOUT_SECONDS: float = 6.0
    CLAUDE_LOG_PROMPT_BODY: bool = False
    CLAUDE_TIMEOUT_SECONDS: int = 30
    CLAUDE_MAX_QUESTION_CHARS: int = 600

    # Voice
    VOICE_PROVIDER: str = "local"  # local | voicelab
    LOCAL_STT_BASE_URL: str = ""  # e.g. http://127.0.0.1:9001 (self-hosted faster-whisper)
    LOCAL_STT_DEV_STUB: bool = True  # dev/test only: deterministic stub when no local STT server
    VOICELAB_ENABLED: bool = False
    VOICELAB_API_KEY: str = ""
    VOICELAB_BASE_URL: str = "https://api.voicelab.uz"
    VOICELAB_EXTERNAL_PROCESSING_APPROVED: bool = False
    VOICE_STORE_RAW_AUDIO: bool = False
    VOICE_MAX_AUDIO_BYTES: int = 15 * 1024 * 1024
    VOICE_DRAFT_TTL_MINUTES: int = 30
    VOICE_TEMP_AUDIO_TTL_MINUTES: int = 15

    # Clinical rules: demo rules (published-guideline thresholds pending local approval)
    CLINICAL_RULES_ALLOW_UNAPPROVED: bool = True
    OBSERVATION_FRESHNESS_DAYS: int = 30
    ALERT_SLA_MINUTES_RED: int = 30
    ALERT_SLA_MINUTES_YELLOW: int = 240

    # Regional methodology
    REGION_STALE_AFTER_YEARS: int = 2
    REPORT_MIN_CELL: int = 5  # same k as the AI-brief gateway (privacy_gateway.K_ANON)

    # Live layer: background simulator (development only) and demo-session lifetime
    LIVE_SIMULATOR: bool = True
    LIVE_DEMO_SESSION_HOURS: int = 14
    LIVE_DEMO_LOGIN: bool = False  # password-less demo personas; needs APP_ENV != production/staging AND this flag
    LIVE_LOGIN_RATE_PER_MINUTE: int = 20
    LIVE_WRITE_RATE_PER_MINUTE: int = 60  # per user, all live write endpoints together
    LIVE_MAX_BODY_BYTES: int = 64 * 1024
    LIVE_MAX_JSON_DEPTH: int = 8

    SMS_PROVIDER_ENABLED: bool = False
    # Accounts (docs/AUTH_ADMIN_CONTRACT.md): one-time codes by e-mail / SMS, Google sign-in, bootstrap admin
    GOOGLE_CLIENT_ID: str = ""            # empty = Google sign-in disabled
    SMTP_HOST: str = ""                   # empty = no mail provider (dev: the code is returned as dev_code)
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMS_PROVIDER_URL: str = ""            # generic HTTPS JSON gateway (stub adapter); needs SMS_PROVIDER_ENABLED=true
    SMS_API_KEY: str = ""
    SMS_FROM: str = ""
    ADMIN_BOOTSTRAP_IDENTIFIER: str = ""  # e-mail or phone whose first registration becomes the admin
    OTP_TTL_SECONDS: int = 300
    OTP_MAX_ATTEMPTS: int = 5
    OTP_START_PER_IDENTIFIER: int = 5     # per 10 minutes
    OTP_START_PER_IP: int = 20            # per 10 minutes
    ACCOUNT_VERIFY_PER_IP: int = 40       # per 10 minutes (verify / second factor / passkey / google)
    ACCOUNT_LOCK_AFTER: int = 8           # failed sign-in attempts before a 15-minute lock
    PUBLIC_RATE_PER_MINUTE: int = 60
    PUSH_PROVIDER_ENABLED: bool = False

    LOG_LEVEL: str = "INFO"
    LOG_CLINICAL_PAYLOADS: bool = False
    SESSION_REPLAY_ENABLED: bool = False
    ANALYTICS_CAPTURE_FOR_CLINICAL_PAGES: bool = False

    seed_demo_password: str = Field(default="", alias="SEED_DEMO_PASSWORD")

    @property
    def is_production(self) -> bool:
        return self.APP_ENV in {"production", "staging"}

    @model_validator(mode="after")
    def _privacy_invariants(self) -> "Settings":
        # Hard invariants: no code path supports these, so refuse to start.
        if self.CLAUDE_EXTERNAL_PATIENT_DATA:
            raise ValueError("CLAUDE_EXTERNAL_PATIENT_DATA=true is not supported: patient data never goes to Claude")
        if self.CLAUDE_LOG_PROMPT_BODY:
            raise ValueError("CLAUDE_LOG_PROMPT_BODY=true is not allowed")
        if self.LOG_CLINICAL_PAYLOADS:
            raise ValueError("LOG_CLINICAL_PAYLOADS=true is not allowed")
        if self.is_production:
            problems = []
            if self.APP_DEBUG:
                problems.append("APP_DEBUG must be false")
            if self.VOICE_STORE_RAW_AUDIO:
                problems.append("VOICE_STORE_RAW_AUDIO must be false")
            if self.SESSION_REPLAY_ENABLED or self.ANALYTICS_CAPTURE_FOR_CLINICAL_PAGES:
                problems.append("session replay / clinical analytics must be off")
            if self.CLAUDE_ALLOW_AGGREGATE_HEALTH_DATA:
                problems.append("CLAUDE_ALLOW_AGGREGATE_HEALTH_DATA requires a privacy review; keep false")
            if self.LIVE_DEMO_LOGIN:
                problems.append("LIVE_DEMO_LOGIN must be off")
            if self.JWT_SECRET.startswith("dev-only") or not self.ENCRYPTION_KEY:
                problems.append("JWT_SECRET and ENCRYPTION_KEY must come from the secret manager")
            if self.LOCAL_STT_DEV_STUB:
                problems.append("LOCAL_STT_DEV_STUB must be false")
            if self.CLINICAL_RULES_ALLOW_UNAPPROVED:
                problems.append("CLINICAL_RULES_ALLOW_UNAPPROVED must be false (only approved rules may alert)")
            if problems:
                raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
