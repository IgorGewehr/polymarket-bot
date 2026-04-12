"""
Lock Profit Assimétrico — lucro garantido comprando ambos os lados.

Se YES pago + NO atual < payout líquido → comprar NO → lucro certo.
"""
import structlog
from dataclasses import dataclass
from config.settings import LOCK_MIN_PROFIT_PER_SHARE, LOCK_FEE_RATE

log = structlog.get_logger()


@dataclass
class LockOpportunity:
    price_a: float
    price_b: float
    direction_b: str
    token_id_b: str
    shares: float
    cost_b: float
    profit_per_share: float
    profit_total: float


def calculate_lock_profit(price_a: float, price_b: float) -> tuple[bool, float]:
    """
    Calcula se comprar lado B garante lucro.

    Fee é cobrada sobre o lucro do lado vencedor: (1.00 - price) * fee_rate.
    Worst case: lado mais barato ganha (fee maior).

    Returns: (is_profitable, profit_per_share)
    """
    if price_a <= 0 or price_b <= 0 or price_a >= 1 or price_b >= 1:
        return False, 0.0

    cheapest = min(price_a, price_b)
    worst_fee = (1.00 - cheapest) * LOCK_FEE_RATE
    net_payout = 1.00 - worst_fee
    profit = net_payout - price_a - price_b

    return profit > 0, profit


def evaluate_lock(
    price_a: float,
    price_b: float,
    direction_b: str,
    token_id_b: str,
    shares_a: float,
) -> LockOpportunity | None:
    """Avalia se vale executar lock profit."""
    # Precisa de pelo menos 5 shares no lado A para fazer lock
    if shares_a < 5.0:
        return None

    is_profitable, profit_per_share = calculate_lock_profit(price_a, price_b)

    if not is_profitable or profit_per_share < LOCK_MIN_PROFIT_PER_SHARE:
        return None

    # Comprar MESMA quantidade de shares que temos no lado A
    shares = round(shares_a, 2)
    cost_b = price_b * shares

    return LockOpportunity(
        price_a=price_a,
        price_b=price_b,
        direction_b=direction_b,
        token_id_b=token_id_b,
        shares=shares,
        cost_b=cost_b,
        profit_per_share=profit_per_share,
        profit_total=profit_per_share * shares,
    )
