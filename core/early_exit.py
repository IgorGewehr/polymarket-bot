"""
Early Exit — SÓ exits de LUCRO. Sem stop loss. Sem panic sell.

Dados de 284 ciclos + 43 trades live mostram:
- Hold to resolution = +$47.34 vs exits = -$5.06
- 67% accuracy direcional do bot = edge enorme na resolução
- Stop loss DESTRÓI valor: -$17.84 vs -$2.55 sem SL
- Hedges custaram -$15 num dia (eliminados)

Prioridade:
1. Safety Sell: share >= $0.85 → vender (lucro garantido)
2. Delta Guard: delta < 10 nos últimos 60s com lucro → vender
3. Take Profit: ganho >= 40% → vender
4. EV Optimal: ganho >= 25% e matematicamente melhor → vender
5. Perdendo? → NÃO FAZER NADA. Hold to resolution.
"""
import structlog
from dataclasses import dataclass
from config.settings import (
    TAKER_FEE_PCT, TAKE_PROFIT_MIN_GAIN_PCT,
    REVERSAL_RISK_DIVISOR,
)

log = structlog.get_logger()

SAFETY_SELL_PRICE = 0.85
SAFETY_SELL_TIME = 200
DELTA_GUARD_THRESHOLD = 10
DELTA_GUARD_TIME = 60


@dataclass
class ExitEvaluation:
    should_exit: bool
    reason: str
    sell_price: float
    sell_proceeds: float
    sell_pnl: float
    hold_ev: float
    gain_pct: float


def evaluate_early_exit(
    direction: str,
    entry_price: float,
    shares: float,
    cost_basis: float,
    current_yes_price: float,
    time_remaining: float,
    current_delta: float = 0.0,
    lowest_price_seen: float = 0.0,
) -> ExitEvaluation:
    """Avalia se vale vender. SÓ vende com lucro. Nunca com loss."""
    if direction == "Up":
        bid_price = current_yes_price
        p_win = current_yes_price
    else:
        bid_price = 1.0 - current_yes_price
        p_win = 1.0 - current_yes_price

    sell_proceeds = shares * bid_price * (1 - TAKER_FEE_PCT)
    sell_pnl = sell_proceeds - cost_basis
    gain_pct = (bid_price - entry_price) / entry_price if entry_price > 0 else 0

    win_pnl = shares * 1.0 - cost_basis
    loss_pnl = -cost_basis
    reversal_discount = max(0.05, min(0.20, time_remaining / REVERSAL_RISK_DIVISOR))
    adjusted_p = p_win * (1 - reversal_discount)
    hold_ev = adjusted_p * win_pnl + (1 - adjusted_p) * loss_pnl

    no_exit = ExitEvaluation(False, "", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    if shares < 4.5:
        return no_exit

    if time_remaining < 10:
        return no_exit

    # ── 1. SAFETY SELL — share >= $0.85 ──
    if bid_price >= SAFETY_SELL_PRICE and time_remaining < SAFETY_SELL_TIME:
        return ExitEvaluation(True, "safety_sell", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # ── 2. DELTA GUARD — mercado indeciso com lucro ──
    if (time_remaining < DELTA_GUARD_TIME
            and abs(current_delta) < DELTA_GUARD_THRESHOLD
            and sell_pnl > 0):
        return ExitEvaluation(True, "delta_guard", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # ── 3. TAKE PROFIT ──
    if gain_pct >= TAKE_PROFIT_MIN_GAIN_PCT and sell_pnl > hold_ev:
        return ExitEvaluation(True, "take_profit", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # ── 4. EV OPTIMAL ──
    if gain_pct >= 0.25 and sell_pnl > 0 and sell_pnl > hold_ev * 1.30:
        return ExitEvaluation(True, "ev_optimal", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # ── PERDENDO? NÃO VENDER. Hold to resolution. ──
    # Dados: 67% accuracy, hold = +$47/dia vs exits = -$5/dia
    return no_exit
