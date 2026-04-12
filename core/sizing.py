"""
Sistema de sizing dinâmico — $1 a $3.
Calibrado nos dados de 57 trades manuais.
"""
from config.settings import (
    SIZING_WEIGHT_CONFIDENCE, SIZING_WEIGHT_RETURN,
    SIZING_WEIGHT_TIME, SIZING_WEIGHT_DIRECTION,
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


def calculate_bet_size(
    confidence: float,
    expected_return: float,
    time_remaining: float,
    direction: str,
    consecutive_losses: int,
    is_drawdown: bool = False,
    is_squeeze_breakout: bool = False
) -> int:
    """
    Calcula o tamanho da aposta ($1, $2, ou $3).

    Args:
        confidence: Score de confiança (-6 a +6)
        expected_return: Retorno esperado (0.0 a 1.0+)
        time_remaining: Segundos restantes no ciclo
        direction: "Up" ou "Down"
        consecutive_losses: Número de losses consecutivos
        is_drawdown: True se em drawdown do dia
        is_squeeze_breakout: True se é breakout após squeeze

    Returns:
        Valor da aposta: 1, 2, ou 3
    """
    # Se em drawdown, forçar sizing mínimo
    if is_drawdown:
        return FORCED_SIZING_ON_DRAWDOWN

    time_slot = get_time_slot(time_remaining)

    # ── Fator 1: Confiança (40%) ──
    conf_normalized = min(abs(confidence) / 6.0, 1.0)

    # ── Fator 2: Retorno esperado (25%) ──
    if expected_return >= 0.30:
        ret_score = 1.0
    elif expected_return >= 0.15:
        ret_score = 0.6
    else:
        ret_score = 0.3

    # ── Fator 3: Bonus de tempo (20%) ──
    t_bonus = TIME_BONUS.get(time_slot, 0.3)

    # ── Fator 4: Bonus de direção (15%) ──
    d_bonus = DIRECTION_BONUS.get(direction, 0.65)

    # ── Penalty por losses consecutivos ──
    loss_penalty = max(
        LOSS_PENALTY_FLOOR,
        1.0 - (consecutive_losses * LOSS_PENALTY_RATE)
    )

    # Score composto
    raw_score = (
        conf_normalized * SIZING_WEIGHT_CONFIDENCE +
        ret_score * SIZING_WEIGHT_RETURN +
        t_bonus * SIZING_WEIGHT_TIME +
        d_bonus * SIZING_WEIGHT_DIRECTION
    ) * loss_penalty

    # Bonus por squeeze breakout (sinal raro mas forte)
    if is_squeeze_breakout:
        raw_score *= 1.2

    # Mapeamento para valor
    if raw_score >= SIZING_HIGH_THRESHOLD:
        return 3
    elif raw_score >= SIZING_MID_THRESHOLD:
        return 2
    else:
        return 1


def sizing_breakdown(
    confidence: float,
    expected_return: float,
    time_remaining: float,
    direction: str,
    consecutive_losses: int
) -> dict:
    """Retorna breakdown detalhado do cálculo para logging."""
    time_slot = get_time_slot(time_remaining)
    conf_normalized = min(abs(confidence) / 6.0, 1.0)
    ret_score = 1.0 if expected_return >= 0.30 else (0.6 if expected_return >= 0.15 else 0.3)
    t_bonus = TIME_BONUS.get(time_slot, 0.3)
    d_bonus = DIRECTION_BONUS.get(direction, 0.65)
    loss_penalty = max(LOSS_PENALTY_FLOOR, 1.0 - (consecutive_losses * LOSS_PENALTY_RATE))

    return {
        "confidence_factor": round(conf_normalized * SIZING_WEIGHT_CONFIDENCE, 3),
        "return_factor": round(ret_score * SIZING_WEIGHT_RETURN, 3),
        "time_factor": round(t_bonus * SIZING_WEIGHT_TIME, 3),
        "direction_factor": round(d_bonus * SIZING_WEIGHT_DIRECTION, 3),
        "loss_penalty": round(loss_penalty, 2),
        "raw_score": round(
            (conf_normalized * SIZING_WEIGHT_CONFIDENCE +
             ret_score * SIZING_WEIGHT_RETURN +
             t_bonus * SIZING_WEIGHT_TIME +
             d_bonus * SIZING_WEIGHT_DIRECTION) * loss_penalty, 3
        ),
        "time_slot": time_slot,
    }
