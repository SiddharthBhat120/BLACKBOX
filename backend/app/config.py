"""Settings from environment variables or a .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for raw in env.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    llm_model: str = os.getenv("LLM_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
    # Timestamps without a time zone are read in this zone, then stored in UTC.
    default_tz: str = os.getenv("DEFAULT_TZ", "UTC")
    # Which service calls which (used to weight trigger candidates). caller:callee,...
    dependencies: str = os.getenv("SERVICE_DEPENDENCIES", "gateway:orders,orders:payments")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "blackbox.db"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


settings = Settings()
