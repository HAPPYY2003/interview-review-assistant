import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

os.environ["AGENT_RUNTIME"] = "fixture"
os.environ["LLM_API_KEY"] = ""
os.environ["DEEPGRAM_API_KEY"] = ""
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="offer-radar-tests-"))
os.environ["OFFER_RADAR_DATA_DIR"] = str(_TEST_DATA_DIR)
atexit.register(shutil.rmtree, _TEST_DATA_DIR, True)

from backend.app.config import ROOT_DIR, Settings


@pytest.fixture
def settings_factory(tmp_path):
    def factory(**overrides):
        values = {
            "root_dir": tmp_path,
            "data_dir": tmp_path / "data",
            "knowledge_dir": ROOT_DIR / "knowledge",
            "frontend_dir": ROOT_DIR / "frontend",
            "agent_runtime": "fixture",
            "demo_mode": False,
            "web_verify_enabled": False,
            "tavily_api_key": "",
            "llm_api_key": "",
        }
        values.update(overrides)
        settings = Settings(**values)
        settings.ensure_directories()
        return settings
    return factory
