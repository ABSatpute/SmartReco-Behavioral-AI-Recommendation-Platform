from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    app_name: str = "SmartReco"
    app_env: str = "development"
    app_project_name: str = "smartreco"

    langsmith_api_key: str = ""
    secret_key: str = "dev-only-change-me"
    database_url: str = "postgresql://smartreco:smartreco@localhost:5432/smartreco"
    app_base_url: str = "http://localhost:8000"
    session_cookie: str = "smartreco_session"
    session_ttl_days: int = 30

    mesh_base_url: str = "https://api.meshapi.ai/v1"
    mesh_api_key: str = ""
    llm_model: str = "openai/gpt-4o"
    embedding_model: str = "openai/text-embedding-3-small"

    pinecone_api_key: str = ""
    pinecone_environment: str = ""
    pinecone_index: str = "smartreco"
    pinecone_dimension: int = 1536
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"

    # Agent engine tuning
    min_events_threshold: int = 3
    min_reco_run_interval_minutes: int = 30
    reco_validity_minutes: int = 60
    agent_max_refine_loops: int = 2
    agent_max_generate_retries: int = 2
    analysis_model: str = "minimax/m2-her"

    digest_time: str = "09:00"
    digest_timezone: str = "Asia/Kolkata"

    # Email delivery: "smtp" (any ESP via SMTP) or "resend" (Resend HTTP API)
    email_backend: str = "smtp"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    resend_api_key: str = ""

    # Notification channels (comma-separated): email, telegram
    notification_channels: str = "email,telegram"
    telegram_bot_token: str = ""

    @property
    def notification_channels_list(self) -> list[str]:
        return [
            c.strip().lower()
            for c in self.notification_channels.split(",")
            if c.strip().lower()
        ]


settings = Settings()