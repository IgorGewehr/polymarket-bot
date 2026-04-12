"""
Sistema de hedge EV-driven com regras anti-erosão.
Só executa hedge quando matematicamente compensa E
dentro dos limites diários.
"""
import time
import structlog
from dataclasses import dataclass, field
from config.settings import (
    MAX_HEDGES_PER_DAY, MIN_LOSS_PROB_FOR_HEDGE,
    MIN_HEDGE_ROI, HEDGE_COOLDOWN_SECONDS,
    HEDGE_COST_RATIO_LIMIT
)

log = structlog.get_logger()


@dataclass
class Position:
    direction: str
    bet_size: float
    entry_price: float
    potential_return: float
    entry_time: float
    market_id: str
    token_id: str
    entry_confidence: float = 0.0
    entry_alignment: int = 0
    shares: float = 0.0
    # Hedge tracking
    hedge_cost: float = 0.0
    hedge_price: float = 0.0
    hedge_direction: str = ""
    hedge_potential_return: float = 0.0
    has_hedge: bool = False
    # Lock profit tracking
    has_lock: bool = False
    lock_price_b: float = 0.0
    lock_shares: float = 0.0
    lock_guaranteed_profit: float = 0.0
    lock_side_b_direction: str = ""
    lock_side_b_token_id: str = ""
    # Early exit tracking
    exited_early: bool = False
    exit_price: float = 0.0
    exit_proceeds: float = 0.0
    exit_reason: str = ""


@dataclass
class HedgeOpportunity:
    direction: str          # Oposto à posição original
    cost: float             # Quanto custa o hedge
    potential_return: float  # Quanto retorna se hedge ganhar
    price: float            # Preço da share do hedge
    token_id: str           # Token ID para executar


@dataclass
class HedgeTracker:
    hedges_today: int = 0
    total_hedge_cost: float = 0.0
    total_hedge_savings: float = 0.0
    last_hedge_time: float = 0.0

    def can_hedge(self) -> tuple[bool, str]:
        """Verifica se pode hedgear com base nas regras anti-erosão."""

        # Regra 1: Máximo de hedges por dia
        if self.hedges_today >= MAX_HEDGES_PER_DAY:
            return False, f"Limite diário atingido ({self.hedges_today}/{MAX_HEDGES_PER_DAY})"

        # Regra 2: Cooldown entre hedges
        elapsed = time.time() - self.last_hedge_time
        if self.last_hedge_time > 0 and elapsed < HEDGE_COOLDOWN_SECONDS:
            remaining = int(HEDGE_COOLDOWN_SECONDS - elapsed)
            return False, f"Cooldown ativo ({remaining}s restantes)"

        # Regra 3: Se custo total > 1.5x savings, parar
        if (self.total_hedge_cost > 0 and self.total_hedge_savings > 0 and
                self.total_hedge_cost > self.total_hedge_savings * HEDGE_COST_RATIO_LIMIT):
            return False, "Custo de hedges excede savings — erosão detectada"

        return True, "OK"

    def record_hedge(self, cost: float, estimated_savings: float):
        self.hedges_today += 1
        self.total_hedge_cost += cost
        self.total_hedge_savings += max(0, estimated_savings)
        self.last_hedge_time = time.time()

    def reset_daily(self):
        self.hedges_today = 0
        self.total_hedge_cost = 0.0
        self.total_hedge_savings = 0.0


def estimate_loss_probability(
    position: Position,
    current_momentum: float,
    current_alignment: int,
    original_alignment: int
) -> float:
    """
    Estima a probabilidade de perder a posição atual.
    Baseado em mudanças de momentum e alignment desde a entrada.
    """
    base_prob = 0.35  # Baseline de 35% (win rate histórico ~65-79%)

    # Momentum inverteu?
    if position.direction == "Up" and current_momentum < 0:
        base_prob += 0.15
    elif position.direction == "Down" and current_momentum > 0:
        base_prob += 0.15

    # Alignment piorou?
    alignment_drop = original_alignment - current_alignment
    base_prob += alignment_drop * 0.08

    # Clamp entre 0.2 e 0.95
    return max(0.2, min(0.95, base_prob))


def calculate_hedge_ev(
    position: Position,
    hedge: HedgeOpportunity,
    loss_probability: float
) -> tuple[float, float, float]:
    """
    Calcula o EV com e sem hedge.
    Retorna: (ev_sem_hedge, ev_com_hedge, savings)
    """
    win_prob = 1 - loss_probability
    orig_profit = position.potential_return - position.bet_size

    # EV sem hedge
    ev_no_hedge = (win_prob * orig_profit) - (loss_probability * position.bet_size)

    # EV com hedge
    # Cenário 1: original ganha → lucro original - custo do hedge
    win_orig = orig_profit - hedge.cost
    # Cenário 2: hedge ganha → retorno hedge - custo hedge - perda original
    win_hedge = hedge.potential_return - hedge.cost - position.bet_size

    ev_with_hedge = (win_prob * win_orig) + (loss_probability * win_hedge)

    savings = ev_with_hedge - ev_no_hedge

    return ev_no_hedge, ev_with_hedge, savings


def should_evaluate_hedge(
    position: Position,
    current_momentum: float,
    current_alignment: int
) -> bool:
    """Decide se deve AVALIAR um hedge (não necessariamente executar)."""

    # Momentum inverteu contra a posição?
    if position.direction == "Up" and current_momentum < -0.001:
        return True
    if position.direction == "Down" and current_momentum > 0.001:
        return True

    # Alignment mudou significativamente?
    if current_alignment < position.entry_alignment - 1:
        return True

    return False


def should_execute_hedge(
    position: Position,
    hedge: HedgeOpportunity,
    loss_probability: float,
    tracker: HedgeTracker
) -> tuple[bool, str, float]:
    """
    Decide se deve EXECUTAR o hedge.
    Todas as 4 condições devem ser verdadeiras.

    Returns: (should_hedge, reason, savings)
    """
    # Condição 0: Tracker permite?
    can, reason = tracker.can_hedge()
    if not can:
        return False, reason, 0.0

    # Condição 1: Probabilidade de perder > 55%
    if loss_probability < MIN_LOSS_PROB_FOR_HEDGE:
        return False, f"Prob. de perder baixa ({loss_probability:.0%})", 0.0

    # Condição 2: Odds do hedge > 15% retorno
    if hedge.cost > 0:
        hedge_roi = (hedge.potential_return - hedge.cost) / hedge.cost
    else:
        hedge_roi = 0
    if hedge_roi < MIN_HEDGE_ROI:
        return False, f"ROI do hedge muito baixo ({hedge_roi:.0%})", 0.0

    # Condição 3: Hedge melhora o EV
    ev_no, ev_with, savings = calculate_hedge_ev(position, hedge, loss_probability)
    if savings <= 0:
        return False, f"Hedge piora o EV (savings: ${savings:.2f})", savings

    log.info("hedge_approved",
             loss_prob=f"{loss_probability:.0%}",
             hedge_roi=f"{hedge_roi:.0%}",
             savings=f"${savings:.2f}",
             ev_without=f"${ev_no:.2f}",
             ev_with=f"${ev_with:.2f}")

    return True, f"Hedge aprovado — savings ${savings:.2f}", savings
