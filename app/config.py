from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str
    debug: bool = False
    upload_dir: str = "./uploads"
    output_dir: str = "./outputs"
    max_upload_bytes: int = 5 * 1024**3  # 5 GiB (ADR 006)
    max_duration_seconds: int = 10800  # 3-hour limit (strict >), ADR 006
    media_backend: str = "ffmpeg"  # "ffmpeg" (real) | "fake" (CI/tests)
    transcriber_backend: str = "faster_whisper"  # "faster_whisper" | "mock" (CI/tests)
    whisper_model: str = "turbo"
    # Domain terms passed to whisper as hotwords (ADR 006); per-job terms are appended.
    whisper_glossary: str = (
        "Claude, Claude Code, Anthropic, ChatGPT, OpenAI, Gemini, GitHub, Git, API, MCP, "
        "CLAUDE.md, Python, JavaScript, Docker, VS Code, Cursor, prompt, skill, hook"
    )
    frame_backend: str = "crv"  # "crv" (scene-change) | "fake" (CI/tests)
    frames_per_minute: float = 5.0
    max_frames: int = 300
    min_frames: int = 20
    frame_width: int = 1280
    rq_queue_name: str = "default"
    rq_job_timeout: int = 14400  # 4 h: a 3 h video needs ~1-1.5 h of processing
    mock_processing_delay_seconds: int = 0
    mock_force_fail: bool = False
    mock_failure_trigger_enabled: bool = False
    cleanup_interval_seconds: int = 600
    session_cookie_name: str = "aivps_session"
    session_cookie_max_age: int = 2592000  # 30 days
    session_cookie_secure: bool = False
    # Host / Origin allowlist for the local tool (DNS-rebinding + CSRF guard)
    allowed_hosts: list[str] = ["localhost", "127.0.0.1"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
