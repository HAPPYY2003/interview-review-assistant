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
    data_dir: Path = Path(os.getenv("OFFER_RADAR_DATA_DIR", str(ROOT_DIR / "data")))
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
    agent_task_timeout: int = int(os.getenv("AGENT_TASK_TIMEOUT", "180"))
    agent_heartbeat_interval: int = int(os.getenv("AGENT_HEARTBEAT_INTERVAL", "10"))
    review_topic_concurrency: int = int(os.getenv("REVIEW_TOPIC_CONCURRENCY", "3"))
    review_fast_path_enabled: bool = _bool("REVIEW_FAST_PATH_ENABLED", True)
    review_evidence_packet_limit: int = int(os.getenv("REVIEW_EVIDENCE_PACKET_LIMIT", "12"))
    parse_worker_concurrency: int = int(os.getenv("PARSE_WORKER_CONCURRENCY", "3"))
    practice_brief_timeout: int = int(os.getenv("PRACTICE_BRIEF_TIMEOUT", "45"))
    practice_review_timeout: int = int(os.getenv("PRACTICE_REVIEW_TIMEOUT", "60"))
    asr_provider: str = os.getenv("ASR_PROVIDER", "deepgram").strip().lower()
    deepgram_api_key: str = os.getenv("DEEPGRAM_API_KEY", "").strip()
    deepgram_model: str = os.getenv("DEEPGRAM_MODEL", "nova-3").strip()
    max_audio_bytes: int = int(os.getenv("MAX_AUDIO_BYTES", str(200 * 1024 * 1024)))
    max_audio_seconds: int = int(os.getenv("MAX_AUDIO_SECONDS", str(120 * 60)))
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
            self.data_dir / "parse-runs",
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
