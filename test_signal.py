"""
Tests for SignalEngine — mocks price fetch, verifies signal emission at 2:30 mark.
"""

import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))

import config
config.MIN_CANDLE_MOVE_USD = 40.0

import core.signal_engine as se_module
from core.signal_engine import SignalEngine


async def _check_signal(open_price: float, mock_price: float) -> list:
    """
    Directly invoke _check_at_230 with a mocked price, bypassing the 150s sleep.
    Returns list of signals emitted.
    """
    emitted = []

    engine = SignalEngine(on_signal=lambda s: emitted.append(s), testnet=True)
    engine._candle_open_price = open_price
    engine._candle_open_time = 1_000_000

    # Patch price fetch to return mock_price immediately
    original_fetch = se_module._fetch_btc_price

    async def mock_fetch(testnet):
        return mock_price

    se_module._fetch_btc_price = mock_fetch

    # Patch asyncio.sleep so the 150s wait is instant
    original_sleep = se_module.asyncio.sleep

    async def fast_sleep(n):
        pass

    se_module.asyncio.sleep = fast_sleep

    try:
        await engine._check_at_230(engine._candle_open_time, engine._candle_open_price)
    finally:
        se_module._fetch_btc_price = original_fetch
        se_module.asyncio.sleep = original_sleep

    return emitted


async def run_all_tests():
    print("Running signal engine tests...\n")
    failures = 0

    # Test 1: LONG — price up $45 from open
    results = await _check_signal(open_price=81_000, mock_price=81_045)
    if results and results[0]["direction"] == "LONG" and abs(results[0]["move_usd"] - 45) < 0.01:
        print(f"PASS — LONG signal emitted (move=${results[0]['move_usd']:.2f})")
    else:
        print(f"FAIL — Expected LONG signal, got: {results}")
        failures += 1

    # Test 2: SHORT — price down $45 from open
    results = await _check_signal(open_price=81_000, mock_price=80_955)
    if results and results[0]["direction"] == "SHORT" and abs(results[0]["move_usd"] - 45) < 0.01:
        print(f"PASS — SHORT signal emitted (move=${results[0]['move_usd']:.2f})")
    else:
        print(f"FAIL — Expected SHORT signal, got: {results}")
        failures += 1

    # Test 3: No signal — price only up $20 (below $40 threshold)
    results = await _check_signal(open_price=81_000, mock_price=81_020)
    if not results:
        print("PASS — No signal emitted (move $20 < threshold $40)")
    else:
        print(f"FAIL — Expected no signal, got: {results}")
        failures += 1

    print()
    if failures == 0:
        print("All 3 signal tests PASSED")
    else:
        print(f"{failures} test(s) FAILED")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_all_tests())
