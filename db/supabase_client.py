"""
Supabase client — logs trades to hyperbetz_trades table.

Run this SQL in your Supabase dashboard to create the table:

    CREATE TABLE IF NOT EXISTS hyperbetz_trades (
        id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
        coin text NOT NULL DEFAULT 'BTC',
        direction text NOT NULL,
        entry_price numeric(12,2) NOT NULL,
        exit_price numeric(12,2),
        entry_time timestamptz NOT NULL,
        exit_time timestamptz,
        stake_usdc numeric(10,2) NOT NULL,
        pnl_usdc numeric(10,4),
        exit_reason text,
        trail_distance_usd numeric(8,2),
        initial_stop_usd numeric(8,2),
        candle_move_at_signal numeric(8,2),
        candle_open_price numeric(12,2),
        trailing_activated bool DEFAULT false,
        paper_mode bool DEFAULT false,
        created_at timestamptz DEFAULT now()
    );
"""

from datetime import datetime, timezone
from typing import Optional

import config
from utils import logger


class SupabaseClient:
    """Handles all database operations for SethTradez trade logging."""

    def __init__(self):
        self._client = None
        self._connected = False

    def connect(self) -> bool:
        if not config.SUPABASE_URL or not config.SUPABASE_KEY:
            logger.warning("Supabase credentials not set — DB logging disabled")
            return False
        try:
            from supabase import create_client
            self._client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
            self._connected = True
            logger.info("Supabase connected")
            return True
        except Exception as exc:
            logger.error(f"Supabase connection failed: {exc}")
            return False

    async def log_trade_entry(self, trade: dict) -> Optional[str]:
        if not self._connected:
            return None
        try:
            entry_dt = datetime.fromtimestamp(trade["entry_time"], tz=timezone.utc).isoformat()
            row = {
                "coin": "BTC",
                "direction": trade["direction"],
                "entry_price": trade["entry_price"],
                "entry_time": entry_dt,
                "stake_usdc": trade["stake_usdc"],
                "initial_stop_usd": trade.get("initial_stop_usd"),
                "trail_distance_usd": trade.get("trail_distance_usd"),
                "candle_open_price": trade.get("candle_open_price"),
                "candle_move_at_signal": trade.get("candle_move_at_signal"),
                "paper_mode": trade.get("paper_mode", True),
            }
            result = self._client.table("hyperbetz_trades").insert(row).execute()
            trade_id = result.data[0]["id"] if result.data else None
            logger.info(f"Trade entry logged: {trade_id}")
            return trade_id
        except Exception as exc:
            logger.error(f"Failed to log trade entry: {exc}")
            return None

    async def update_trade_exit(self, trade_id: str, trade: dict) -> None:
        if not self._connected or not trade_id:
            return
        try:
            exit_dt = datetime.fromtimestamp(trade["exit_time"], tz=timezone.utc).isoformat()
            updates = {
                "exit_price": trade["exit_price"],
                "exit_time": exit_dt,
                "pnl_usdc": trade["pnl_usdc"],
                "exit_reason": trade["exit_reason"],
                "trailing_activated": trade.get("trailing_activated", False),
            }
            self._client.table("hyperbetz_trades").update(updates).eq("id", trade_id).execute()
            logger.info(f"Trade exit logged: {trade_id} P&L=${trade['pnl_usdc']:+.4f}")
        except Exception as exc:
            logger.error(f"Failed to update trade exit: {exc}")

    async def get_recent_trades(self, limit: int = 10) -> list:
        if not self._connected:
            return []
        try:
            result = (
                self._client.table("hyperbetz_trades")
                .select("*")
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return result.data or []
        except Exception as exc:
            logger.error(f"Failed to fetch recent trades: {exc}")
            return []

    async def get_daily_summary(self, date: str) -> dict:
        if not self._connected:
            return {}
        try:
            result = (
                self._client.table("hyperbetz_trades")
                .select("*")
                .gte("entry_time", f"{date}T00:00:00+00:00")
                .lt("entry_time", f"{date}T23:59:59+00:00")
                .not_.is_("exit_time", "null")
                .execute()
            )
            trades = result.data or []
            total_pnl = sum(t.get("pnl_usdc", 0) or 0 for t in trades)
            winners = [t for t in trades if (t.get("pnl_usdc") or 0) > 0]
            losers = [t for t in trades if (t.get("pnl_usdc") or 0) <= 0]
            best = max(trades, key=lambda t: t.get("pnl_usdc") or 0, default=None)
            worst = min(trades, key=lambda t: t.get("pnl_usdc") or 0, default=None)
            return {
                "date": date,
                "count": len(trades),
                "winners": len(winners),
                "losers": len(losers),
                "total_pnl": total_pnl,
                "win_rate": round(len(winners) / len(trades) * 100, 1) if trades else 0,
                "best": best,
                "worst": worst,
            }
        except Exception as exc:
            logger.error(f"Failed to get daily summary: {exc}")
            return {}
