"""
Sizing fixo: $1 por trade.

Dados de 344 trades mostram:
- $1 sizing: W/L ratio 1.01x (único sizing com ratio >= 1)
- $10 sizing: W/L ratio 0.40x (causou -$60 de loss)
- Kelly criterion com p=0.57, b=0.90: fração ótima = 9.2% → $0.83 em $9

$1 é ligeiramente acima do Kelly, mas aceitável.
Escalar apenas quando banca crescer.
"""
from config.settings import BET_SIZE


def calculate_bet_size(**kwargs) -> float:
    """Retorna sizing fixo. Sem variação, sem escalada."""
    return BET_SIZE
