# Clawdbot - Weather Trading Bot

Automated trading bot for Polymarket weather markets. Uses multi-source weather forecasts (NOAA + Open-Meteo ensemble) to identify mispriced temperature buckets and trade them via the [Simmer Markets SDK](https://simmer.markets).

Fork of [SpartanLabsXyz/simmer-sdk/skills/weather](https://github.com/SpartanLabsXyz/simmer-sdk), fully refactored into a modular architecture with probabilistic modeling, Kelly criterion sizing, and dynamic risk management.

## How It Works

1. **Fetches weather forecasts** from NOAA and Open-Meteo (GFS + ECMWF models) in parallel
2. **Builds an ensemble forecast** with weighted averaging (ECMWF 50%, GFS 30%, NOAA 20%)
3. **Scores all temperature buckets** using a normal distribution CDF model that accounts for forecast horizon uncertainty
4. **Sizes positions** with quarter-Kelly criterion based on estimated edge
5. **Manages exits** dynamically based on cost basis and time to resolution
6. **Protects against losses** with stop-loss on forecast reversal and correlation guards

## Quick Start

### Prerequisites

- Python 3.12+
- A [Simmer Markets](https://simmer.markets) API key

### Setup

```bash
git clone https://github.com/leodid68/Clawdbot.git
cd Clawdbot

# Set your API key
export SIMMER_API_KEY="your-api-key-here"
```

No external dependencies required - the project uses only the Python standard library.

### Run

```bash
# Dry run (default) - shows opportunities without trading
python3 -m weather

# Live trading
python3 -m weather --live

# Show current positions
python3 -m weather --positions

# Show full config
python3 -m weather --config

# Debug mode
python3 -m weather --verbose
```

## Architecture

```
weather/
├── __init__.py          # Package marker + Simmer SDK backward compat exports
├── __main__.py          # CLI entry point (argparse)
├── config.py            # Config dataclass, load/save, LOCATIONS
├── noaa.py              # NOAA Weather API client (api.weather.gov)
├── open_meteo.py        # Open-Meteo multi-model client (GFS + ECMWF)
├── parsing.py           # Event name + temperature bucket parsing
├── probability.py       # NOAA probability model (horizon decay, seasonal, CDF)
├── sizing.py            # Kelly criterion + dynamic exit thresholds
├── state.py             # Persistent state (trades, forecasts, calibration)
├── strategy.py          # Main strategy loop
├── simmer_client.py     # Simmer Markets API client
├── config.json          # User configuration
├── scripts/
│   └── status.py        # Standalone account status script
└── tests/
    ├── test_parsing.py
    ├── test_probability.py
    ├── test_sizing.py
    ├── test_open_meteo.py
    ├── test_state_extended.py
    ├── test_strategy.py
    └── fixtures/
        ├── noaa_forecast.json
        ├── simmer_markets.json
        └── simmer_positions.json
```

## Strategy

### Probability Model

The bot estimates the probability of temperature falling in each bucket using:

- **Horizon-dependent accuracy**: Day 0 = 97%, Day 3 = 85%, Day 7 = 65%, Day 10 = 50%
- **Normal CDF**: Standard deviation grows with forecast horizon (1.5°F at Day 0 to 9°F at Day 10)
- **Seasonal adjustments**: Winter months reduce confidence (x0.90), summer increases it (x1.00)
- **Multi-source ensemble**: Weighted average of NOAA, GFS, and ECMWF reduces individual model error

### Position Sizing (Kelly Criterion)

```
kelly_fraction = (p * b - q) / b
position_size = balance * kelly_fraction * 0.25  # Quarter-Kelly (conservative)
```

Where `p` = estimated probability, `b` = net odds `(1/price - 1)`, `q = 1 - p`.

Quarter-Kelly is used by default because probability estimates are noisy. This prevents catastrophic overbetting.

### Risk Management

| Feature | Description |
|---------|-------------|
| **Dynamic exits** | Exit threshold adapts based on cost basis and time to resolution |
| **Stop-loss on reversal** | Auto-exits when forecast shifts 5+°F away from held bucket |
| **Correlation guard** | Max 1 position per event (prevents buying both "50-54" and "55-59") |
| **Forecast change detection** | Logs when forecasts shift significantly between runs |
| **Calibration tracking** | Records predictions and computes Brier score for self-assessment |
| **Slippage guard** | Blocks trades when estimated slippage exceeds threshold |
| **Flip-flop detection** | Blocks trades when reversing positions too frequently |
| **Race condition guard** | Re-reads position before selling to prevent double-sells |

## Configuration

### config.json

| Field | Default | Description |
|-------|---------|-------------|
| `entry_threshold` | `0.15` | Max price to enter a position (higher = more conservative) |
| `exit_threshold` | `0.45` | Target exit price (used when dynamic exits are off) |
| `max_position_usd` | `2.00` | Cap per position in USD |
| `kelly_fraction` | `0.25` | Kelly fraction (0.25 = quarter-Kelly) |
| `min_ev_threshold` | `0.03` | Minimum expected value to trade a bucket |
| `max_trades_per_run` | `5` | Max trades per execution |
| `max_retries` | `3` | API retry count with exponential backoff |
| `retry_base_delay` | `1.0` | Base delay between retries (seconds) |
| `locations` | `"NYC"` | Active locations (comma-separated) |
| `log_level` | `"INFO"` | Logging level |
| `state_file` | `"state.json"` | Persistent state file path |
| `max_days_ahead` | `7` | Max forecast horizon (days) |
| `seasonal_adjustments` | `true` | Apply seasonal probability adjustments |
| `adjacent_buckets` | `true` | Score and trade adjacent buckets (not just center) |
| `dynamic_exits` | `true` | Adapt exit threshold to cost basis + time |
| `multi_source` | `true` | Use Open-Meteo ensemble (GFS + ECMWF + NOAA) |
| `forecast_change_threshold` | `3.0` | °F change to log re-evaluation |
| `correlation_guard` | `true` | Max 1 position per event |
| `stop_loss_reversal` | `true` | Exit when forecast reverses away from bucket |
| `stop_loss_reversal_threshold` | `5.0` | °F shift to trigger stop-loss |

Update via CLI:

```bash
python3 -m weather --set locations=NYC,Chicago,Seattle
python3 -m weather --set max_position_usd=5.00
python3 -m weather --set kelly_fraction=0.10
```

### Environment Variables

Config priority: `config.json` > environment variables > defaults.

| Variable | Maps to |
|----------|---------|
| `SIMMER_API_KEY` | API authentication (required) |
| `SIMMER_WEATHER_ENTRY` | `entry_threshold` |
| `SIMMER_WEATHER_EXIT` | `exit_threshold` |
| `SIMMER_WEATHER_MAX_POSITION` | `max_position_usd` |
| `SIMMER_WEATHER_SIZING_PCT` | `sizing_pct` |
| `SIMMER_WEATHER_MAX_TRADES` | `max_trades_per_run` |
| `SIMMER_WEATHER_LOCATIONS` | `locations` |

## Supported Locations

| Code | Airport | Coordinates |
|------|---------|-------------|
| `NYC` | LaGuardia (LGA) | 40.78°N, 73.87°W |
| `Chicago` | O'Hare (ORD) | 41.97°N, 87.91°W |
| `Seattle` | Sea-Tac (SEA) | 47.45°N, 122.31°W |
| `Atlanta` | Hartsfield (ATL) | 33.64°N, 84.43°W |
| `Dallas` | DFW | 32.90°N, 97.04°W |
| `Miami` | MIA | 25.80°N, 80.29°W |

Location input is case-insensitive: `nyc`, `NYC`, and `Nyc` all resolve to `NYC`.

## CLI Reference

```
python3 -m weather [OPTIONS]

Options:
  --live              Execute real trades (default is dry-run)
  --dry-run           Show opportunities without trading (default)
  --positions         Show current positions only
  --config            Show full configuration
  --set KEY=VALUE     Update config (repeatable)
  --verbose           Enable DEBUG logging
  --json-log          Output structured JSON logs
  --smart-sizing      Use Kelly criterion sizing
  --no-safeguards     Disable context safeguards
  --no-trends         Disable price trend detection
```

## Data Sources

| Source | URL | Auth | Purpose |
|--------|-----|------|---------|
| **NOAA** | api.weather.gov | None (free) | Point forecasts (high/low temps) |
| **Open-Meteo** | api.open-meteo.com | None (free) | GFS + ECMWF model forecasts |
| **Simmer** | api.simmer.markets | `SIMMER_API_KEY` | Market data, portfolio, trade execution |

All forecast data is fetched in parallel using `ThreadPoolExecutor` for minimal latency.

## Tests

```bash
# Run all 109 tests
python3 -m pytest weather/tests/ -v

# Run specific test module
python3 -m pytest weather/tests/test_probability.py -v
python3 -m pytest weather/tests/test_sizing.py -v
python3 -m pytest weather/tests/test_strategy.py -v
```

Test coverage:
- **Parsing** (33 tests): Location aliases, date formats, temperature buckets, edge cases
- **Probability** (11 tests): Horizon decay, seasonal effects, CDF, bucket sum-to-one
- **Sizing** (15 tests): Kelly edge cases, position caps, min trade, exit thresholds
- **Open-Meteo** (7 tests): Ensemble averaging, spread calculation, metric selection
- **State** (15 tests): Forecast tracking, calibration, correlation guard, save/load roundtrip
- **Strategy** (28 tests): Safeguards, trends, bucket scoring, dry-run integration

## Automation

Run the bot on a schedule with cron:

```bash
# Every 30 minutes during market hours
*/30 * * * * cd /path/to/Clawdbot && SIMMER_API_KEY=your-key python3 -m weather --live >> /var/log/clawdbot.log 2>&1
```

Or with JSON logs for parsing:

```bash
*/30 * * * * cd /path/to/Clawdbot && SIMMER_API_KEY=your-key python3 -m weather --live --json-log >> /var/log/clawdbot.json 2>&1
```

## License

MIT
