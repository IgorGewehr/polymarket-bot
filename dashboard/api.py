"""
API REST para o dashboard do bot.
Roda como servidor FastAPI em paralelo ao engine.
"""
import time
from datetime import datetime
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

app = FastAPI(title="Polymarket Bot Dashboard", docs_url=None, redoc_url=None)

# Referência ao engine (injetada no startup)
_engine = None


def set_engine(engine):
    global _engine
    _engine = engine


# ── Static files ───────────────────────────────────────────
DASHBOARD_DIR = Path(__file__).parent

app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(DASHBOARD_DIR / "index.html")


# ── API Endpoints ──────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    if not _engine:
        return {"error": "Engine not initialized"}

    rm = _engine.risk_manager
    pos = _engine.current_position
    market = _engine.current_market

    return {
        "running": _engine.running,
        "dry_run": False,
        "timestamp": time.time(),
        "market": {
            "question": market.get("question", "?")[:80] if market else None,
            "time_remaining": round(_engine._get_time_remaining(market), 1) if market else 0,
        } if market else None,
        "position": {
            "direction": pos.direction,
            "bet_size": pos.bet_size,
            "entry_price": round(pos.entry_price, 4),
            "confidence": round(pos.entry_confidence, 1),
        } if pos else None,
        "risk": rm.get_summary(),
        "prices": {
            "yes": round(_engine.poly_feed.yes_price, 4),
            "no": round(_engine.poly_feed.no_price, 4),
            "btc": round(_engine.btc_feed.last_price, 2),
        },
        "hedge_tracker": {
            "hedges_today": _engine.hedge_tracker.hedges_today,
            "total_cost": round(_engine.hedge_tracker.total_hedge_cost, 2),
            "total_savings": round(_engine.hedge_tracker.total_hedge_savings, 2),
        },
    }


@app.get("/api/trades")
async def get_trades():
    if not _engine:
        return []
    try:
        trades = _engine.storage.get_recent_trades(100)
        return trades
    except Exception:
        return []


@app.get("/api/pnl")
async def get_pnl():
    """PnL cumulativo por trade para gráfico."""
    if not _engine:
        return []
    try:
        result = _engine.storage.conn.execute("""
            SELECT timestamp, pnl, direction, bet_size, result,
                   SUM(pnl) OVER (ORDER BY timestamp) as cumulative_pnl
            FROM trades
            WHERE result IS NOT NULL
            ORDER BY timestamp
        """).fetchall()
        cols = ["timestamp", "pnl", "direction", "bet_size", "result", "cumulative_pnl"]
        return [dict(zip(cols, row)) for row in result]
    except Exception:
        return []


@app.get("/api/cycles")
async def get_cycles():
    """Dados dos ciclos do Excel para tabela."""
    try:
        from openpyxl import load_workbook
        path = Path("./data/cycle_data.xlsx")
        if not path.exists():
            return []
        wb = load_workbook(path, read_only=True)
        ws = wb.active
        headers = [cell.value for cell in ws[1]]
        rows = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            rows.append(dict(zip(headers, row)))
        wb.close()
        return rows[-50:]  # Últimos 50 ciclos
    except Exception:
        return []


@app.get("/api/daily")
async def get_daily():
    """Stats diárias."""
    if not _engine:
        return []
    try:
        result = _engine.storage.conn.execute("""
            SELECT * FROM daily_stats ORDER BY date DESC LIMIT 30
        """).fetchall()
        if not result:
            return []
        cols = [d[0] for d in _engine.storage.conn.description]
        return [dict(zip(cols, row)) for row in result]
    except Exception:
        return []


@app.post("/api/pause")
async def pause_bot():
    if _engine:
        _engine.running = False
        return {"status": "paused"}
    return {"error": "Engine not initialized"}


@app.post("/api/resume")
async def resume_bot():
    if _engine:
        _engine.running = True
        return {"status": "resumed"}
    return {"error": "Engine not initialized"}
