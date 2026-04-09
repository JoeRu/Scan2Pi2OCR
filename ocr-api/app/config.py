from pydantic_settings import BaseSettings


class Settings(BaseSettings):
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

    class Config:
        env_file = ".env"


settings = Settings()
