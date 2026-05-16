"""
SethTradez configuration — loads .env first, then runtime_config.json merges
over the top so Telegram command changes survive restarts.
"""

import json
import os
from dotenv import load_dotenv

load_dotenv()

_RUNTIME_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "runtime_config.json")


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Required env var {key} is missing")
    return value


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


def _float(key: str, default: float = 0.0) -> float:
    return float(os.getenv(key, str(default)))


# Hyperliquid
HL_PRIVATE_KEY: str = os.getenv("HL_PRIVATE_KEY", "")
HL_WALLET_ADDRESS: str = os.getenv("HL_WALLET_ADDRESS", "")
HL_TESTNET: bool = _bool("HL_TESTNET", True)

# Telegram
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN_HYPERBETZ", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID_HYPERBETZ", "")

# Database (shared Railway PostgreSQL with SethBetz)
DATABASE_URL: str = os.getenv("DATABASE_URL", "")

# Trading parameters — overrideable at runtime via Telegram commands
STAKE_USDC: float = _float("STAKE_USDC", 10.0)
INITIAL_STOP_USD: float = _float("INITIAL_STOP_USD", 5.0)
TRAIL_DISTANCE_USD: float = _float("TRAIL_DISTANCE_USD", 10.0)
TRAIL_ACTIVATE_USD: float = _float("TRAIL_ACTIVATE_USD", 15.0)
MIN_CANDLE_MOVE_USD: float = _float("MIN_CANDLE_MOVE_USD", 40.0)
MAX_CANDLE_MOVE_USD: float = _float("MAX_CANDLE_MOVE_USD", 300.0)
DAILY_LOSS_LIMIT_USD: float = _float("DAILY_LOSS_LIMIT_USD", 50.0)
TIME_STOP_SECS: int = int(os.getenv("TIME_STOP_SECS", "1200"))

# Modes
PAPER_MODE: bool = _bool("PAPER_MODE", True)
FADE_MODE: bool = _bool("FADE_MODE", True)

# Keys persisted to runtime_config.json with their types
_PERSIST_KEYS: dict = {
    "STAKE_USDC": float,
    "INITIAL_STOP_USD": float,
    "TRAIL_DISTANCE_USD": float,
    "TRAIL_ACTIVATE_USD": float,
    "MIN_CANDLE_MOVE_USD": float,
    "MAX_CANDLE_MOVE_USD": float,
    "DAILY_LOSS_LIMIT_USD": float,
    "TIME_STOP_SECS": int,
    "PAPER_MODE": bool,
    "FADE_MODE": bool,
}


def load_runtime_config() -> None:
    """Read runtime_config.json and override module globals. Called at import time."""
    if not os.path.exists(_RUNTIME_CONFIG_PATH):
        return
    try:
        with open(_RUNTIME_CONFIG_PATH) as f:
            data = json.load(f)
        g = globals()
        for key, cast in _PERSIST_KEYS.items():
            if key in data:
                g[key] = cast(data[key])
    except Exception:
        pass  # corrupt file — fall back to env values silently


def save_runtime_config() -> None:
    """Write all persisted settings to runtime_config.json."""
    g = globals()
    data = {key: g[key] for key in _PERSIST_KEYS}
    with open(_RUNTIME_CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


# Merge runtime overrides over .env values on every startup
load_runtime_config()
