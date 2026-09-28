from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Stylist"
    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8010
    database_path: str = "data/ai_stylist.db"
    upload_dir: str = "uploads"
    openai_api_key: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
