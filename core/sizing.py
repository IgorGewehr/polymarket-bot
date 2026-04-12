"""
Sistema de sizing — Kelly fracionário por convicção.
Aposta $1 a $3 baseado no edge real (probabilidade vs preço da share).
"""
from config.settings import (
    SIZING_HIGH_THRESHOLD, SIZING_MID_THRESHOLD,
    TIME_BONUS, DIRECTION_BONUS,
    LOSS_PENALTY_RATE, LOSS_PENALTY_FLOOR,
    FORCED_SIZING_ON_DRAWDOWN
)


def get_time_slot(time_remaining: float) -> str:
    """Converte segundos restantes para time slot."""
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


def estimate_win_probability(
    trend_strength: int,
    entry_price: float,
    direction: str,
) -> float:
    """
    Estima probabilidade de ganhar baseado na força da trend.

    Args:
        trend_strength: Quantos timeframes concordam (0-3)
        entry_price: Preço da share ($0.50-$0.75)
        direction: "Up" ou "Down"

    Returns:
        Probabilidade estimada (0.0-1.0)
    """
    # Base: preço da share É a probabilidade do mercado
    market_prob = entry_price

    # Ajuste por trend strength
    # 3/3 TF concordam → mercado provavelmente está certo, +5%
    # 2/3 TF concordam → sinal moderado, +2%
    # 1/3 ou 0/3 → contra a trend, -5% (não deveria acontecer com nossos filtros)
    if trend_strength >= 3:
        adjustment = 0.05
    elif trend_strength >= 2:
        adjustment = 0.02
    else:
        adjustment = -0.05

    # Up historicamente ganha mais (89%) que Down (62%)
    if direction == "Down":
        adjustment -= 0.03

    return min(0.85, max(0.40, market_prob + adjustment))


def calculate_kelly_size(
    entry_price: float,
    estimated_prob: float,
    bankroll: float = 10.0,
    kelly_fraction: float = 0.25,
) -> float:
    """
    Calcula bet size via Kelly fracionário.

    Kelly: f* = (p*b - q) / b
    onde p = prob de ganhar, q = 1-p, b = odds (retorno por $1 apostado)

    Args:
        entry_price: Preço da share
        estimated_prob: Probabilidade estimada de ganhar
        bankroll: Capital disponível
        kelly_fraction: Fração de Kelly (0.25 = quarter Kelly)

    Returns:
        Bet size em dólares
    """
    if entry_price <= 0 or entry_price >= 1:
        return 1.0

    # Odds: quanto ganha por $1 apostado (ex: share @ $0.55 → odds = 0.45/0.55 = 0.818)
    b = (1.0 - entry_price) / entry_price
    p = estimated_prob
    q = 1.0 - p

    # Kelly: f* = (p*b - q) / b
    kelly = (p * b - q) / b if b > 0 else 0

    if kelly <= 0:
        return 1.0  # Edge negativo → bet mínimo

    # Quarter Kelly × bankroll
    raw_size = kelly * kelly_fraction * bankroll

    return raw_size


def calculate_bet_size(
    confidence: float,
    expected_return: float,
    time_remaining: float,
    direction: str,
    consecutive_losses: int,
    is_drawdown: bool = False,
    is_squeeze_breakout: bool = False,
    entry_price: float = 0.50,
    trend_strength: int = 2,
) -> int:
    """
    Calcula o tamanho da aposta ($1, $2, ou $3).
    Usa Kelly fracionário como base, ajustado por penalidades.

    Returns:
        Valor da aposta: 1, 2, ou 3
    """
    # Se em drawdown, forçar sizing mínimo
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
        return 1  # Após 2+ losses, sizing mínimo

    if trend_strength >= 3 and entry_price <= 0.55:
        return 3  # Trend 3/3 + preço barato = máximo edge

    if trend_strength >= 2:
        return 2  # Trend 2/3+ = sizing normal

    return 1  # Trend fraca


def sizing_breakdown(
    entry_price: float,
    direction: str,
    trend_strength: int,
    time_remaining: float,
    consecutive_losses: int
) -> dict:
    """Retorna breakdown detalhado do cálculo para logging."""
    prob = estimate_win_probability(trend_strength, entry_price, direction)
    kelly_size = calculate_kelly_size(entry_price, prob)
    time_slot = get_time_slot(time_remaining)
    loss_penalty = max(LOSS_PENALTY_FLOOR, 1.0 - (consecutive_losses * LOSS_PENALTY_RATE))
    t_bonus = TIME_BONUS.get(time_slot, 0.3)

    return {
        "estimated_prob": round(prob, 3),
        "kelly_raw": round(kelly_size, 3),
        "loss_penalty": round(loss_penalty, 2),
        "time_bonus": round(t_bonus, 2),
        "time_slot": time_slot,
        "final_score": round(kelly_size * loss_penalty * t_bonus, 3),
    }
