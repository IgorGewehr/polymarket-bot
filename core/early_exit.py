"""
Early Exit — Profit exits apenas. Stop loss é via limit order no book.

Sistema de 2 limit sells no book desde a compra:
1. PROFIT SELL a entry + $0.20 → captura wins (~$2.00 avg)
2. STOP LOSS SELL a $0.40 → caps loss a ~$1.73

Quando um preenche, cancela o outro. Zero lag, zero slippage.

Exits adicionais via monitor:
- Safety Sell: share >= $0.85 → vender
- Take Profit: ganho >= 35% → vender
- EV Optimal: ganho >= 30% e matematicamente melhor → vender
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
    """Avalia exits de LUCRO. Stop loss é via limit order no book."""
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

    # ── 2. TAKE PROFIT — ganho >= 35% (subiu de 40%, dados mostram que era alto demais) ──
    if gain_pct >= 0.35 and sell_pnl > hold_ev:
        return ExitEvaluation(True, "take_profit", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # ── 3. EV OPTIMAL — ganho >= 30% e vender > hold × 1.30 ──
    if gain_pct >= 0.30 and sell_pnl > 0 and sell_pnl > hold_ev * 1.30:
        return ExitEvaluation(True, "ev_optimal", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # ── 4. DELTA GUARD — mercado indeciso com lucro ──
    if (time_remaining < DELTA_GUARD_TIME
            and abs(current_delta) < DELTA_GUARD_THRESHOLD
            and sell_pnl > 0):
        return ExitEvaluation(True, "delta_guard", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # ── 5. FALLBACK STOP LOSS — se limit sell no book falhou ──
    # NUNCA perder mais que 1/3 da aposta. Se share caiu 33%+ do entry, vender.
    # Isso é backup — normalmente o SL a $0.40 no book já pegou.
    price_drop = (entry_price - bid_price) / entry_price if entry_price > 0 else 0
    if price_drop >= 0.33:
        return ExitEvaluation(True, "fallback_stop_loss", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    return no_exit
