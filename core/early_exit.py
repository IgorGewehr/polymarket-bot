"""
Early Exit — Take Profit e Stop Loss vendendo shares antes da resolução.

Compara PnL de vender agora vs EV de segurar até o fim,
com desconto de risco de reversão de último segundo.
"""
import structlog
from dataclasses import dataclass
from config.settings import (
    TAKER_FEE_PCT, TAKE_PROFIT_MIN_GAIN_PCT,
    STOP_LOSS_THRESHOLD_PCT, EARLY_EXIT_MIN_TIME,
    EARLY_EXIT_MAX_TIME, REVERSAL_RISK_DIVISOR,
)

log = structlog.get_logger()


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
) -> ExitEvaluation:
    """
    Avalia se vale vender shares agora.

    Returns ExitEvaluation com should_exit e reason.
    """
    # Preço bid das NOSSAS shares
    if direction == "Up":
        bid_price = current_yes_price
        p_win = current_yes_price
    else:
        bid_price = 1.0 - current_yes_price
        p_win = 1.0 - current_yes_price

    # PnL de vender agora
    sell_proceeds = shares * bid_price * (1 - TAKER_FEE_PCT)
    sell_pnl = sell_proceeds - cost_basis
    gain_pct = (bid_price - entry_price) / entry_price if entry_price > 0 else 0

    # EV de segurar até resolução (com desconto de reversão)
    win_pnl = shares * 1.0 - cost_basis
    loss_pnl = -cost_basis

    reversal_discount = max(0.05, min(0.20, time_remaining / REVERSAL_RISK_DIVISOR))
    adjusted_p = p_win * (1 - reversal_discount)
    hold_ev = adjusted_p * win_pnl + (1 - adjusted_p) * loss_pnl

    no_exit = ExitEvaluation(False, "", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # Guards de tempo
    if time_remaining < EARLY_EXIT_MIN_TIME or time_remaining > EARLY_EXIT_MAX_TIME:
        return no_exit

    # Mínimo 5 shares para vender
    if shares < 5.0:
        return no_exit

    # Stop loss: preço caiu 50%+ do entry
    price_drop = (entry_price - bid_price) / entry_price if entry_price > 0 else 0
    if price_drop >= STOP_LOSS_THRESHOLD_PCT:
        return ExitEvaluation(True, "stop_loss", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # Take profit: ganho >= 40% E vender é melhor que segurar
    if gain_pct >= TAKE_PROFIT_MIN_GAIN_PCT and sell_pnl > hold_ev:
        return ExitEvaluation(True, "take_profit", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # EV puro: vender é significativamente melhor que segurar
    if sell_pnl > 0 and sell_pnl > hold_ev * 1.15:
        return ExitEvaluation(True, "ev_optimal", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    return no_exit
