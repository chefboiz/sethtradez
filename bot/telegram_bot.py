"""
Telegram bot for SethTradez — commands and proactive trade alerts.
Uses python-telegram-bot v20 (async). Separate bot from SethBetz.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

import config
from utils import logger

if TYPE_CHECKING:
    from core.position_manager import PositionManager
    from core.risk_manager import RiskManager
    from db.supabase_client import SupabaseClient


def _fmt_hold(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


class TelegramBot:
    """Handles all Telegram interaction for SethTradez."""

    def __init__(
        self,
        position_manager: Optional["PositionManager"] = None,
        risk_manager: Optional["RiskManager"] = None,
        db: Optional["SupabaseClient"] = None,
    ):
        self._position_manager = position_manager
        self._risk_manager = risk_manager
        self._db = db
        self._app = None
        self._pending_live_confirm = False
        self._stop_event: Optional[asyncio.Event] = None

    def setup(self) -> bool:
        if not config.TELEGRAM_TOKEN:
            logger.warning("TELEGRAM_TOKEN_HYPERBETZ not set — Telegram disabled")
            return False
        try:
            from telegram.ext import Application, CommandHandler
            self._app = Application.builder().token(config.TELEGRAM_TOKEN).build()
            self._register_handlers()
            logger.info("Telegram bot configured")
            return True
        except Exception as exc:
            logger.error(f"Telegram setup failed: {exc}")
            return False

    def _register_handlers(self) -> None:
        from telegram.ext import CommandHandler
        handlers = [
            ("status", self._cmd_status),
            ("pause", self._cmd_pause),
            ("resume", self._cmd_resume),
            ("stake", self._cmd_stake),
            ("stop", self._cmd_stop),
            ("trail", self._cmd_trail),
            ("trail_activate", self._cmd_trail_activate),
            ("move", self._cmd_move),
            ("maxmove", self._cmd_maxmove),
            ("close", self._cmd_close),
            ("daily", self._cmd_daily),
            ("daily_limit", self._cmd_daily_limit),
            ("timestop", self._cmd_timestop),
            ("paper", self._cmd_paper),
            ("confirm_live", self._cmd_confirm_live),
            ("fade", self._cmd_fade),
            ("help", self._cmd_help),
        ]
        for name, handler in handlers:
            self._app.add_handler(CommandHandler(name, handler))

    async def start(self) -> None:
        if not self._app:
            return
        self._stop_event = asyncio.Event()
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot polling started")
        await self._stop_event.wait()  # keep task alive so PTB's internal tasks run

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        if self._stop_event:
            self._stop_event.set()

    async def send(self, text: str) -> None:
        if not self._app or not config.TELEGRAM_CHAT_ID:
            return
        try:
            await self._app.bot.send_message(chat_id=config.TELEGRAM_CHAT_ID, text=text, parse_mode="HTML")
        except Exception as exc:
            logger.error(f"Telegram send failed: {exc}")

    async def send_entry_alert(self, pos: dict, signal: dict) -> None:
        mode = "[PAPER]" if config.PAPER_MODE else "[LIVE]"
        direction = pos["direction"]
        arrow = "↑" if direction == "LONG" else "↓"
        faded_from = signal.get("faded_from")
        fade_line = f"🔄 FADE MODE — Signal: {faded_from} → Trading: {direction}\n" if faded_from else ""
        text = (
            f"🟢 SETHTRADEZ ENTRY {mode}\n\n"
            f"{fade_line}"
            f"📈 {direction} BTC\n"
            f"💵 Entry: ${pos['entry_price']:,.2f}\n"
            f"📊 Move at signal: ${signal.get('move_usd', 0):.0f} {arrow}\n"
            f"🕐 Candle open: ${signal.get('candle_open_price', 0):,.2f}\n"
            f"💰 Stake: ${pos['stake_usdc']:.0f} USDC\n"
            f"🛑 Stop loss: ${pos['initial_stop']:,.2f}\n\n"
            f"Trailing stop activates at +${config.TRAIL_ACTIVATE_USD:.0f} price move from entry"
        )
        await self.send(text)

    async def send_exit_alert(self, trade: dict) -> None:
        rm = self._risk_manager
        daily_pnl = rm.daily_pnl if rm else 0.0
        mode = "[PAPER]" if config.PAPER_MODE else "[LIVE]"
        direction = trade["direction"]
        pnl = trade["pnl_usdc"]
        pnl_pct = (pnl / trade["stake_usdc"] * 100) if trade.get("stake_usdc") else 0
        hold = _fmt_hold(trade.get("hold_seconds", 0))
        trail_yes = "YES" if trade.get("trailing_activated") else "NO"
        highest = trade.get("highest_price") or trade.get("lowest_price")
        highest_str = f"${highest:,.2f}" if highest else "n/a"
        pnl_sign = "+" if pnl >= 0 else ""
        text = (
            f"🔴 SETHTRADEZ EXIT {mode}\n\n"
            f"📈 {direction} BTC closed\n"
            f"💵 Entry: ${trade['entry_price']:,.2f} → Exit: ${trade['exit_price']:,.2f}\n"
            f"📤 Reason: {trade['exit_reason']}\n"
            f"⏱️ Hold time: {hold}\n"
            f"💰 P&L: {pnl_sign}${pnl:.2f} ({pnl_sign}{pnl_pct:.1f}%)\n"
            f"🛑 Trail activated: {trail_yes} (highest: {highest_str})\n\n"
            f"📊 Today P&L: ${daily_pnl:+.2f}"
        )
        await self.send(text)

    async def send_limit_alert(self, daily_pnl: float, limit: float) -> None:
        text = (
            "🚨 DAILY LOSS LIMIT HIT\n\n"
            f"❌ Loss today: ${daily_pnl:.2f}\n"
            f"⚙️ Limit: ${limit:.2f}\n"
            "⏸️ Trading AUTO-PAUSED\n\n"
            "Use /resume to restart trading tomorrow."
        )
        await self.send(text)

    # ── Commands ────────────────────────────────────────────────────────────

    async def _cmd_help(self, update, context) -> None:
        text = (
            "SETHTRADEZ — Commands\n\n"
            "/status — current position + daily P&L\n"
            "/daily — today's trade summary\n"
            "/pause — pause trading\n"
            "/resume — resume trading\n"
            "/close — emergency close open position\n\n"
            "Settings:\n"
            "/stake [amount] — USDC per trade\n"
            "/stop [amount] — initial stop loss ($)\n"
            "/trail [amount] — trailing stop distance ($)\n"
            "/trail_activate [amount] — price move from entry to activate trail ($)\n"
            "/move [amount] — min candle move to signal ($)\n"
            "/maxmove [amount] — max candle move before skipping signal ($)\n"
            "/daily_limit [amount] — daily loss limit ($)\n"
            "/timestop [minutes] — max time in trade before forced exit\n\n"
            "Mode:\n"
            "/paper on — enable paper mode\n"
            "/paper off — disable paper mode (prompts confirm)\n"
            "/confirm_live — confirm switch to live trading\n"
            "/fade on — fade signals (trade the reversal)\n"
            "/fade off — trade in signal direction (default)"
        )
        await update.message.reply_text(text)

    async def _cmd_status(self, update, context) -> None:
        rm = self._risk_manager
        pm = self._position_manager
        mode = "PAPER" if (rm.paper_mode if rm else config.PAPER_MODE) else "LIVE"
        trading = "PAUSED ⏸️" if (rm.is_paused if rm else False) else "ACTIVE ✅"
        daily_pnl = rm.daily_pnl if rm else 0.0

        if pm and pm.open_position:
            pos = pm.open_position
            current_price = pos["entry_price"]
            direction = pos["direction"]
            pnl = (current_price - pos["entry_price"]) * pos["qty"] if direction == "LONG" else (pos["entry_price"] - current_price) * pos["qty"]
            elapsed_secs = time.time() - pos["entry_time"]
            elapsed = _fmt_hold(elapsed_secs)
            remaining_secs = max(0.0, config.TIME_STOP_SECS - elapsed_secs)
            remaining = _fmt_hold(remaining_secs)
            trail_str = f"${pos['trailing_stop_level']:,.2f} (active)" if pos["trailing_active"] and pos["trailing_stop_level"] else "not active"
            pos_text = (
                f"\n📈 Open Position: {direction} BTC\n"
                f"  Entry: ${pos['entry_price']:,.2f}\n"
                f"  Current: ${current_price:,.2f}\n"
                f"  P&L: ${pnl:+.2f}\n"
                f"  Stop: ${pos['initial_stop']:,.2f} (hard)\n"
                f"  Trail: {trail_str}\n"
                f"  Time in trade: {elapsed}\n"
                f"  Time remaining: {remaining}"
            )
        else:
            pos_text = "\n📭 No open position"

        fade_str = "ON 🔄" if config.FADE_MODE else "OFF"
        text = (
            "📊 SETHTRADEZ STATUS\n\n"
            f"🔄 Mode: {mode}\n"
            f"⏸️ Trading: {trading}\n"
            f"🔄 Fade Mode: {fade_str}"
            f"{pos_text}\n\n"
            f"💰 Today's P&L: ${daily_pnl:+.2f}\n"
            f"⚙️ Settings:\n"
            f"  Stake: ${config.STAKE_USDC:.0f} USDC\n"
            f"  Min move: ${config.MIN_CANDLE_MOVE_USD:.0f} / Max: ${config.MAX_CANDLE_MOVE_USD:.0f}\n"
            f"  Init stop: ${config.INITIAL_STOP_USD:.0f}\n"
            f"  Trail dist: ${config.TRAIL_DISTANCE_USD:.0f} | Activate: ${config.TRAIL_ACTIVATE_USD:.0f}\n"
            f"  Time stop: {config.TIME_STOP_SECS // 60} mins\n"
            f"  Daily limit: ${config.DAILY_LOSS_LIMIT_USD:.0f}"
        )
        await update.message.reply_text(text)

    async def _cmd_pause(self, update, context) -> None:
        if self._risk_manager:
            self._risk_manager.pause()
        await update.message.reply_text("⏸️ Trading PAUSED.")

    async def _cmd_resume(self, update, context) -> None:
        if self._risk_manager:
            self._risk_manager.resume()
        await update.message.reply_text("▶️ Trading RESUMED.")

    async def _cmd_stake(self, update, context) -> None:
        try:
            amount = float(context.args[0])
            config.STAKE_USDC = amount
            config.save_runtime_config()
            await update.message.reply_text(f"✅ Stake updated to ${amount} USDC")
        except (IndexError, ValueError):
            await update.message.reply_text("Usage: /stake [amount]")

    async def _cmd_stop(self, update, context) -> None:
        try:
            amount = float(context.args[0])
            config.INITIAL_STOP_USD = amount
            config.save_runtime_config()
            await update.message.reply_text(f"✅ Initial stop updated to ${amount}")
        except (IndexError, ValueError):
            await update.message.reply_text("Usage: /stop [amount]")

    async def _cmd_trail(self, update, context) -> None:
        try:
            amount = float(context.args[0])
            config.TRAIL_DISTANCE_USD = amount
            config.save_runtime_config()
            await update.message.reply_text(f"✅ Trailing distance updated to ${amount}")
        except (IndexError, ValueError):
            await update.message.reply_text("Usage: /trail [amount]")

    async def _cmd_trail_activate(self, update, context) -> None:
        try:
            amount = float(context.args[0])
            config.TRAIL_ACTIVATE_USD = amount
            config.save_runtime_config()
            await update.message.reply_text(f"✅ Trail activation threshold updated to ${amount} price move from entry")
        except (IndexError, ValueError):
            await update.message.reply_text("Usage: /trail_activate [amount]")

    async def _cmd_move(self, update, context) -> None:
        try:
            amount = float(context.args[0])
            config.MIN_CANDLE_MOVE_USD = amount
            config.save_runtime_config()
            await update.message.reply_text(f"✅ Minimum candle move updated to ${amount}")
        except (IndexError, ValueError):
            await update.message.reply_text("Usage: /move [amount]")

    async def _cmd_maxmove(self, update, context) -> None:
        try:
            amount = float(context.args[0])
            config.MAX_CANDLE_MOVE_USD = amount
            config.save_runtime_config()
            await update.message.reply_text(f"✅ Max candle move updated to ${amount} (signals above this are skipped)")
        except (IndexError, ValueError):
            await update.message.reply_text("Usage: /maxmove [amount]")

    async def _cmd_close(self, update, context) -> None:
        pm = self._position_manager
        if not pm or pm.open_position is None:
            await update.message.reply_text("No open position to close.")
            return
        await update.message.reply_text("⚡ Closing position...")
        await pm.close_position("MANUAL")

    async def _cmd_daily(self, update, context) -> None:
        if not self._db:
            await update.message.reply_text("Database not connected.")
            return
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        summary = await self._db.get_daily_summary(today)
        if not summary or summary.get("count", 0) == 0:
            await update.message.reply_text(f"📅 No trades today ({today}).")
            return
        best = summary.get("best")
        worst = summary.get("worst")
        best_str = f"+${best['pnl_usdc']:.2f} ({best['direction']}, {best['exit_reason']})" if best else "n/a"
        worst_str = f"${worst['pnl_usdc']:.2f} ({worst['direction']}, {worst['exit_reason']})" if worst else "n/a"
        text = (
            f"📅 DAILY SUMMARY — {today}\n\n"
            f"🔢 Trades: {summary['count']}\n"
            f"✅ Winners: {summary['winners']}\n"
            f"❌ Losers: {summary['losers']}\n"
            f"💰 P&L: ${summary['total_pnl']:+.2f}\n"
            f"📊 Win rate: {summary['win_rate']:.0f}%\n"
            f"🏆 Best trade: {best_str}\n"
            f"💀 Worst trade: {worst_str}"
        )
        await update.message.reply_text(text)

    async def _cmd_daily_limit(self, update, context) -> None:
        try:
            amount = float(context.args[0])
            config.DAILY_LOSS_LIMIT_USD = amount
            if self._risk_manager:
                self._risk_manager.set_daily_limit(amount)
            config.save_runtime_config()
            await update.message.reply_text(f"✅ Daily loss limit set to ${amount}")
        except (IndexError, ValueError):
            await update.message.reply_text("Usage: /daily_limit [amount]")

    async def _cmd_timestop(self, update, context) -> None:
        try:
            minutes = float(context.args[0])
            config.TIME_STOP_SECS = int(minutes * 60)
            config.save_runtime_config()
            await update.message.reply_text(f"✅ Time stop updated to {minutes:.0f} mins")
        except (IndexError, ValueError):
            await update.message.reply_text("Usage: /timestop [minutes]")

    async def _cmd_paper(self, update, context) -> None:
        try:
            arg = context.args[0].lower()
        except IndexError:
            await update.message.reply_text("Usage: /paper on | /paper off")
            return

        if arg == "on":
            config.PAPER_MODE = True
            if self._risk_manager:
                self._risk_manager.toggle_paper(True)
            config.save_runtime_config()
            await update.message.reply_text(
                "🧻 PAPER MODE ON\nAll trades will be logged but NOT executed.\nUse /paper off to go live."
            )
        elif arg == "off":
            self._pending_live_confirm = True
            await update.message.reply_text(
                "🚨 PAPER MODE OFF — LIVE TRADING ENABLED\n"
                "Real orders will now be placed on Hyperliquid.\n"
                "Are you sure? Reply /confirm_live to confirm."
            )
        else:
            await update.message.reply_text("Usage: /paper on | /paper off")

    async def _cmd_confirm_live(self, update, context) -> None:
        if not self._pending_live_confirm:
            await update.message.reply_text("Send /paper off first to initiate the switch to live mode.")
            return
        self._pending_live_confirm = False
        config.PAPER_MODE = False
        if self._risk_manager:
            self._risk_manager.toggle_paper(False)
        config.save_runtime_config()
        await update.message.reply_text(
            "🚨 LIVE TRADING ACTIVE\nReal orders will be placed on Hyperliquid.\nUse /paper on to return to paper mode."
        )

    async def _cmd_fade(self, update, context) -> None:
        try:
            arg = context.args[0].lower()
        except IndexError:
            status = "ON" if config.FADE_MODE else "OFF"
            await update.message.reply_text(f"🔄 Fade Mode is currently {status}.\nUsage: /fade on | /fade off")
            return

        if arg == "on":
            config.FADE_MODE = True
            config.save_runtime_config()
            await update.message.reply_text(
                "🔄 FADE MODE ON\n"
                "LONG signals will trade SHORT.\n"
                "SHORT signals will trade LONG."
            )
        elif arg == "off":
            config.FADE_MODE = False
            config.save_runtime_config()
            await update.message.reply_text("🔄 FADE MODE OFF\nTrading in signal direction (normal mode).")
        else:
            await update.message.reply_text("Usage: /fade on | /fade off")
