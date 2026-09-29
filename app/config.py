from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Stylist"
    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8010

    database_path: str = "data/ai_stylist.db"
    upload_dir: str = "uploads"
    generated_dir: str = "generated"

    openai_api_key: str | None = None
    preview_image_model: str = "gpt-image-2.5-flare"
    final_image_model: str = "gpt-image-2.5-sunburst"
    preview_image_quality: str = "medium"
    final_image_quality: str = "medium"
    image_size: str = "1024x1536"
    image_output_format: str = "webp"
    image_output_compression: int = 88
    max_reference_images: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
