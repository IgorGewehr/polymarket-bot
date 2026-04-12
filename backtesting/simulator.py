"""
Backtesting engine — replay de trades históricos para validar estratégia.
Usa dados do DuckDB para simular a performance do bot.
"""
import numpy as np
import structlog
from dataclasses import dataclass
from core.analyzer import (
    analyze_layer1_trend, analyze_layer2_multiTF,
    analyze_layer3_bollinger, analyze_layer4_momentum
)
from core.sizing import calculate_bet_size
from core.risk_manager import RiskManager
from config.settings import (
    MIN_DELTA, MIN_RETURN_PCT, MIN_CONFIDENCE,
    WEIGHT_TREND_5M, WEIGHT_MULTI_TF, WEIGHT_BOLLINGER, WEIGHT_MOMENTUM,
    LATERAL_MAX_DELTA
)

log = structlog.get_logger()


@dataclass
class BacktestResult:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    win_rate: float = 0.0
    roi: float = 0.0
    sharpe: float = 0.0
    trades_skipped_lateral: int = 0
    trades_skipped_filter: int = 0
    total_bet: float = 0.0


def simulate_from_manual_data() -> BacktestResult:
    """
    Simula o bot usando os dados manuais coletados.
    Esses dados têm: apostado, retorno, win/loss, tempo, direção, delta.
    """
    # Dados da página 3-5 do PDF (trades com direção e delta)
    trades = [
        (1, 1.49, 'S', '3:30', 'Down', 49),
        (1, 1.16, 'S', '1:30', 'Down', 33),
        (2, 2.17, 'S', '0:30', 'Down', 33),
        (1, 1.41, 'N', '3:30', 'Down', 43),
        (1, 1.30, 'S', '2:30', 'Up', 38),
        (1, 1.06, 'S', '1:30', 'Up', 66),
        (2, 2.04, 'S', '0:30', 'Up', 59),
        (1, 1.27, 'N', '2:30', 'Down', 55),
        (2, 2.04, 'S', '0:30', 'Up', 49),
        (1, 1.20, 'S', '3:30', 'Up', 80),
        (1, 1.22, 'S', '2:30', 'Up', 65),
        (1, 1.30, 'S', '1:30', 'Up', 30),
        (1, 1.32, 'S', '2:30', 'Down', 39),
        (1, 1.15, 'S', '1:30', 'Down', 48),
        (1, 1.27, 'N', '3:30', 'Down', 54),
        (1, 1.04, 'N', '1:30', 'Down', 87),
        (1, 1.41, 'N', '3:30', 'Down', 40),
        (1, 1.47, 'N', '2:30', 'Down', 33),
        (1, 1.85, 'S', '3:30', 'Up', 17),
        (1, 1.22, 'S', '2:30', 'Up', 64),
        (1, 1.09, 'S', '1:30', 'Up', 59),
        (1, 1.14, 'S', '3:30', 'Up', 102),
        (1, 1.07, 'S', '2:30', 'Up', 105),
        (1, 1.96, 'S', '3:30', 'Up', 4),
        (1, 1.28, 'S', '0:30', 'Up', 26),
        (1, 1.49, 'S', '3:30', 'Down', 29),
        (1, 1.06, 'S', '2:30', 'Down', 120),
        (1, 1.37, 'S', '3:30', 'Up', 45),
        (1, 1.37, 'S', '2:30', 'Up', 35),
        (1, 1.43, 'S', '3:30', 'Up', 34),
        (1, 1.23, 'S', '2:30', 'Up', 37),
        (1, 1.25, 'S', '1:30', 'Up', 40),
        (1, 1.67, 'S', '3:30', 'Down', 10),
        (1, 1.32, 'S', '2:30', 'Down', 36),
        (1, 1.35, 'N', '3:30', 'Up', 33),
        (1, 1.20, 'N', '1:30', 'Up', 33),
        (1, 1.33, 'N', '3:30', 'Down', 49),
        (1, 1.47, 'N', '2:30', 'Down', 34),
        (1, 1.85, 'N', '3:30', 'Up', 8),
        (1, 1.75, 'N', '2:30', 'Up', 36),
        (1, 1.54, 'S', '4:00', 'Up', 20),
        (1, 2.22, 'S', '3:30', 'Up', 4),
        (1, 1.67, 'S', '4:00', 'Up', 6),
        (1, 1.75, 'S', '3:30', 'Up', 8),
        (1, 2.04, 'S', '4:30', 'Up', 4),
        (1, 1.85, 'S', '4:00', 'Up', 14),
        (1, 1.43, 'S', '3:30', 'Up', 29),
        (1, 1.61, 'S', '4:30', 'Up', 20),
        (1, 1.52, 'S', '4:00', 'Up', 23),
        (1, 1.85, 'S', '3:30', 'Up', 12),
        (1, 1.85, 'S', '4:30', 'Down', 4),
        (1, 1.79, 'S', '3:30', 'Down', 2),
        (1, 1.72, 'S', '4:30', 'Up', 8),
        (1, 1.33, 'S', '4:00', 'Up', 39),
        (1, 1.08, 'S', '3:30', 'Up', 89),
        (5, 7.81, 'S', '4:00', 'Down', 22),
        (5, 7.46, 'S', '3:30', 'Down', 37),
    ]

    time_to_seconds = {
        '4:30': 270, '4:00': 240, '3:30': 210,
        '2:30': 150, '1:30': 90, '1:00': 60, '0:30': 30
    }

    result = BacktestResult()
    risk = RiskManager()
    pnls = []

    for bet, ret, won_str, time_slot, direction, delta in trades:
        won = won_str == 'S'
        time_remaining = time_to_seconds.get(time_slot, 210)
        expected_return = (ret - bet) / bet

        # Simular filtros do bot (apenas os que podemos avaliar sem dados live)
        if delta < MIN_DELTA:
            result.trades_skipped_filter += 1
            continue
        if expected_return < MIN_RETURN_PCT:
            result.trades_skipped_filter += 1
            continue
        if time_remaining < 60:
            result.trades_skipped_filter += 1
            continue

        can_trade, _ = risk.can_trade()
        if not can_trade and risk.state.pnl_today <= -15:
            # Only enforce the hard daily loss limit in backtest
            # Cooldowns are time-based and can't be simulated here
            result.trades_skipped_filter += 1
            continue

        # Estimar confidence baseado nos dados disponíveis
        # (em produção, as 4 camadas calculam isso com dados live)
        conf_from_delta = 4.0 if delta > 30 else (3.5 if delta > 15 else 3.0)
        conf_from_dir = 1.0 if direction == "Up" else -0.5
        confidence = conf_from_delta + conf_from_dir

        bot_size = calculate_bet_size(
            confidence=confidence,
            expected_return=expected_return,
            time_remaining=time_remaining,
            direction=direction,
            consecutive_losses=risk.state.consecutive_losses,
            is_drawdown=risk.is_drawdown
        )

        # Calcular PnL com sizing do bot
        if won:
            # Proporcional ao sizing
            bot_ret = ret * (bot_size / bet) if bet > 0 else ret
            pnl = bot_ret - bot_size
        else:
            pnl = -bot_size

        result.total_trades += 1
        result.total_bet += bot_size
        if won:
            result.wins += 1
        else:
            result.losses += 1
        result.total_pnl += pnl
        pnls.append(pnl)

        result.peak_pnl = max(result.peak_pnl, result.total_pnl)
        drawdown = result.peak_pnl - result.total_pnl
        result.max_drawdown = max(result.max_drawdown, drawdown)

        risk.update(pnl)

    # Métricas finais
    if result.total_trades > 0:
        result.win_rate = result.wins / result.total_trades * 100
        winning_pnls = [p for p in pnls if p > 0]
        losing_pnls = [p for p in pnls if p < 0]
        result.avg_win = np.mean(winning_pnls) if winning_pnls else 0
        result.avg_loss = np.mean(losing_pnls) if losing_pnls else 0
        result.roi = (result.total_pnl / result.total_bet * 100) if result.total_bet > 0 else 0

        if len(pnls) > 1:
            returns = np.array(pnls)
            if np.std(returns) > 0:
                result.sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)

    return result


async def run_backtest():
    """Entry point para backtesting."""
    log.info("backtest_starting")

    result = simulate_from_manual_data()

    print("\n" + "=" * 60)
    print("RESULTADO DO BACKTEST")
    print("=" * 60)
    print(f"  Trades executados:  {result.total_trades}")
    print(f"  Trades filtrados:   {result.trades_skipped_filter}")
    print(f"  Wins:               {result.wins} ({result.win_rate:.1f}%)")
    print(f"  Losses:             {result.losses}")
    print(f"  P&L total:          ${result.total_pnl:+.2f}")
    print(f"  Total apostado:     ${result.total_bet:.2f}")
    print(f"  ROI:                {result.roi:.1f}%")
    print(f"  Avg win:            ${result.avg_win:+.2f}")
    print(f"  Avg loss:           ${result.avg_loss:+.2f}")
    print(f"  Max drawdown:       ${result.max_drawdown:.2f}")
    print(f"  Sharpe ratio:       {result.sharpe:.2f}")
    print("=" * 60)

    return result
