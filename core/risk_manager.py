"""
Risk Manager — Estratégia "O Disciplinado"

Mudanças vs versão anterior:
- Removido lock após 3 losses (edge fino precisa de volume de trades)
- Stop total: 7 losses consecutivos (não 5)
- Daily loss: $5 (protege banca de $9)
- Sem cooldown (cada ciclo é independente)
"""
import time
import structlog
from dataclasses import dataclass, field
from config.settings import (
    MAX_DAILY_LOSS, MAX_TRADES_PER_DAY, MAX_TRADES_PER_HOUR,
    FULL_STOP_AFTER_LOSSES
)

log = structlog.get_logger()


@dataclass
class RiskState:
    pnl_today: float = 0.0
    peak_pnl: float = 0.0
    trades_today: int = 0
    trades_this_hour: int = 0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    hour_start: float = field(default_factory=time.time)
    is_stopped: bool = False
    stop_reason: str = ""


class RiskManager:
    def __init__(self):
        self.state = RiskState()

    def can_trade(self) -> tuple[bool, str]:
        """Verifica se pode abrir um novo trade."""
        s = self.state

        if s.is_stopped:
            return False, s.stop_reason

        if s.pnl_today <= -MAX_DAILY_LOSS:
            s.is_stopped = True
            s.stop_reason = f"Max loss diário atingido (${s.pnl_today:.2f})"
            return False, s.stop_reason

        if s.trades_today >= MAX_TRADES_PER_DAY:
            return False, f"Max trades/dia ({s.trades_today})"

        if time.time() - s.hour_start > 3600:
            s.trades_this_hour = 0
            s.hour_start = time.time()
        if s.trades_this_hour >= MAX_TRADES_PER_HOUR:
            return False, f"Max trades/hora ({s.trades_this_hour})"

        if s.consecutive_losses >= FULL_STOP_AFTER_LOSSES:
            s.is_stopped = True
            s.stop_reason = (
                f"STOP: {s.consecutive_losses} losses consecutivos. "
                f"Bot parado para proteger capital."
            )
            log.error("full_stop_activated",
                      consecutive_losses=s.consecutive_losses,
                      pnl_today=f"${s.pnl_today:.2f}")
            return False, s.stop_reason

        return True, "OK"

    def unlock(self):
        """Destrava o bot manualmente."""
        self.state.is_stopped = False
        self.state.stop_reason = ""
        self.state.consecutive_losses = 0
        log.info("manual_unlock")

    def update(self, pnl: float):
        """Atualiza estado após resultado de um trade."""
        s = self.state
        s.pnl_today += pnl
        s.trades_today += 1
        s.trades_this_hour += 1

        if pnl > 0:
            s.consecutive_losses = 0
            s.consecutive_wins += 1
            s.peak_pnl = max(s.peak_pnl, s.pnl_today)
        else:
            s.consecutive_losses += 1
            s.consecutive_wins = 0

        log.info("risk_update",
                 pnl=f"${pnl:+.2f}",
                 pnl_today=f"${s.pnl_today:+.2f}",
                 streak=f"W{s.consecutive_wins}" if pnl > 0 else f"L{s.consecutive_losses}",
                 trades=s.trades_today)

    def get_summary(self) -> dict:
        s = self.state
        return {
            "pnl_today": round(s.pnl_today, 2),
            "trades_today": s.trades_today,
            "consecutive_losses": s.consecutive_losses,
            "consecutive_wins": s.consecutive_wins,
            "is_stopped": s.is_stopped,
        }

    def reset_daily(self):
        """Reset para um novo dia."""
        self.state = RiskState()
        log.info("risk_manager_reset")
