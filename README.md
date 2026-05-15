# SethTradez

Hyperliquid BTC perpetuals trading bot. Runs alongside SethBetz on the same Railway VPS.

## What it does

SethTradez monitors BTC 5-minute candles on Hyperliquid and trades a specific momentum signal:
- At **2 minutes 30 seconds** into each candle, if BTC has moved **$40+** from the candle open price, it enters a position in that direction
- Manages the position with a hard stop loss, trailing stop, and 10-minute time stop
- Logs all trades to Supabase and sends Telegram alerts

## Relationship to SethBetz

SethBetz and SethTradez are **separate services** that share the same:
- Railway VPS (deployed as separate Railway services)
- Supabase project (SethTradez writes to `hyperbetz_trades` table)
- Telegram infrastructure (separate bot token: `TELEGRAM_TOKEN_HYPERBETZ`)

SethTradez does **not** modify any SethBetz code or tables.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in all values
```

### 3. Create Supabase table

Run the SQL in `db/supabase_client.py` (top of file, in the docstring) in your Supabase dashboard.

### 4. Run in paper mode (testnet)

```bash
python main.py
```

Ensure `.env` has `PAPER_MODE=true` and `HL_TESTNET=true`.

## Switching from testnet to mainnet

1. Get a Hyperliquid mainnet wallet and fund it with USDC
2. Set `HL_PRIVATE_KEY` and `HL_WALLET_ADDRESS` to mainnet values
3. Set `HL_TESTNET=false` in `.env`
4. Keep `PAPER_MODE=true` until you've verified signal quality
5. In Telegram: `/paper off` → `/confirm_live`

## Telegram Commands

| Command | Description |
|---|---|
| `/status` | Show current position and daily P&L |
| `/pause` | Pause trading |
| `/resume` | Resume trading |
| `/stake [amount]` | Update USDC stake per trade |
| `/stop [amount]` | Update initial stop loss distance |
| `/trail [amount]` | Update trailing stop distance |
| `/move [amount]` | Update minimum candle move threshold |
| `/close` | Emergency close open position |
| `/daily` | Show today's trade summary |
| `/daily_limit [amount]` | Update daily loss limit |
| `/paper on/off` | Toggle paper mode |
| `/confirm_live` | Confirm switch to live trading |

## Railway Deployment

The `Procfile` runs `python main.py` as a worker process. Deploy as a separate Railway service in the same project as SethBetz.

Set all `.env` variables in Railway's environment variable settings.
