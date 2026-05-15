"""
SethTradez configuration — loads all settings from environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv()


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

# Trading parameters
STAKE_USDC: float = _float("STAKE_USDC", 10.0)
INITIAL_STOP_USD: float = _float("INITIAL_STOP_USD", 5.0)
TRAIL_DISTANCE_USD: float = _float("TRAIL_DISTANCE_USD", 10.0)
TRAIL_ACTIVATE_USD: float = _float("TRAIL_ACTIVATE_USD", 5.0)
MIN_CANDLE_MOVE_USD: float = _float("MIN_CANDLE_MOVE_USD", 40.0)
DAILY_LOSS_LIMIT_USD: float = _float("DAILY_LOSS_LIMIT_USD", 50.0)

# Modes
PAPER_MODE: bool = _bool("PAPER_MODE", True)
FADE_MODE: bool = _bool("FADE_MODE", False)
