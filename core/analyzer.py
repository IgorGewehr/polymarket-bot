"""
Motor de análise de 4 camadas + detecção de regime.

Camada 1: Trend de 5 minutos (slope linear)
Camada 2: Multi-timeframe alignment (5/15/30 min)
Camada 3: Mean reversion via Bollinger Bands
Camada 4: Momentum (aceleração/desaceleração)

Regime: Trending vs Lateral (squeeze detection)
"""
import numpy as np
from dataclasses import dataclass
from config.settings import (
    WEIGHT_TREND_5M, WEIGHT_MULTI_TF, WEIGHT_BOLLINGER, WEIGHT_MOMENTUM,
    BOLLINGER_PERIOD, BOLLINGER_STD, TREND_TICKS, MOMENTUM_WINDOW,
    LATERAL_MAX_DELTA, SQUEEZE_BREAKOUT_MULTIPLIER,
    SQUEEZE_MIN_CALM_TICKS, SQUEEZE_CALM_THRESHOLD
)
from data.price_buffer import PriceBuffer, CycleTracker


@dataclass
class AnalysisResult:
    direction: str              # "Up" ou "Down"
    confidence: float           # -6 a +6
    expected_return: float      # 0.0 a 1.0
    delta: float                # delta atual
    regime: str                 # "trending" ou "lateral"
    is_squeeze_breakout: bool   # True se breakout após squeeze

    # Scores individuais das camadas
    layer1_trend: float
    layer2_alignment: float
    layer3_bollinger: float
    layer4_momentum: float

    # Dados brutos para logging
    slope_5m: float
    btc_price: float
    z_score: float


def calc_slope(prices: np.ndarray) -> float:
    """Regressão linear simples. Retorna slope normalizado."""
    if len(prices) < 3:
        return 0.0
    x = np.arange(len(prices), dtype=np.float64)
    coeffs = np.polyfit(x, prices, 1)
    return coeffs[0]


def calc_bollinger(prices: np.ndarray, period: int = 20, num_std: float = 2.0):
    """Calcula Bollinger Bands e Z-score."""
    if len(prices) < period:
        return 0.0, 0.0, 0.0, 0.0
    window = prices[-period:]
    sma = np.mean(window)
    std = np.std(window)
    if std == 0:
        return sma, sma, sma, 0.0
    upper = sma + num_std * std
    lower = sma - num_std * std
    z_score = (prices[-1] - sma) / std
    return sma, upper, lower, z_score


