"""
Sizing Kelly-lite: $3 base, reduz com losses consecutivos.

- 0-1 losses: $3 (base)
- 2 losses:   $2 (cautela)
- 3+ losses:  $1 (mínimo — mercado choppy, esperar)

Lógica: 10 wins × $0.90 = +$9.00. 1 loss máx = -$3.00.
1 loss nunca apaga 10 wins. Sem SL = perda máxima é o bet.
"""
from config.settings import FORCED_SIZING_ON_DRAWDOWN


def get_time_slot(time_remaining: float) -> str:
    if time_remaining >= 270:
        return "4:30"
    elif time_remaining >= 240:
        return "4:00"
    elif time_remaining >= 210:
        return "3:30"
    elif time_remaining >= 150:
        return "2:30"
    else:
        return "1:30"


def calculate_bet_size(
    confidence: float = 0,
    expected_return: float = 0,
    time_remaining: float = 0,
    direction: str = "Up",
    consecutive_losses: int = 0,
    consecutive_wins: int = 0,
    is_drawdown: bool = False,
    is_squeeze_breakout: bool = False,
    entry_price: float = 0.50,
    trend_strength: int = 2,
) -> int:
    """
    Kelly-lite: $3 base, desce com losses consecutivos.
    Sobe de volta automaticamente após um win.
    """
    if is_drawdown:
        return FORCED_SIZING_ON_DRAWDOWN

    # Penalty por losses consecutivos
    loss_penalty = max(
        LOSS_PENALTY_FLOOR,
        1.0 - (consecutive_losses * LOSS_PENALTY_RATE)
    )

    # Sizing baseado na força da trend + preço da share
    # $3 = trend forte + preço bom (habilita lock e early exit com 5+ shares)
    # $2 = trend moderada ou preço alto
    # $1 = após 2+ losses seguidos

    if loss_penalty < 0.6:
        return 3  # Após 2+ losses, sizing reduzido

    if trend_strength >= 2:
        return 5  # Trend 2/3+ = $5

    return 3  # Trend fraca = $3


def sizing_breakdown(
    entry_price: float,
    direction: str,
    trend_strength: int,
    time_remaining: float,
    consecutive_losses: int
) -> dict:
    size = calculate_bet_size(consecutive_losses=consecutive_losses)
    return {
        "size": size,
        "consecutive_losses": consecutive_losses,
        "time_slot": get_time_slot(time_remaining),
        "reason": f"kelly_lite_losses={consecutive_losses}",
    }
