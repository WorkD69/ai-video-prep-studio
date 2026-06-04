from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str
    debug: bool = False
    upload_dir: str = "./uploads"
    output_dir: str = "./outputs"
    max_upload_bytes: int = 524288000  # 500 MB
    rq_queue_name: str = "default"
    rq_job_timeout: int = 600
    mock_processing_delay_seconds: int = 0
    mock_force_fail: bool = False
    mock_failure_trigger_enabled: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
