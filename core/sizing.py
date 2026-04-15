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

    if consecutive_losses >= 3:
        return 2   # 3+ losses: mínimo
    elif consecutive_losses >= 2:
        return 4   # 2 losses: cautela
    elif consecutive_wins >= 3:
        return 8   # 3+ wins seguidas: escalar (cap)
    else:
        return 6   # Base


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
