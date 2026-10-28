from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://bats:change_me@localhost:5432/bats"
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    jwt_secret: str  # required: the app refuses to start without it
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480
    audit_anchor_path: str = "audit_anchor.log"  # off-database record of the chain head
    audit_verify_interval_seconds: int = 300
    hms_retry_interval_seconds: int = 10
    hms_api_key: str  # shared secret the HMS presents when calling BATS; required
    enable_mock_hms: bool = True  # the placeholder HMS is unauthenticated simulation: turn off in production
    bats_inbound_url: str = "http://localhost:8000/api/v1/hms/inbound"
    hms_webhook_url: str = "http://localhost:8000/mock-hms/webhook"
    hms_timeout_seconds: float = 3.0


settings = Settings()
