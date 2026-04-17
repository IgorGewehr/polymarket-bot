"""
Early Exit — DESATIVADO.

Dados de 344 trades mostram que early exits custaram -$50.96:
- V3: exits transformaram +$9.07 em -$2.44
- V6: exits transformaram +$19.99 em -$10.32
- V7: exits transformaram +$11.23 em -$3.21

Estratégia "O Disciplinado": hold to resolution SEMPRE.
Sem TP, sem SL, sem safety sell.
"""
from dataclasses import dataclass


@dataclass
class ExitEvaluation:
    should_exit: bool
    reason: str
    sell_price: float
    sell_proceeds: float
    sell_pnl: float
    hold_ev: float
    gain_pct: float


def evaluate_early_exit(**kwargs) -> ExitEvaluation:
    """Sempre retorna no-exit. Hold to resolution."""
    return ExitEvaluation(
        should_exit=False,
        reason="hold_to_resolution",
        sell_price=0.0,
        sell_proceeds=0.0,
        sell_pnl=0.0,
        hold_ev=0.0,
        gain_pct=0.0,
    )
