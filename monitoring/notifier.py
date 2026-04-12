"""
Monitoring: logs estruturados + alertas Telegram.
"""
import asyncio
import logging
import structlog
import httpx
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LOG_LEVEL


def setup_logging():
    """Configura structlog para output JSON."""
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )


class TelegramNotifier:
    """Envia alertas via Telegram."""

    def __init__(self):
        self.enabled = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        if self.enabled:
            self.client = httpx.AsyncClient(timeout=10.0)
            self.base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    async def send(self, message: str):
        if not self.enabled:
            return
        try:
            await self.client.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
            )
        except Exception:
            pass

    async def notify_trade(self, direction: str, amount: float, price: float,
                           confidence: float, regime: str):
        msg = (
            f"<b>TRADE</b>\n"
            f"Dir: {direction} | ${amount} @ {price:.4f}\n"
            f"Conf: {confidence:.1f} | Regime: {regime}"
        )
        await self.send(msg)

    async def notify_result(self, won: bool, pnl: float, pnl_today: float):
        emoji = "WIN" if won else "LOSS"
        msg = (
            f"<b>{emoji}</b> ${pnl:+.2f}\n"
            f"P&L hoje: ${pnl_today:+.2f}"
        )
        await self.send(msg)

    async def notify_hedge(self, cost: float, savings: float):
        msg = f"<b>HEDGE</b> custo ${cost:.2f} | savings ${savings:.2f}"
        await self.send(msg)

    async def notify_stop(self, reason: str):
        msg = f"<b>STOP</b> {reason}"
        await self.send(msg)

    async def notify_daily_summary(self, stats: dict):
        msg = (
            f"<b>RESUMO DO DIA</b>\n"
            f"P&L: ${stats.get('pnl_today', 0):+.2f}\n"
            f"Trades: {stats.get('trades_today', 0)}\n"
            f"Win rate: {stats.get('win_rate', 0):.0f}%\n"
            f"Max drawdown: ${stats.get('drawdown', 0):.2f}"
        )
        await self.send(msg)

    async def close(self):
        if self.enabled:
            await self.client.aclose()
