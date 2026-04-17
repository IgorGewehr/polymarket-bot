"""
Configuracoes — Estrategia "Market Maker"

Baseada em analise de 3500+ whale trades:
- Frequent-Jack: +$206, compra lados baratos, locks profit
- 0x20d2309cd9: +$245, same pattern
- Compra um lado <= $0.48, se outro lado tambem cai, compra e trava lucro
- Total cost < $1.00 = lucro garantido na resolucao
"""
import os
from dotenv import load_dotenv

load_dotenv()

POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")
POLYMARKET_CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
POLYMARKET_CLOB_URL = os.getenv("POLYMARKET_CLOB_URL", "https://clob.polymarket.com")
GAMMA_API_URL = os.getenv("GAMMA_API_URL", "https://gamma-api.polymarket.com")
POLYMARKET_WS = os.getenv("POLYMARKET_WS", "wss://ws-subscriptions-clob.polymarket.com/ws/market")
BINANCE_WS = os.getenv("BINANCE_WS", "wss://stream.binance.com:9443/ws/btcusdt@trade")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DUCKDB_PATH = os.getenv("DUCKDB_PATH", "./data/trades2.duckdb")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# == Timing ==============================================================
TICK_INTERVAL = 2
CYCLE_DURATION = 300

# Phase boundaries (seconds remaining in cycle)
PHASE_COLLECT_START = 300       # 5:00 -> start collecting data
PHASE_FIRST_BUY_START = 285    # 4:45 -> compra 15s após abertura (pega spike inicial)
PHASE_FIRST_BUY_END = 150      # 2:30 -> para de comprar
PHASE_SECOND_BUY_START = 150   # 2:30 -> eligible for lock / take profit (alinha com fim do first buy)
PHASE_SECOND_BUY_END = 90      # 1:30 -> stop second buy
PHASE_EXIT_START = 90           # 1:30 -> exit / hold phase

# == Market Maker Strategy ================================================
SHARES_PER_TRADE = 5            # Shares per buy order (Polymarket min = 5)

# First buy: only buy a side if price in this range
MIN_BUY_PRICE = 0.38            # Nunca comprar abaixo (direcional demais, lock impossível)
MAX_BUY_PRICE = 0.45

# Lock buy: buy second side if its price drops to this or below
# This ensures total cost < $1.00 -> guaranteed profit at resolution
LOCK_BUY_PRICE = 0.45

# Take profit: sell when gain >= 30% do entry price
# Ex: entry $0.18 → TP $0.23, entry $0.46 → TP $0.60
TAKE_PROFIT_PCT = 0.30

# Late sell: in exit phase, sell if gain >= 15%
# Ex: entry $0.18 → sell $0.21, entry $0.46 → sell $0.53
LATE_SELL_PCT = 0.15

# == Maker Order Config ===================================================
MAKER_FILL_TIMEOUT = 45.0      # Seconds to wait for maker fill
LOCK_FILL_TIMEOUT = 10.0       # Shorter timeout for lock buy (urgency)

# == Risk Management ======================================================
FULL_STOP_AFTER_LOSSES = 7     # Consecutive losses before full stop
MAX_DAILY_LOSS = 4.0           # Max daily loss in USD
MAX_TRADES_PER_DAY = 200
MAX_TRADES_PER_HOUR = 20

# == Analysis (used by analyzer.py for BTC 2/3 slope) ====================
WEIGHT_TREND_5M = 0.30
WEIGHT_MULTI_TF = 0.30
WEIGHT_BOLLINGER = 0.20
WEIGHT_MOMENTUM = 0.20
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2.0
TREND_TICKS = 10
MOMENTUM_WINDOW = 5

BUFFER_SIZE = 400
BTC_BUFFER_SIZE = 1800

LATERAL_MAX_DELTA = 15
SQUEEZE_BREAKOUT_MULTIPLIER = 2.0
SQUEEZE_MIN_CALM_TICKS = 6
SQUEEZE_CALM_THRESHOLD = 3

MARKET_KEYWORDS = ["Bitcoin", "BTC", "above", "below"]
MARKET_CATEGORY = "crypto"

# == Signal thresholds (data/signals.py) ==================================
CHAINLINK_MIN_DELTA_PCT = 0.03
VOLUME_IMBALANCE_THRESHOLD = 0.15
LIQUIDATION_CASCADE_THRESHOLD = 500_000
MIN_SIGNAL_SCORE = 3
