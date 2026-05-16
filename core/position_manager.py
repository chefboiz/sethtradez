"""
Position manager — full lifecycle of a single BTC position: entry, stop loss,
trailing stop, time-based forced exit, paper mode support.
"""

import asyncio
import time
from typing import Optional, Callable, TYPE_CHECKING

import aiohttp

import config
from utils import logger

if TYPE_CHECKING:
    from db.supabase_client import SupabaseClient
    from bot.telegram_bot import TelegramBot
    from core.risk_manager import RiskManager

_ALLMIDS_TESTNET = "https://api.hyperliquid-testnet.xyz/info"
_ALLMIDS_MAINNET = "https://api.hyperliquid.xyz/info"


async def _get_btc_price(testnet: bool) -> Optional[float]:
    url = _ALLMIDS_TESTNET if testnet else _ALLMIDS_MAINNET
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"type": "allMids"}, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                return float(data.get("BTC", data.get("@107", 0)))
    except Exception as exc:
        logger.error(f"Price fetch error: {exc}")
        return None


class PositionManager:
    """
    Manages the full lifecycle of a single BTC position:
    - Entry via market order
    - Hard stop loss monitoring
    - Trailing stop activation and management
    - Time-based forced exit (TIME_STOP_SECS, default 20 min)
    - Paper mode support (log only, no real orders)
    """

    def __init__(
        self,
        db: Optional["SupabaseClient"] = None,
        telegram: Optional["TelegramBot"] = None,
        risk_manager: Optional["RiskManager"] = None,
        testnet: bool = True,
    ):
        self.open_position: Optional[dict] = None
        self._db = db
        self._telegram = telegram
        self._risk_manager = risk_manager
        self._testnet = testnet
        self._monitor_task: Optional[asyncio.Task] = None
        self._trade_id: Optional[str] = None

    async def enter_position(self, signal: dict) -> None:
        if self.open_position is not None:
            logger.info("Already in position, skipping signal")
            return

        current_price = signal["current_price"]
        direction = signal["direction"]
        qty = round(config.STAKE_USDC / current_price, 4)

        if config.PAPER_MODE:
            logger.trade(f"[PAPER] ENTER {direction} BTC qty={qty} @ ${current_price:,.2f}")
        else:
            try:
                from hyperliquid.exchange import Exchange
                from hyperliquid.utils import constants
                api_url = constants.TESTNET_API_URL if self._testnet else constants.MAINNET_API_URL
                exchange = Exchange(private_key=config.HL_PRIVATE_KEY, base_url=api_url)
                is_buy = direction == "LONG"
                order_result = exchange.market_open(coin="BTC", is_buy=is_buy, sz=qty)
                logger.trade( f"Order placed: {order_result}")
            except Exception as exc:
                logger.error(f"Order placement failed: {exc}")
                return

        if direction == "LONG":
            initial_stop = current_price - config.INITIAL_STOP_USD
        else:
            initial_stop = current_price + config.INITIAL_STOP_USD

        self.open_position = {
            "direction": direction,
            "entry_price": current_price,
            "entry_time": time.time(),
            "qty": qty,
            "stake_usdc": config.STAKE_USDC,
            "initial_stop": initial_stop,
            "trailing_active": False,
            "highest_price": current_price if direction == "LONG" else None,
            "lowest_price": current_price if direction == "SHORT" else None,
            "trailing_stop_level": None,
            "order_id": None,
            "signal": signal,
        }

        mode_tag = "[PAPER]" if config.PAPER_MODE else "[LIVE]"
        logger.trade( (
            f"{mode_tag} ENTERED {direction} BTC @ ${current_price:,.2f} | "
            f"qty={qty} | stop=${initial_stop:,.2f} | stake=${config.STAKE_USDC}"
        ))

        if self._telegram:
            await self._telegram.send_entry_alert(self.open_position, signal)

        if self._db:
            self._trade_id = await self._db.log_trade_entry({
                "direction": direction,
                "entry_price": current_price,
                "entry_time": self.open_position["entry_time"],
                "stake_usdc": config.STAKE_USDC,
                "initial_stop_usd": config.INITIAL_STOP_USD,
                "trail_distance_usd": config.TRAIL_DISTANCE_USD,
                "candle_open_price": signal.get("candle_open_price"),
                "candle_move_at_signal": signal.get("move_usd"),
                "paper_mode": config.PAPER_MODE,
            })

        loop = asyncio.get_event_loop()
        self._monitor_task = loop.create_task(self._monitor_position())

    async def _monitor_position(self) -> None:
        while self.open_position is not None:
            await asyncio.sleep(1)
            pos = self.open_position
            if pos is None:
                break

            current_price = await _get_btc_price(self._testnet)
            if current_price is None:
                continue

            direction = pos["direction"]
            qty = pos["qty"]
            entry_price = pos["entry_price"]
            initial_stop = pos["initial_stop"]
            elapsed = time.time() - pos["entry_time"]

            # Hard stop loss
            if direction == "LONG" and current_price <= initial_stop:
                await self.close_position("STOP_LOSS", current_price)
                return
            if direction == "SHORT" and current_price >= initial_stop:
                await self.close_position("STOP_LOSS", current_price)
                return

            # Trailing stop activation (price movement from entry, not PnL)
            if direction == "LONG":
                if not pos["trailing_active"] and (current_price - entry_price) >= config.TRAIL_ACTIVATE_USD:
                    pos["trailing_active"] = True
                    pos["highest_price"] = current_price
                    pos["trailing_stop_level"] = current_price - config.TRAIL_DISTANCE_USD
                    logger.info(f"Trail activated at ${current_price:,.2f}, stop=${pos['trailing_stop_level']:,.2f}")

            if direction == "SHORT":
                if not pos["trailing_active"] and (entry_price - current_price) >= config.TRAIL_ACTIVATE_USD:
                    pos["trailing_active"] = True
                    pos["lowest_price"] = current_price
                    pos["trailing_stop_level"] = current_price + config.TRAIL_DISTANCE_USD
                    logger.info(f"Trail activated at ${current_price:,.2f}, stop=${pos['trailing_stop_level']:,.2f}")

            # Trailing stop movement
            if pos["trailing_active"]:
                if direction == "LONG" and current_price > pos["highest_price"]:
                    pos["highest_price"] = current_price
                    pos["trailing_stop_level"] = current_price - config.TRAIL_DISTANCE_USD
                if direction == "SHORT" and current_price < pos["lowest_price"]:
                    pos["lowest_price"] = current_price
                    pos["trailing_stop_level"] = current_price + config.TRAIL_DISTANCE_USD

            # Trailing stop hit
            if pos["trailing_active"]:
                if direction == "LONG" and current_price <= pos["trailing_stop_level"]:
                    await self.close_position("TRAILING_STOP", current_price)
                    return
                if direction == "SHORT" and current_price >= pos["trailing_stop_level"]:
                    await self.close_position("TRAILING_STOP", current_price)
                    return

            # Time stop
            if elapsed >= config.TIME_STOP_SECS:
                await self.close_position("TIME_STOP", current_price)
                return

    async def close_position(self, reason: str, exit_price: Optional[float] = None) -> None:
        if self.open_position is None:
            return

        pos = self.open_position
        self.open_position = None

        if exit_price is None:
            exit_price = await _get_btc_price(self._testnet) or pos["entry_price"]

        direction = pos["direction"]
        qty = pos["qty"]
        entry_price = pos["entry_price"]

        if direction == "LONG":
            pnl = (exit_price - entry_price) * qty
        else:
            pnl = (entry_price - exit_price) * qty

        hold_secs = time.time() - pos["entry_time"]

        if config.PAPER_MODE:
            logger.trade( (
                f"[PAPER] CLOSE {direction} BTC @ ${exit_price:,.2f} | "
                f"reason={reason} | P&L=${pnl:+.4f} | held={hold_secs:.0f}s"
            ))
        else:
            try:
                from hyperliquid.exchange import Exchange
                from hyperliquid.utils import constants
                api_url = constants.TESTNET_API_URL if self._testnet else constants.MAINNET_API_URL
                exchange = Exchange(private_key=config.HL_PRIVATE_KEY, base_url=api_url)
                is_buy = direction == "SHORT"
                exchange.market_open(coin="BTC", is_buy=is_buy, sz=qty)
            except Exception as exc:
                logger.error(f"Close order failed: {exc}")

        trade_record = {
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_time": pos["entry_time"],
            "exit_time": time.time(),
            "stake_usdc": pos["stake_usdc"],
            "pnl_usdc": pnl,
            "exit_reason": reason,
            "trailing_activated": pos["trailing_active"],
            "highest_price": pos.get("highest_price"),
            "lowest_price": pos.get("lowest_price"),
            "hold_seconds": hold_secs,
            "signal": pos.get("signal", {}),
            "paper_mode": config.PAPER_MODE,
        }

        if self._db and self._trade_id:
            await self._db.update_trade_exit(self._trade_id, trade_record)

        if self._telegram:
            await self._telegram.send_exit_alert(trade_record)

        if self._risk_manager:
            self._risk_manager.record_trade_pnl(pnl)

        self._trade_id = None
        logger.trade( f"Position closed. P&L=${pnl:+.4f} reason={reason}")
