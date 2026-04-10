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
    mail_to: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
