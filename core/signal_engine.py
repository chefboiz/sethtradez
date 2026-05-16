"""
Signal engine — detects a $MIN_CANDLE_MOVE_USD move at exactly 2:30 into each BTC 5m candle.
"""

import asyncio
import time
from typing import Callable, Optional

import aiohttp

import config
from utils import logger

_ALLMIDS_URL_TESTNET = "https://api.hyperliquid-testnet.xyz/info"
_ALLMIDS_URL_MAINNET = "https://api.hyperliquid.xyz/info"


async def _fetch_btc_price(testnet: bool) -> Optional[float]:
    url = _ALLMIDS_URL_TESTNET if testnet else _ALLMIDS_URL_MAINNET
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"type": "allMids"}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return float(data.get("BTC", data.get("@107", 0)))
    except Exception as exc:
        logger.error(f"Failed to fetch BTC price: {exc}")
        return None


class SignalEngine:
    """
    Monitors BTC 5-minute candles.
    At exactly 2 minutes 30 seconds into each candle:
      - If price moved $MIN_CANDLE_MOVE_USD+ UP   → emit LONG signal
      - If price moved $MIN_CANDLE_MOVE_USD+ DOWN → emit SHORT signal
      - Otherwise → no signal, wait for next candle
    """

    def __init__(self, on_signal: Callable[[dict], None], testnet: bool = True):
        self._on_signal = on_signal
        self._testnet = testnet
        self._candle_open_price: Optional[float] = None
        self._candle_open_time: Optional[int] = None
        self._pending_task: Optional[asyncio.Task] = None
        self._seen_candle_times: set = set()

    def process_candle(self, candle: dict) -> None:
        open_time = candle["open_time"]
        if open_time == self._candle_open_time:
            return

        # New candle started
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
            logger.debug(f"Cancelled pending 2:30 timer for candle {self._candle_open_time}")

        self._candle_open_price = candle["open"]
        self._candle_open_time = open_time
        logger.info(f"New candle: BTC open=${self._candle_open_price:,.2f} at t={open_time}")

        loop = asyncio.get_event_loop()
        self._pending_task = loop.create_task(self._check_at_230(open_time, self._candle_open_price))

    async def _check_at_230(self, candle_open_time: int, candle_open_price: float) -> None:
        await asyncio.sleep(150)

        current_price = await _fetch_btc_price(self._testnet)
        if current_price is None:
            logger.error("Could not fetch price at 2:30 mark — skipping signal check")
            return

        move = current_price - candle_open_price
        min_move = config.MIN_CANDLE_MOVE_USD

        logger.info(f"2:30 check: open=${candle_open_price:,.2f} current=${current_price:,.2f} move=${move:+.2f}")

        if move >= min_move:
            direction = "LONG"
        elif move <= -min_move:
            direction = "SHORT"
        else:
            logger.info(f"No signal — move was ${move:+.2f} (threshold ±${min_move})")
            return

        if abs(move) > config.MAX_CANDLE_MOVE_USD:
            logger.info(f"Signal skipped — move ${abs(move):.2f} exceeds MAX_CANDLE_MOVE ${config.MAX_CANDLE_MOVE_USD} (liquidation event)")
            return

        sig = {
            "direction": direction,
            "coin": "BTC",
            "candle_open_price": candle_open_price,
            "current_price": current_price,
            "move_usd": abs(move),
            "signal_time": time.time(),
            "candle_open_time": candle_open_time,
        }
        logger.signal(f"{direction} signal — BTC moved ${abs(move):.2f} at 2:30 mark")
        self._on_signal(sig)

    def on_disconnect(self) -> None:
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()
            logger.warning("WS disconnected — cancelled pending 2:30 timer")
        self._candle_open_price = None
        self._candle_open_time = None
