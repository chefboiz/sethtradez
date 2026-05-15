"""
SethTradez — Hyperliquid BTC perpetuals trading bot.
Runs alongside SethBetz on the same Railway VPS.

Startup sequence:
1. Load config and validate all required env vars present
2. Connect to Supabase — verify connection
3. Start Telegram bot (async)
4. Connect websocket to Hyperliquid (testnet or mainnet based on HL_TESTNET)
5. Start signal engine
6. Print startup banner to logs
7. Run forever — all modules handle their own reconnects
"""

import asyncio
import signal as _signal
import sys

import config
from core.websocket_client import WebsocketClient
from core.signal_engine import SignalEngine
from core.position_manager import PositionManager
from core.risk_manager import RiskManager
from db.supabase_client import SupabaseClient
from bot.telegram_bot import TelegramBot
from utils import logger


def _print_banner() -> None:
    mode_line = "🚨 PAPER MODE — No real orders placed" if config.PAPER_MODE else "🔴 LIVE TRADING — Real orders active"
    network = "TESTNET" if config.HL_TESTNET else "MAINNET"
    mode = "PAPER" if config.PAPER_MODE else "LIVE"
    print("╔══════════════════════════════════════════╗")
    print("║           SETHTRADEZ v1.0.0              ║")
    print("║      Hyperliquid BTC Perpetuals Bot      ║")
    print("╠══════════════════════════════════════════╣")
    print(f"║  Mode:      {mode:<29}║")
    print(f"║  Network:   {network:<29}║")
    print(f"║  Coin:      {'BTC':<29}║")
    print(f"║  Stake:     ${config.STAKE_USDC:<28}║")
    print(f"║  Min move:  ${config.MIN_CANDLE_MOVE_USD:<28}║")
    print(f"║  Init stop: ${config.INITIAL_STOP_USD:<28}║")
    print(f"║  Trail:     ${config.TRAIL_DISTANCE_USD:<28}║")
    print("╠══════════════════════════════════════════╣")
    print(f"║  {mode_line:<40}  ║")
    print("╚══════════════════════════════════════════╝")


async def main() -> None:
    _print_banner()

    # Supabase
    db = SupabaseClient()
    db.connect()

    # Risk manager
    risk_manager = RiskManager()

    # Position manager (wired up after telegram is created)
    position_manager = PositionManager(
        db=db,
        testnet=config.HL_TESTNET,
    )

    # Telegram bot
    telegram = TelegramBot(
        position_manager=position_manager,
        risk_manager=risk_manager,
        db=db,
    )
    telegram_ok = telegram.setup()
    position_manager._telegram = telegram
    position_manager._risk_manager = risk_manager

    # Wire limit hit alert
    async def _on_limit_hit():
        await telegram.send_limit_alert(risk_manager.daily_pnl, config.DAILY_LOSS_LIMIT_USD)

    def _limit_hit_sync():
        asyncio.get_event_loop().create_task(_on_limit_hit())

    risk_manager._on_limit_hit = _limit_hit_sync

    # Signal engine
    def _on_signal(sig: dict) -> None:
        ok, reason = risk_manager.check_ok()
        if not ok:
            logger.info(f"Signal skipped — {reason}")
            return
        if position_manager.open_position is not None:
            logger.info("Signal skipped — position already open")
            return
        if config.FADE_MODE:
            original = sig["direction"]
            sig = {**sig, "direction": "SHORT" if original == "LONG" else "LONG", "faded_from": original}
            logger.signal(f"FADE MODE — signal was {original}, trading {sig['direction']}")
        asyncio.get_event_loop().create_task(position_manager.enter_position(sig))

    signal_engine = SignalEngine(on_signal=_on_signal, testnet=config.HL_TESTNET)

    # Websocket client
    ws_client = WebsocketClient(
        on_candle_update=signal_engine.process_candle,
        testnet=config.HL_TESTNET,
    )

    # Graceful shutdown
    loop = asyncio.get_event_loop()

    async def _shutdown():
        logger.info("Shutdown signal received")
        ws_client.stop()
        if position_manager.open_position is not None:
            logger.info("Closing open position before shutdown...")
            await position_manager.close_position("MANUAL")
        if telegram_ok:
            await telegram.stop()
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        loop.stop()

    for sig in (_signal.SIGTERM, _signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_shutdown()))
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler for all signals

    logger.info("SethTradez started — waiting for candles...")

    tasks = [
        asyncio.create_task(ws_client.start()),
        asyncio.create_task(risk_manager.start_midnight_reset()),
    ]
    if telegram_ok:
        tasks.append(asyncio.create_task(telegram.start()))

    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
