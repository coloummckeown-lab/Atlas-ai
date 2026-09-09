from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-sol"
    fast_model: str = "gpt-5.6-luna"
    database_url: str = ""
    atlas_name: str = "CMK"
    enable_web_search: bool = False
    enable_self_evolution: bool = True
    trace_sensitive_data: bool = False
    cmk_admin_username: str = ""
    cmk_admin_password_hash: str = ""
    cmk_reset_email: str = ""
    cmk_session_secret: str = ""
    cmk_public_url: str = "https://atlas-ai-app.onrender.com"
    cmk_version: str = "4.3.1"
    cmk_github_token: str = ""
    cmk_github_repo: str = "coloummckeown-lab/Atlas-ai"
    cmk_github_branch: str = "main"
    cmk_github_package_path: str = "ATLAS_COMPLETE_PACKAGE.zip"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True

    @field_validator("database_url", "openai_api_key", "cmk_github_token", mode="before")
    @classmethod
    def _clean_secret_values(cls, value):
        if value is None:
            return ""
        value = str(value).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1].strip()
        return value

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
