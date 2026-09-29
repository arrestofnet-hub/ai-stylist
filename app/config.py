from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Stylist"
    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8010
    mcp_port: int = 8011
    public_base_url: str = "http://localhost:8010"

    database_path: str = "data/ai_stylist.db"
    upload_dir: str = "uploads"
    generated_dir: str = "generated"

    openai_api_key: str | None = None
    admin_api_key: str | None = None
    preview_image_model: str = "gpt-image-2.5-flare"
    final_image_model: str = "gpt-image-2.5-sunburst"
    preview_image_quality: str = "medium"
    final_image_quality: str = "medium"
    image_size: str = "1024x1536"
    image_output_format: str = "webp"
    image_output_compression: int = 88
    max_reference_images: int = 3
    free_tries_on_signup: int = 3
    preview_credit_cost: int = 1
    final_credit_cost: int = 4
    stale_generation_minutes: int = 30
    generated_retention_days: int = 30
    max_processing_generations_per_profile: int = 1

    image_text_input_usd_per_million: float = 5.0
    image_input_usd_per_million: float = 8.0
    image_output_usd_per_million: float = 30.0
    usd_kzt_rate: float = 0.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
