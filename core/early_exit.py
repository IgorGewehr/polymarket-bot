"""
Early Exit — Safety Sell, Delta Guard, Take Profit e Stop Loss.

Prioridade:
1. Safety Sell: share >= $0.80 com < 2min → vender (lucro quase certo)
2. Delta Guard: delta < 10 nos últimos 60s com lucro → vender (50/50 não vale o risco)
3. Take Profit: ganho >= 30% E vender > hold_ev → vender
4. Stop Loss: preço caiu 35%+ do entry → vender para limitar loss
"""
import structlog
from dataclasses import dataclass
from config.settings import (
    TAKER_FEE_PCT, TAKE_PROFIT_MIN_GAIN_PCT,
    STOP_LOSS_THRESHOLD_PCT, REVERSAL_RISK_DIVISOR,
)

log = structlog.get_logger()

# Safety sell: share muito alta → vender (lucro grande, pouco upside restante)
SAFETY_SELL_PRICE = 0.85       # Share >= $0.85 → max $0.15 upside vs risco de reversão
SAFETY_SELL_TIME = 200         # Ativar depois dos primeiros 100s de monitoramento

# Delta guard: mercado indeciso perto do fim → vender se tem lucro
DELTA_GUARD_THRESHOLD = 10     # Delta < 10 = indeciso
DELTA_GUARD_TIME = 60          # Nos últimos 60s


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
) -> ExitEvaluation:
    """
    Avalia se vale vender shares agora.

    Args:
        current_delta: delta absoluto atual do ciclo (para delta guard)
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

    # EV de segurar (com desconto de reversão)
    win_pnl = shares * 1.0 - cost_basis
    loss_pnl = -cost_basis
    reversal_discount = max(0.05, min(0.20, time_remaining / REVERSAL_RISK_DIVISOR))
    adjusted_p = p_win * (1 - reversal_discount)
    hold_ev = adjusted_p * win_pnl + (1 - adjusted_p) * loss_pnl

    no_exit = ExitEvaluation(False, "", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # Mínimo ~4.5 shares para vender (fills parciais podem dar menos que 5)
    if shares < 4.5:
        return no_exit

    # Não vender nos últimos 10s (muito perto da resolução, spread pode ser ruim)
    if time_remaining < 10:
        return no_exit

    # ── 1. SAFETY SELL — share muito alta → vender ──
    # Share >= $0.85 → max upside é $0.15/share, não vale o risco de reversão
    if bid_price >= SAFETY_SELL_PRICE and time_remaining < SAFETY_SELL_TIME:
        return ExitEvaluation(True, "safety_sell", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # ── 2. DELTA GUARD — mercado indeciso nos últimos 60s ──
    # Delta < 10 = 50/50, se temos lucro → vender
    if (time_remaining < DELTA_GUARD_TIME
            and abs(current_delta) < DELTA_GUARD_THRESHOLD
            and sell_pnl > 0):
        return ExitEvaluation(True, "delta_guard", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # ── 3. STOP LOSS — preço caiu demais (ativa SEMPRE, sem restrição de tempo) ──
    price_drop = (entry_price - bid_price) / entry_price if entry_price > 0 else 0
    if price_drop >= STOP_LOSS_THRESHOLD_PCT:
        return ExitEvaluation(True, "stop_loss", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # ── 4. TAKE PROFIT — ganho bom (ativa SEMPRE, sem restrição de tempo) ──
    if gain_pct >= TAKE_PROFIT_MIN_GAIN_PCT and sell_pnl > hold_ev:
        return ExitEvaluation(True, "take_profit", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    # ── 5. EV PURO — vender é significativamente melhor ──
    # Só ativar com gain mínimo de 15% (evitar vender muito cedo)
    if gain_pct >= 0.15 and sell_pnl > 0 and sell_pnl > hold_ev * 1.30:
        return ExitEvaluation(True, "ev_optimal", bid_price, sell_proceeds, sell_pnl, hold_ev, gain_pct)

    return no_exit
