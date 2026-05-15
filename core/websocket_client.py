"""
Hyperliquid websocket client — subscribes to BTC 5-minute candles.
Auto-reconnects with exponential backoff on disconnect.
"""

import asyncio
import json
import time
from typing import Callable, Optional

import websockets

from utils import logger

TESTNET_WS = "wss://api.hyperliquid-testnet.xyz/ws"
MAINNET_WS = "wss://api.hyperliquid.xyz/ws"


def _parse_candle(raw: dict) -> Optional[dict]:
    """Convert raw Hyperliquid candle payload to clean dict."""
    try:
        data = raw.get("data", {})
        if isinstance(data, list):
            c = data[0] if data else None
        else:
            c = data
        if not c:
            return None
        return {
            "coin": c.get("s", "BTC"),
            "open": float(c.get("o", 0)),
            "high": float(c.get("h", 0)),
            "low": float(c.get("l", 0)),
            "close": float(c.get("c", 0)),
            "volume": float(c.get("v", 0)),
            "open_time": int(c.get("t", 0)),
            "is_closed": bool(c.get("T", False)),
        }
    except Exception as exc:
        logger.error(f"Failed to parse candle: {exc} | raw={raw}")
        return None


class WebsocketClient:
    """Connects to Hyperliquid WS and streams BTC 5m candle updates."""

    def __init__(self, on_candle_update: Callable[[dict], None], testnet: bool = True):
        self._on_candle_update = on_candle_update
        self._ws_url = TESTNET_WS if testnet else MAINNET_WS
        self._running = False

    async def start(self) -> None:
        self._running = True
        backoff = 1
        while self._running:
            try:
                logger.info(f"Connecting to Hyperliquid WS: {self._ws_url}")
                async with websockets.connect(self._ws_url, ping_interval=20, ping_timeout=30) as ws:
                    backoff = 1
                    logger.info("WS connected — subscribing to BTC 5m candles")
                    await ws.send(json.dumps({
                        "method": "subscribe",
                        "subscription": {"type": "candle", "coin": "BTC", "interval": "5m"},
                    }))
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw_msg)
                            if msg.get("channel") == "candle":
                                candle = _parse_candle(msg)
                                if candle:
                                    self._on_candle_update(candle)
                        except Exception as exc:
                            logger.error(f"WS message error: {exc}")
            except Exception as exc:
                if not self._running:
                    break
                logger.warning(f"WS disconnected: {exc} — reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def stop(self) -> None:
        self._running = False
        logger.info("WS client stopped")
