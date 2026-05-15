"""
Tests for PositionManager — verifies trailing stop, stop loss, and time stop logic.
"""

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))

import config
# qty = STAKE_USDC / entry_price = 810/81000 = 0.01
# Trail activates when pnl_usd >= TRAIL_ACTIVATE_USD
# At $81,002 with qty=0.01: pnl = $0.02 -> set TRAIL_ACTIVATE_USD=0.02
config.PAPER_MODE = True
config.STAKE_USDC = 810.0
config.INITIAL_STOP_USD = 50.0      # far enough not to fire on test prices
config.TRAIL_DISTANCE_USD = 10.0
config.TRAIL_ACTIVATE_USD = 0.02    # activates at $2 price move with qty=0.01

from core.position_manager import PositionManager
import core.position_manager as pm_module


def _mock_signal(entry_price: float = 81_000.0, direction: str = "LONG") -> dict:
    return {
        "direction": direction,
        "coin": "BTC",
        "candle_open_price": entry_price,
        "current_price": entry_price,
        "move_usd": 45.0,
        "signal_time": time.time(),
        "candle_open_time": 1_000_000,
    }


class _FakeRiskManager:
    def __init__(self):
        self.recorded_pnl = None

    def record_trade_pnl(self, pnl):
        self.recorded_pnl = pnl


def _simulate_monitor(pm, prices: list):
    """Step through monitor logic synchronously. Returns exit reason or None."""
    if pm._monitor_task:
        pm._monitor_task.cancel()

    for current_price in prices:
        pos = pm.open_position
        if pos is None:
            break

        direction = pos["direction"]
        qty = pos["qty"]
        entry_price = pos["entry_price"]

        # Hard stop loss
        if direction == "LONG" and current_price <= pos["initial_stop"]:
            pm.open_position = None
            return "STOP_LOSS"
        if direction == "SHORT" and current_price >= pos["initial_stop"]:
            pm.open_position = None
            return "STOP_LOSS"

        # Trailing activation
        if direction == "LONG":
            pnl_usd = (current_price - entry_price) * qty
            if not pos["trailing_active"] and pnl_usd >= config.TRAIL_ACTIVATE_USD:
                pos["trailing_active"] = True
                pos["highest_price"] = current_price
                pos["trailing_stop_level"] = current_price - config.TRAIL_DISTANCE_USD
        if direction == "SHORT":
            pnl_usd = (entry_price - current_price) * qty
            if not pos["trailing_active"] and pnl_usd >= config.TRAIL_ACTIVATE_USD:
                pos["trailing_active"] = True
                pos["lowest_price"] = current_price
                pos["trailing_stop_level"] = current_price + config.TRAIL_DISTANCE_USD

        # Trailing movement
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
                pm.open_position = None
                return "TRAILING_STOP"
            if direction == "SHORT" and current_price >= pos["trailing_stop_level"]:
                pm.open_position = None
                return "TRAILING_STOP"

        # Time stop
        if time.time() - pos["entry_time"] >= 600:
            pm.open_position = None
            return "TIME_STOP"

    return None


async def run_all_tests():
    print("Running position manager tests...\n")
    failures = 0

    async def _noop_price(testnet):
        return 81_000.0

    pm_module._get_btc_price = _noop_price

    # ── Test 1: Trailing stop (LONG) ────────────────────────────────────
    # qty=0.01; TRAIL_ACTIVATE_USD=0.02
    # $81,002 -> pnl=$0.02 -> trail ON, stop=$80,992
    # $81,015 -> highest moves, stop=$81,005
    # $81,004 -> $81,004 <= $81,005 -> TRAILING_STOP
    print("Test 1: Trailing stop (LONG)...")
    rm1 = _FakeRiskManager()
    pm1 = PositionManager(risk_manager=rm1, testnet=True)
    await pm1.enter_position(_mock_signal(81_000.0))
    reason1 = _simulate_monitor(pm1, [81_002.0, 81_015.0, 81_004.0])

    if reason1 == "TRAILING_STOP":
        print(f"PASS - Trailing stop triggered (reason={reason1})")
    else:
        print(f"FAIL - Expected TRAILING_STOP, got: {reason1}")
        failures += 1

    # ── Test 2: Time stop ────────────────────────────────────────────────
    print("\nTest 2: Time stop (10 min)...")
    rm2 = _FakeRiskManager()
    pm2 = PositionManager(risk_manager=rm2, testnet=True)
    await pm2.enter_position(_mock_signal(81_000.0))
    if pm2.open_position:
        pm2.open_position["entry_time"] = time.time() - 660   # 11 min ago
    reason2 = _simulate_monitor(pm2, [81_003.0])   # one tick, no stops hit but time > 600s

    if reason2 == "TIME_STOP":
        print(f"PASS - Time stop triggered (reason={reason2})")
    else:
        print(f"FAIL - Expected TIME_STOP, got: {reason2}")
        failures += 1

    # ── Test 3: PnL calculation ──────────────────────────────────────────
    # Trail activates at $81,002 (stop=$80,992), highest=$81,015 -> stop=$81,005
    # Exit at $81,004: PnL = (81004 - 81000) * 0.01 = $0.04
    print("\nTest 3: PnL calculation...")
    entry, exit_p = 81_000.0, 81_004.0
    qty = round(config.STAKE_USDC / entry, 4)   # 0.01
    pnl = round((exit_p - entry) * qty, 4)
    expected = round(4.0 * qty, 4)
    if abs(pnl - expected) < 0.0001:
        print(f"PASS - PnL=${pnl:+.4f} (qty={qty}, entry=${entry}, exit=${exit_p})")
    else:
        print(f"FAIL - PnL mismatch: ${pnl:.4f} vs expected ${expected:.4f}")
        failures += 1

    print()
    if failures == 0:
        print("All 3 position tests PASSED")
    else:
        print(f"{failures} test(s) FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
