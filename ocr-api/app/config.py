from functools import lru_cache
from typing import Literal

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
    output_dir: str = "/ocr-api/output"

    ocr_language: str = "deu+eng+frk"
    ocr_engine: Literal["tesseract", "paddleocr", "gcv", "openrouter"] = "tesseract"
    paddle_det_limit_type: Literal["max", "min"] = "max"
    paddle_det_limit_side_len: int = 1600
    # Lightweight mobile detector: much faster on CPU than the default
    # PP-OCRv5_server_det, small accuracy hit on clean docs. Recognition stays
    # on the accurate server default. Must be baked into the Docker warmup layer.
    paddle_text_det_model: str = "PP-OCRv5_mobile_det"

    # OCR_ENGINE=openrouter: Tesseract builds the PDF text layer, a vision LLM
    # transcribes each page. Cheap model first; handwritten/uncertain pages are
    # re-run on the strong model (empty = no escalation). Uses openrouter_api_key.
    ocr_llm_model: str = "google/gemini-3.1-flash-lite"
    ocr_llm_strong_model: str = "google/gemini-3.1-pro-preview"
    ocr_llm_fallback_models: list[str] = []  # env: JSON list
    ocr_llm_concurrency: int = 3
    ocr_llm_timeout: int = 90
    # Output token budgets; reasoning tokens count toward them. The cheap pass
    # normally needs < 800 tokens, so a small cap cuts a repetition loop off after
    # seconds (it then escalates as truncated). Strong/line+box replies need up to
    # ~6k on dense table pages.
    ocr_llm_max_tokens: int = 3000
    ocr_llm_strong_max_tokens: int = 16000
    ocr_llm_image_max_side: int = 2000
    ocr_llm_escalate_unclear_max: int = 2
    # OpenRouter reasoning effort per tier ("" = provider default, no `reasoning` sent).
    # Measured 2026-09-16 on a 5-page handwritten scan: gemini-3.5-flash ran away to
    # ~16k reasoning tokens (60+ s, truncations) at any effort; gemini-3.1-pro-preview
    # at its default finished every page in 4-7 s with the best transcripts.
    ocr_llm_reasoning_effort: Literal["", "none", "minimal", "low", "medium", "high"] = ""
    ocr_llm_strong_reasoning_effort: Literal["", "none", "minimal", "low", "medium", "high"] = ""
    # LLM text in the PDF text layer (Ctrl+F, Paperless full-text search):
    #   off        - Tesseract layer only
    #   block      - plus the transcript as an invisible, unpositioned block per page
    #   positioned - escalated pages: strong model returns lines with boxes, which
    #                replace Tesseract's lines (falls back to block, then Tesseract)
    ocr_llm_pdf_text: Literal["off", "block", "positioned"] = "off"

    trash_tmp_files: bool = True

    enable_ai_metadata: bool = False
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-haiku-4.5"
    ai_document_language: str = "de"

    enable_mail: bool = False
    mail_to: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    gcv_credentials_file: str = ""
    gcv_project_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
