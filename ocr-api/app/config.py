from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    api_key: str

    enable_paperless: bool = False
    paperless_url: str = "https://paperless.jru.me"
    paperless_token: str = ""

    enable_rclone: bool = False
    rclone_target: str = "OneDrive_Joe:scanner/"

    enable_filesystem: bool = False
    output_dir: str = "/output"

    ocr_language: str = "deu+eng+frk"
    trash_tmp_files: bool = True

    enable_mail: bool = False
    mail_to: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
