"""
Storage em DuckDB para logs de trades e dados históricos.
Otimizado para queries analíticas e backtesting futuro.
"""
import duckdb
import time
import structlog
from config.settings import DUCKDB_PATH

log = structlog.get_logger()


class Storage:
    def __init__(self, path: str = DUCKDB_PATH):
        self.path = path
        self.conn = None

    def connect(self):
        self.conn = duckdb.connect(self.path)
        self._create_tables()
        log.info("duckdb_connected", path=self.path)

    def _create_tables(self):
        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS trade_seq START 1
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY DEFAULT(nextval('trade_seq')),
                timestamp DOUBLE,
                market_id VARCHAR,
                direction VARCHAR,
                bet_size DOUBLE,
                entry_price DOUBLE,
                entry_time_remaining DOUBLE,
                confidence_score DOUBLE,
                layer1_trend DOUBLE,
                layer2_alignment DOUBLE,
                layer3_bollinger DOUBLE,
                layer4_momentum DOUBLE,
                delta_at_entry DOUBLE,
                btc_price_at_entry DOUBLE,
                expected_return DOUBLE,
                regime VARCHAR,
                hedge_executed BOOLEAN DEFAULT FALSE,
                hedge_cost DOUBLE,
                hedge_return DOUBLE,
                result VARCHAR,
                pnl DOUBLE,
                resolution_price DOUBLE
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ticks (
                timestamp DOUBLE,
                source VARCHAR,
                price DOUBLE,
                delta DOUBLE
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date VARCHAR PRIMARY KEY,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                total_pnl DOUBLE DEFAULT 0,
                max_drawdown DOUBLE DEFAULT 0,
                hedges_used INTEGER DEFAULT 0,
                lateral_cycles_skipped INTEGER DEFAULT 0
            )
        """)

    def log_trade(self, trade: dict):
        cols = ", ".join(trade.keys())
        placeholders = ", ".join(["?"] * len(trade))
        self.conn.execute(
            f"INSERT INTO trades ({cols}) VALUES ({placeholders})",
            list(trade.values())
        )

    def log_tick(self, source: str, price: float, delta: float = 0.0):
        self.conn.execute(
            "INSERT INTO ticks VALUES (?, ?, ?, ?)",
            [time.time(), source, price, delta]
        )

    def get_recent_trades(self, n: int = 50) -> list[dict]:
        result = self.conn.execute(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", [n]
        )
        cols = [d[0] for d in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]

    def get_daily_stats(self, date: str) -> dict | None:
        result = self.conn.execute(
            "SELECT * FROM daily_stats WHERE date = ?", [date]
        ).fetchone()
        if result:
            cols = [d[0] for d in self.conn.description]
            return dict(zip(cols, result))
        return None

    def update_daily_stats(self, date: str, stats: dict):
        existing = self.get_daily_stats(date)
        if existing:
            sets = ", ".join(f"{k} = ?" for k in stats.keys())
            self.conn.execute(
                f"UPDATE daily_stats SET {sets} WHERE date = ?",
                list(stats.values()) + [date]
            )
        else:
            stats["date"] = date
            cols = ", ".join(stats.keys())
            placeholders = ", ".join(["?"] * len(stats))
            self.conn.execute(
                f"INSERT INTO daily_stats ({cols}) VALUES ({placeholders})",
                list(stats.values())
            )

    def get_win_rate_by_direction(self, direction: str, n: int = 100) -> float:
        result = self.conn.execute("""
            SELECT COUNT(*) FILTER (WHERE result = 'WIN') * 1.0 / COUNT(*)
            FROM (SELECT * FROM trades WHERE direction = ? ORDER BY timestamp DESC LIMIT ?)
        """, [direction, n]).fetchone()
        return result[0] if result and result[0] else 0.5

    def close(self):
        if self.conn:
            self.conn.close()
