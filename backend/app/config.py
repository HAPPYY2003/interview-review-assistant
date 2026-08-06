from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    root_dir: Path = ROOT_DIR
    data_dir: Path = ROOT_DIR / "data"
    knowledge_dir: Path = ROOT_DIR / "knowledge"
    frontend_dir: Path = ROOT_DIR / "frontend"
    app_env: str = os.getenv("APP_ENV", "development")
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    agent_runtime: str = os.getenv("AGENT_RUNTIME", "fixture").strip().lower()
    demo_mode: bool = _bool("DEMO_MODE")
    web_verify_enabled: bool = _bool("WEB_VERIFY_ENABLED")
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "").strip()
    llm_model_id: str = os.getenv("LLM_MODEL_ID", "gpt-4.1-mini").strip()
    llm_api_key: str = os.getenv("LLM_API_KEY", "").strip()
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip()
    llm_timeout: int = int(os.getenv("LLM_TIMEOUT", "90"))
    max_file_bytes: int = 5 * 1024 * 1024

    @property
    def database_path(self) -> Path:
        return self.data_dir / "offer_radar.db"

    @property
    def real_agent_enabled(self) -> bool:
        return self.agent_runtime == "helloagents" and bool(self.llm_api_key)

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.data_dir / "uploads",
            self.data_dir / "sessions",
            self.data_dir / "traces",
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()

