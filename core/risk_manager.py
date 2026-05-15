"""
Risk manager — enforces daily loss limit, paper mode toggle, and trading pause state.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional, Callable

import config
from utils import logger


class RiskManager:
    """
    Enforces:
    - Daily loss limit (auto-pause if breached)
    - Paper mode toggle
    - Single position enforcement (validated in PositionManager)
    """

    def __init__(self, on_limit_hit: Optional[Callable[[], None]] = None):
        self.daily_pnl: float = 0.0
        self.is_paused: bool = False
        self.paper_mode: bool = config.PAPER_MODE
        self._on_limit_hit = on_limit_hit
        self._daily_loss_limit: float = config.DAILY_LOSS_LIMIT_USD
        self._reset_task: Optional[asyncio.Task] = None
        self._pending_live_confirm: bool = False

    def check_ok(self) -> tuple[bool, str]:
        if self.is_paused:
            return False, "Trading is paused"
        return True, "ok"

    def record_trade_pnl(self, pnl_usd: float) -> None:
        self.daily_pnl += pnl_usd
        logger.info(f"Daily P&L updated: ${self.daily_pnl:+.4f}")
        self.check_daily_limit()

    def check_daily_limit(self) -> None:
        if self.daily_pnl <= -self._daily_loss_limit:
            logger.error(f"Daily loss limit hit: ${self.daily_pnl:.2f} >= limit ${self._daily_loss_limit:.2f}")
            self.is_paused = True
            if self._on_limit_hit:
                self._on_limit_hit()

    def reset_daily_pnl(self) -> None:
        logger.info(f"Daily P&L reset (was ${self.daily_pnl:+.4f})")
        self.daily_pnl = 0.0
        if self.is_paused:
            logger.info("Auto-unpausing after daily reset")
            self.is_paused = False

    def pause(self) -> None:
        self.is_paused = True
        logger.info("Trading PAUSED")

    def resume(self) -> None:
        self.is_paused = False
        logger.info("Trading RESUMED")

    def toggle_paper(self, on: bool) -> None:
        self.paper_mode = on
        logger.info(f"Paper mode {'ON' if on else 'OFF'}")

    def set_daily_limit(self, limit: float) -> None:
        self._daily_loss_limit = limit
        logger.info(f"Daily loss limit set to ${limit:.2f}")

    async def start_midnight_reset(self) -> None:
        while True:
            now = datetime.now(timezone.utc)
            seconds_until_midnight = (
                (24 - now.hour) * 3600 - now.minute * 60 - now.second
            )
            await asyncio.sleep(seconds_until_midnight)
            self.reset_daily_pnl()