def calc_momentum(prices: np.ndarray, window: int = 5) -> float:
    """Calcula aceleração (segunda derivada do preço)."""
    if len(prices) < window + 2:
        return 0.0
    roc_recent = prices[-1] - prices[-window // 2 - 1]
    roc_prior = prices[-window // 2 - 1] - prices[-window]
    return roc_recent - roc_prior


def analyze_layer1_trend(share_prices: np.ndarray) -> tuple[str, float, float]:
    """
    Camada 1: Trend de 5 minutos.
    Retorna: (direção, score, slope)
    """
    n = min(TREND_TICKS, len(share_prices))
    if n < 3:
        return "Up", 0.0, 0.0

    prices = share_prices[-n:]
    slope = calc_slope(prices)

    # Score baseado na força do slope
    abs_slope = abs(slope) * 10000  # normalizar
    if abs_slope > 5:
        score = 2.0
    elif abs_slope > 2:
        score = 1.0
    elif abs_slope > 0.5:
        score = 0.5
    else:
        score = 0.0

    direction = "Up" if slope > 0 else "Down"
    return direction, score, slope


def analyze_layer2_multiTF(
    btc_prices: np.ndarray,
    trade_direction: str
) -> tuple[float, int]:
    """
    Camada 2: Multi-timeframe alignment.
    Analisa slope de 5min, 15min, 30min do BTC spot.
    Retorna: (score, alignment_count)
    """
    if len(btc_prices) < 60:
        return 0.0, 0

    slopes = []
    for window in [10, 30, 60]:
        n = min(window, len(btc_prices))
        s = calc_slope(btc_prices[-n:])
        slopes.append(s)

    # Contar quantos timeframes concordam com trade_direction
    if trade_direction == "Up":
        alignment = sum(1 for s in slopes if s > 0)
    else:
        alignment = sum(1 for s in slopes if s < 0)

    score_map = {3: 2.0, 2: 1.0, 1: -1.0, 0: -2.0}
    return score_map[alignment], alignment


def analyze_layer3_bollinger(
    btc_prices: np.ndarray,
    trade_direction: str
) -> tuple[float, float]:
    """
    Camada 3: Mean reversion via Bollinger Bands.
    Retorna: (score, z_score)
    """
    sma, upper, lower, z_score = calc_bollinger(
        btc_prices, BOLLINGER_PERIOD, BOLLINGER_STD
    )

    if trade_direction == "Up":
        if z_score <= -2.0:
            score = 2.0   # Extremo baixo → provável reversão pra cima
        elif z_score <= -1.0:
            score = 1.0
        elif z_score >= 2.0:
            score = -1.0  # Já subiu demais → pode reverter
        else:
            score = 0.0
    else:
        if z_score >= 2.0:
            score = 2.0   # Extremo alto → provável reversão pra baixo
        elif z_score >= 1.0:
            score = 1.0
        elif z_score <= -2.0:
            score = -1.0
        else:
            score = 0.0

    return score, z_score


def analyze_layer4_momentum(
    share_prices: np.ndarray,
    trade_direction: str
) -> float:
    """
    Camada 4: Momentum (aceleração).
    Retorna: score
    """
    accel = calc_momentum(share_prices, MOMENTUM_WINDOW)

    if trade_direction == "Up":
        if accel > 0:
            return 1.0    # Acelerando pra cima — confirma
        elif accel < -0.001:
            return -1.0   # Desacelerando — alerta
        return 0.0
    else:
        if accel < 0:
            return 1.0    # Acelerando pra baixo — confirma
        elif accel > 0.001:
            return -1.0
        return 0.0


def detect_regime(cycle_tracker: CycleTracker) -> str:
    """
    Detecta se o mercado está trending ou lateral.
    Olha os últimos 5 ciclos de delta máximo.
    """
    recent = cycle_tracker.get_recent_max_deltas(5)
    if len(recent) < 3:
        return "trending"  # Dados insuficientes, assumir trending

    if all(d < LATERAL_MAX_DELTA for d in recent):
        return "lateral"
    return "trending"


def detect_squeeze_breakout(
    cycle_tracker: CycleTracker,
    current_delta: float
) -> bool:
    """
    Detecta breakout após squeeze.
    Squeeze = vários ciclos com delta baixo.
    Breakout = delta atual > 2x a média recente.
    """
    avg = cycle_tracker.avg_max_delta(5)
    if avg == 0:
        return False

    return (
        abs(current_delta) > avg * SQUEEZE_BREAKOUT_MULTIPLIER
        and avg < LATERAL_MAX_DELTA
    )


def run_analysis(
    share_buffer: PriceBuffer,
    btc_buffer: PriceBuffer,
    cycle_tracker: CycleTracker,
    current_share_price: float
) -> AnalysisResult | None:
    """
    Pipeline completo de análise.
    Retorna AnalysisResult ou None se dados insuficientes.
    """
    share_prices = share_buffer.get_prices()
    btc_prices = btc_buffer.get_prices()

    if len(share_prices) < 5 or len(btc_prices) < 10:
        return None

    # Camada 1: Trend de 5min
    direction, l1_score, slope = analyze_layer1_trend(share_prices)

    # Camada 2: Multi-timeframe
    l2_score, alignment = analyze_layer2_multiTF(btc_prices, direction)

    # Camada 3: Bollinger
    l3_score, z_score = analyze_layer3_bollinger(btc_prices, direction)

    # Camada 4: Momentum
    l4_score = analyze_layer4_momentum(share_prices, direction)

    # Score composto → confidence -6 a +6
    raw_score = (
        l1_score * WEIGHT_TREND_5M +
        l2_score * WEIGHT_MULTI_TF +
        l3_score * WEIGHT_BOLLINGER +
        l4_score * WEIGHT_MOMENTUM
    )
    confidence = raw_score * 3  # Escalar para range -6 a +6

    # Delta atual — calculado diretamente dos preços do buffer
    all_prices = share_buffer.get_prices()
    if len(all_prices) >= 2:
        delta = abs(all_prices[-1] - all_prices[0]) * 10000  # Em "pontos"
    else:
        delta = 0.0

    # Retorno esperado
    if direction == "Up":
        expected_return = (1.0 - current_share_price) / current_share_price
    else:
        expected_return = (1.0 - (1 - current_share_price)) / (1 - current_share_price)

    # Regime
    regime = detect_regime(cycle_tracker)
    is_breakout = detect_squeeze_breakout(cycle_tracker, delta)

    btc_price = btc_buffer.latest_price() or 0.0

    return AnalysisResult(
        direction=direction,
        confidence=confidence,
        expected_return=expected_return,
        delta=delta,
        regime=regime,
        is_squeeze_breakout=is_breakout,
        layer1_trend=l1_score,
        layer2_alignment=l2_score,
        layer3_bollinger=l3_score,
        layer4_momentum=l4_score,
        slope_5m=slope,
        btc_price=btc_price,
        z_score=z_score
    )
