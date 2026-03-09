"""Configuration settings for the PR Dashboard."""

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # GitHub
    github_token: str = Field(default="", description="GitHub PAT for API access")
    github_org: str = Field(default="kyndryl-agentic-ai", description="Default GitHub org to track")

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/pr_dashboard",
        description="Async SQLAlchemy database URL",
    )
    database_echo: bool = Field(default=False, description="Echo SQL statements")

    @model_validator(mode="after")
    def _fix_database_url_scheme(self) -> "Settings":
        """Railway provides postgresql://, asyncpg needs postgresql+asyncpg://."""
        if self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        return self

    # Sync
    sync_interval_seconds: int = Field(
        default=180, description="Seconds between GitHub sync cycles"
    )

    # Auth
    dashboard_password: str = Field(default="", description="Dashboard login password")
    secret_key: str = Field(
        default="change-me-in-production",
        description="Secret key for signing session cookies",
    )
    session_max_age_seconds: int = Field(
        default=7 * 24 * 3600, description="Session cookie lifetime"
    )

    # GitHub OAuth
    github_oauth_client_id: str = Field(default="", description="GitHub OAuth App client ID")
    github_oauth_client_secret: str = Field(
        default="", description="GitHub OAuth App client secret"
    )

    # Frontend URL (for OAuth redirect; defaults to Vite dev server, override in production)
    frontend_url: str = Field(
        default="http://localhost:5173", description="Frontend URL for redirects"
    )

    # Dev mode
    dev_mode: bool = Field(default=False, description="Enable dev-only features (impersonation)")
    dev_alice_token: str = Field(default="", description="GitHub PAT for Alice dev user")
    dev_bob_token: str = Field(default="", description="GitHub PAT for Bob dev user")

    # Server
    host: str = Field(default="0.0.0.0", description="Server bind host")
    port: int = Field(default=8000, description="Server bind port")
    log_level: str = Field(default="INFO", description="Logging level")


settings = Settings()
