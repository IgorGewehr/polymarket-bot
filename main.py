"""
Polymarket BTC Trading Bot — Entry Point

Uso:
    python main.py              # Modo normal (usa DRY_RUN do .env)
    python main.py --live       # Modo live (override DRY_RUN=false)
    python main.py --backtest   # Modo backtest (replay de dados históricos)
"""
import asyncio
import sys
import signal
import structlog
import threading

from monitoring.notifier import setup_logging


def run_dashboard(engine):
    """Roda o dashboard FastAPI numa thread separada."""
    import uvicorn
    from dashboard.api import app, set_engine
    set_engine(engine)
    uvicorn.run(app, host="0.0.0.0", port=8889, log_level="warning")


def main():
    setup_logging()
    log = structlog.get_logger()

    mode = "live"
    if "--backtest" in sys.argv:
        mode = "backtest"
    elif "--live" in sys.argv:
        import config.settings as cfg
        cfg.DRY_RUN = False

    log.info("bot_starting", mode=mode)

    if mode == "backtest":
        from backtesting.simulator import run_backtest
        asyncio.run(run_backtest())
    else:
        from core.engine import TradingEngine
        engine = TradingEngine()

        # Dashboard em thread separada
        dash_thread = threading.Thread(target=run_dashboard, args=(engine,), daemon=True)
        dash_thread.start()
        log.info("dashboard_started", url="http://localhost:8889")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Graceful shutdown
        def handle_signal(sig, frame):
            log.info("shutdown_signal", signal=sig)
            engine.running = False

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        try:
            # Tentar usar uvloop para performance
            try:
                import uvloop
                uvloop.install()
                log.info("uvloop_enabled")
            except ImportError:
                log.info("uvloop_not_available, using default loop")

            loop.run_until_complete(engine.start())
        except KeyboardInterrupt:
            log.info("keyboard_interrupt")
        finally:
            loop.run_until_complete(engine.shutdown())
            loop.close()
            log.info("bot_stopped")


if __name__ == "__main__":
    main()
