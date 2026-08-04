from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    app_name: str = "SmartReco"
    app_env: str = "development"
    secret_key: str = "dev-only-change-me"
    database_url: str = f"sqlite:///{BASE_DIR / 'smartreco.db'}"
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

    digest_time: str = "09:00"
    digest_timezone: str = "Asia/Kolkata"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()
