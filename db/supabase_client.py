"""
PostgreSQL client — logs trades to hyperbetz_trades table.
Shares the same Railway Postgres instance as SethBetz.

Run this SQL once in your database (via psql or Railway's query console):

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

import asyncpg

import config
from utils import logger


class SupabaseClient:
    """Handles all database operations for SethTradez trade logging."""

    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
        self._connected = False

    def connect(self) -> bool:
        """Synchronous connect check — actual pool is created async in async_connect()."""
        if not config.DATABASE_URL:
            logger.warning("DATABASE_URL not set — DB logging disabled")
            return False
        logger.info("DATABASE_URL configured — will connect on first async operation")
        self._connected = True
        return True

    async def _get_pool(self) -> Optional[asyncpg.Pool]:
        if not self._connected:
            return None
        if self._pool is None:
            try:
                dsn = config.DATABASE_URL
                # Railway Postgres requires SSL; asyncpg needs ssl='require' for external connections
                self._pool = await asyncpg.create_pool(dsn, ssl="require", min_size=1, max_size=5)
                logger.info("PostgreSQL pool connected")
            except Exception as exc:
                logger.error(f"PostgreSQL connection failed: {exc}")
                self._pool = None
        return self._pool

    async def log_trade_entry(self, trade: dict) -> Optional[str]:
        pool = await self._get_pool()
        if not pool:
            return None
        try:
            entry_dt = datetime.fromtimestamp(trade["entry_time"], tz=timezone.utc)
            row = await pool.fetchrow(
                """
                INSERT INTO hyperbetz_trades
                    (coin, direction, entry_price, entry_time, stake_usdc,
                     initial_stop_usd, trail_distance_usd, candle_open_price,
                     candle_move_at_signal, paper_mode)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                RETURNING id
                """,
                "BTC",
                trade["direction"],
                trade["entry_price"],
                entry_dt,
                trade["stake_usdc"],
                trade.get("initial_stop_usd"),
                trade.get("trail_distance_usd"),
                trade.get("candle_open_price"),
                trade.get("candle_move_at_signal"),
                trade.get("paper_mode", True),
            )
            trade_id = str(row["id"])
            logger.info(f"Trade entry logged: {trade_id}")
            return trade_id
        except Exception as exc:
            logger.error(f"Failed to log trade entry: {exc}")
            return None

    async def update_trade_exit(self, trade_id: str, trade: dict) -> None:
        pool = await self._get_pool()
        if not pool or not trade_id:
            return
        try:
            exit_dt = datetime.fromtimestamp(trade["exit_time"], tz=timezone.utc)
            await pool.execute(
                """
                UPDATE hyperbetz_trades
                SET exit_price=$1, exit_time=$2, pnl_usdc=$3,
                    exit_reason=$4, trailing_activated=$5
                WHERE id=$6::uuid
                """,
                trade["exit_price"],
                exit_dt,
                trade["pnl_usdc"],
                trade["exit_reason"],
                trade.get("trailing_activated", False),
                trade_id,
            )
            logger.info(f"Trade exit logged: {trade_id} P&L=${trade['pnl_usdc']:+.4f}")
        except Exception as exc:
            logger.error(f"Failed to update trade exit: {exc}")

    async def get_recent_trades(self, limit: int = 10) -> list:
        pool = await self._get_pool()
        if not pool:
            return []
        try:
            rows = await pool.fetch(
                "SELECT * FROM hyperbetz_trades ORDER BY created_at DESC LIMIT $1", limit
            )
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.error(f"Failed to fetch recent trades: {exc}")
            return []

    async def get_daily_summary(self, date: str) -> dict:
        pool = await self._get_pool()
        if not pool:
            return {}
        try:
            rows = await pool.fetch(
                """
                SELECT * FROM hyperbetz_trades
                WHERE entry_time >= $1::timestamptz
                  AND entry_time <  $2::timestamptz
                  AND exit_time IS NOT NULL
                """,
                f"{date}T00:00:00+00:00",
                f"{date}T23:59:59+00:00",
            )
            trades = [dict(r) for r in rows]
            total_pnl = sum(t.get("pnl_usdc") or 0 for t in trades)
            winners = [t for t in trades if (t.get("pnl_usdc") or 0) > 0]
            losers  = [t for t in trades if (t.get("pnl_usdc") or 0) <= 0]
            best  = max(trades, key=lambda t: t.get("pnl_usdc") or 0, default=None)
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
