"""
Early Exit v3 — EV Optimal Dinâmico + Stop Loss (-30%).

Filosofia:
- Vencedores: sair cedo via ev_optimal (captura ganho antes de reversão)
- Perdedores: limitados pelo Stop Loss (-30%) ou resolução
- Volume confirma: volume forte na direção = hold mais tempo
- Tempo restante: quanto menos tempo, menor o threshold para sair

Exits em ordem de prioridade:
1. Safety Sell   → share >= $0.85 (quase certo, pegar logo)
2. Take Profit   → gain >= 35%   (ganho sólido, garantir)
3. EV Optimal    → dinâmico por tempo + volume
4. Delta Guard   → últimos 60s, mercado 50/50, com lucro
5. Stop Loss     → perda >= 30%  (cortar posições ruins)
6. Hold          → aguardando
"""
import structlog
from dataclasses import dataclass
from config.settings import TAKER_FEE_PCT, REVERSAL_RISK_DIVISOR

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
    volume_imbalance: float = 0.0,   # volume BTC na direção da posição (+/-)
) -> ExitEvaluation:
    """
    Avalia se vale vender agora.
    SEM stop loss — perdedores vão à resolução.
    """
    if direction == "Up":
        bid_price = current_yes_price
        p_win = current_yes_price
    else:
        bid_price = 1.0 - current_yes_price
        p_win = 1.0 - current_yes_price

    bid_price = min(bid_price, 0.95)

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

    # ═══════════════════════════════════════════════════════
    # 1. SAFETY SELL — share muito alta, pouco upside restante
    # ═══════════════════════════════════════════════════════
    if bid_price >= SAFETY_SELL_PRICE and time_remaining < SAFETY_SELL_TIME:
        return ExitEvaluation(True, "safety_sell", bid_price, sell_proceeds,
                              sell_pnl, hold_ev, gain_pct)

    # ═══════════════════════════════════════════════════════
    # 2. TAKE PROFIT — ganho sólido, garantir
    # ═══════════════════════════════════════════════════════
    if gain_pct >= 0.35 and sell_pnl > 0:
        return ExitEvaluation(True, "take_profit", bid_price, sell_proceeds,
                              sell_pnl, hold_ev, gain_pct)

    # ═══════════════════════════════════════════════════════
    # 3. EV OPTIMAL DINÂMICO — threshold por tempo + volume
    # ═══════════════════════════════════════════════════════
    # Volume forte na nossa direção → tese ainda viva → segurar mais
    # Volume fraco ou oposto → sair logo com o que tem
    vol_in_direction = volume_imbalance if direction == "Up" else -volume_imbalance
    vol_is_strong = vol_in_direction > 0.25   # volume confirma fortemente
    vol_is_weak = vol_in_direction < -0.10    # volume está contra

    if time_remaining > 180:
        # 3+ min restantes: segurar — mercado pode ir até $0.85+ (take_profit captura)
        min_gain = 0.25
        ev_mult = 1.70 if vol_is_strong else (1.50 if not vol_is_weak else 1.30)
    elif time_remaining > 60:
        # 1-3 min: threshold moderado — ainda pode subir mais
        min_gain = 0.20
        ev_mult = 1.50 if vol_is_strong else (1.35 if not vol_is_weak else 1.20)
    else:
        # Últimos 60s: sair com qualquer ganho real (reversão iminente)
        min_gain = 0.10
        ev_mult = 1.15 if vol_is_strong else 1.10

    if gain_pct >= min_gain and sell_pnl > 0 and sell_pnl > hold_ev * ev_mult:
        reason = f"ev_optimal"
        return ExitEvaluation(True, reason, bid_price, sell_proceeds,
                              sell_pnl, hold_ev, gain_pct)

    # ═══════════════════════════════════════════════════════
    # 4. DELTA GUARD — últimos 60s, mercado 50/50, temos lucro
    # ═══════════════════════════════════════════════════════
    if (time_remaining < DELTA_GUARD_TIME
            and abs(current_delta) < DELTA_GUARD_THRESHOLD
            and sell_pnl > 0):
        return ExitEvaluation(True, "delta_guard", bid_price, sell_proceeds,
                              sell_pnl, hold_ev, gain_pct)

    # ═══════════════════════════════════════════════════════
    # 5. STOP LOSS — perda atingiu -30% do valor de entrada
    # ═══════════════════════════════════════════════════════
    if gain_pct <= -0.30:
        return ExitEvaluation(True, "stop_loss", bid_price, sell_proceeds,
                              sell_pnl, hold_ev, gain_pct)

    # ═══════════════════════════════════════════════════════
    # 6. HOLD — aguardar recuperação ou resolução
    # ═══════════════════════════════════════════════════════
    return no_exit
