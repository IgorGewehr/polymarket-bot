"""
Configurações globais do bot.
Todos os thresholds calibrados nos dados históricos de 57 trades manuais.
"""
import os
from dotenv import load_dotenv

load_dotenv()


# ── Polymarket Credentials (mesma carteira do polymarket-agent) ───
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")
POLYMARKET_CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
POLYMARKET_CLOB_URL = os.getenv("POLYMARKET_CLOB_URL", "https://clob.polymarket.com")
GAMMA_API_URL = os.getenv("GAMMA_API_URL", "https://gamma-api.polymarket.com")
POLYMARKET_WS = os.getenv("POLYMARKET_WS", "wss://ws-subscriptions-clob.polymarket.com/ws/market")
BINANCE_WS = os.getenv("BINANCE_WS", "wss://stream.binance.com:9443/ws/btcusdt@trade")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DUCKDB_PATH = os.getenv("DUCKDB_PATH", "./data/trades.duckdb")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


# ── Timing (segundos) ───────────────────────────────────────────
TICK_INTERVAL = 2               # Amostragem de preço a cada 2s
CYCLE_DURATION = 300            # 5 minutos por ciclo
ENTRY_WINDOW_START = 270        # 4:30 restantes — janela abre
ENTRY_WINDOW_SOFT = 240         # 4:00 — relaxar threshold 10%
ENTRY_DEADLINE = 215            # 3:35 — deadline forçado
ENTRY_CUTOFF = 210              # 3:30 — pular se não entrou
MIN_TIME_REMAINING = 60         # Nunca apostar com < 1:00 restante
HEDGE_MONITOR_INTERVAL = 2     # Monitorar posição a cada 2s


# ── Filtros Absolutos ────────────────────────────────────────────
MIN_DELTA = 4                   # Nunca apostar com delta < 4
MIN_RETURN_PCT = 0.05           # Retorno mínimo de 5% (dados mostram que trades <10% ainda são lucrativos)
MIN_CONFIDENCE = 3              # Score mínimo |3| para entrar
MAX_CONSECUTIVE_LOSSES = 5      # STOP TOTAL após 5 losses seguidos
COOLDOWN_SECONDS = 900          # 15 minutos de cooldown (após 3 losses)
FULL_STOP_AFTER_LOSSES = 5      # Para tudo após 5 losses consecutivos


# ── Análise: pesos das 4 camadas ────────────────────────────────
WEIGHT_TREND_5M = 0.30          # Camada 1: trend de 5 minutos
WEIGHT_MULTI_TF = 0.30          # Camada 2: multi-timeframe alignment
WEIGHT_BOLLINGER = 0.20         # Camada 3: mean reversion
WEIGHT_MOMENTUM = 0.20          # Camada 4: aceleração/desaceleração

BOLLINGER_PERIOD = 20           # Períodos para média móvel
BOLLINGER_STD = 2.0             # Desvios padrão para as bandas
TREND_TICKS = 10                # Ticks para calcular slope (10 x 2s = 20s)
MOMENTUM_WINDOW = 5             # Ticks para calcular aceleração


# ── Sizing: $1 a $3 ─────────────────────────────────────────────
SIZING_WEIGHT_CONFIDENCE = 0.40
SIZING_WEIGHT_RETURN = 0.25
SIZING_WEIGHT_TIME = 0.20
SIZING_WEIGHT_DIRECTION = 0.15

# Thresholds de sizing
SIZING_HIGH_THRESHOLD = 0.70    # Score >= 0.70 → $3
SIZING_MID_THRESHOLD = 0.45     # Score >= 0.45 → $2
                                 # Score < 0.45 → $1

# Bonus por tempo de entrada (calibrados nos dados históricos)
TIME_BONUS = {
    "4:30": 1.0,    # 100% win rate, 81% ROI
    "4:00": 0.9,    # 100% win rate, 57% ROI
    "3:30": 0.7,    # 73% win rate, 53% ROI
    "2:30": 0.5,    # 69% win rate, 31% ROI
    "1:30": 0.3,    # 75% win rate, 16% ROI
}

# Bonus por direção
DIRECTION_BONUS = {
    "Up": 1.0,      # 89% win rate histórico
    "Down": 0.65,   # 62% win rate histórico
}

# Penalty por losses consecutivos
LOSS_PENALTY_RATE = 0.20        # -20% por loss consecutivo
LOSS_PENALTY_FLOOR = 0.20      # Mínimo 20% do sizing normal


# ── Hedge ────────────────────────────────────────────────────────
MAX_HEDGES_PER_DAY = 2
MIN_LOSS_PROB_FOR_HEDGE = 0.55  # Só hedge se prob. de perder > 55%
MIN_HEDGE_ROI = 0.15            # Odds do hedge > 15% retorno
HEDGE_COOLDOWN_SECONDS = 900    # 15 min entre hedges
HEDGE_COST_RATIO_LIMIT = 1.5   # Se custo > 1.5x savings, parar


# ── Lock Profit (compra assimétrica YES+NO) ─────────────────────
LOCK_PROFIT_ENABLED = True
LOCK_MIN_PROFIT_PER_SHARE = 0.02   # Mínimo $0.02/share para executar
LOCK_FEE_RATE = 0.10               # 10% maker fee
LOCK_SPREAD_BUFFER = 0.02          # Buffer de spread quando usa preço derivado
LOCK_MIN_TIME_REMAINING = 30       # Não tentar lock com < 30s restantes


# ── Early Exit (Take Profit / Stop Loss) ────────────────────────
EARLY_EXIT_ENABLED = True
TAKER_FEE_PCT = 0.0315             # Fee de taker para SELL orders
TAKE_PROFIT_MIN_GAIN_PCT = 0.30    # Ganho mínimo de 30% para considerar TP
STOP_LOSS_THRESHOLD_PCT = 0.35     # Vender se preço caiu 35%+ do entry
EARLY_EXIT_MIN_TIME = 15           # Não vender nos últimos 15s
EARLY_EXIT_MAX_TIME = 200          # Começar a avaliar exit mais cedo
REVERSAL_RISK_DIVISOR = 1200.0     # Para desconto de reversão


# ── Risk Management ─────────────────────────────────────────────
MAX_DAILY_LOSS = 15.0           # Stop loss diário
MAX_TRADES_PER_DAY = 200
MAX_TRADES_PER_HOUR = 12
DRAWDOWN_REDUCE_THRESHOLD = 8.0  # Se cair $8 do pico, sizing = $1
FORCED_SIZING_ON_DRAWDOWN = 1


# ── Detecção de Regime (Lateral vs Trending) ─────────────────────
REGIME_LOOKBACK_CYCLES = 5      # Olhar últimos 5 ciclos
LATERAL_MAX_DELTA = 15          # Se nenhum ciclo teve delta > 15 = lateral
SQUEEZE_BREAKOUT_MULTIPLIER = 2.0  # Breakout = delta > 2x média recente
SQUEEZE_MIN_CALM_TICKS = 6     # Mínimo de ticks calmos antes do breakout
SQUEEZE_CALM_THRESHOLD = 3     # "Calmo" = delta absoluto < 3


# ── Price Buffer ─────────────────────────────────────────────────
BUFFER_SIZE = 400               # ~13 minutos a 2s por tick (cobre 2.5 ciclos)
BTC_BUFFER_SIZE = 1800          # 30 minutos de preço BTC a 1s


# ── Mercados BTC no Polymarket ───────────────────────────────────
# O bot procura mercados com estas palavras no título
MARKET_KEYWORDS = ["Bitcoin", "BTC", "above", "below"]
MARKET_CATEGORY = "crypto"
