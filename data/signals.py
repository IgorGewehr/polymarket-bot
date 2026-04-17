"""
Real-time signal feeds for BTC 5-minute trading.

1. ChainlinkFeed    -- Oracle price that resolves the market
2. VolumeImbalanceFeed -- Binance taker buy/sell ratio
3. LiquidationFeed  -- Binance futures forced liquidations
4. SignalAggregator  -- Combines all signals into a single score
"""
import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

import orjson
import structlog
import websockets

from config.settings import (
    CHAINLINK_MIN_DELTA_PCT,
    VOLUME_IMBALANCE_THRESHOLD,
    LIQUIDATION_CASCADE_THRESHOLD,
    MIN_SIGNAL_SCORE,
)

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _backoff(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
    """Exponential backoff with cap."""
    return min(base * (2 ** attempt), cap)


@dataclass
class _TimestampedValue:
    timestamp: float
    value: float
    side: str = ""          # used by liquidation feed


# ---------------------------------------------------------------------------
# 1. ChainlinkFeed
# ---------------------------------------------------------------------------

class ChainlinkFeed:
    """
    Polymarket live-data WS -- Chainlink BTC/USD oracle price.

    This is the price that actually resolves the 5-min market, so it is the
    single strongest signal available.
    """

    WS_URL = "wss://ws-live-data.polymarket.com"
    PING_INTERVAL = 5  # seconds -- literal text "PING", not WS ping frame

    def __init__(self):
        self.price: float = 0.0
        self.cycle_open_price: float = 0.0
        self.delta_pct: float = 0.0
        self.direction: str | None = None
        self._last_update: float = 0.0
        self._running = False
        self._ws = None

    # -- public API --

    def reset_cycle(self):
        """Call at the start of each 5-min window."""
        self.cycle_open_price = self.price if self.price > 0 else 0.0
        self.delta_pct = 0.0
        self.direction = None

    # -- connection --

    async def connect(self):
        self._running = True
        attempt = 0
        while self._running:
            try:
                async with websockets.connect(
                    self.WS_URL,
                    ping_interval=None,   # we handle keep-alive manually
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    attempt = 0
                    log.info("chainlink_connected")

                    # Subscribe
                    sub = orjson.dumps({
                        "action": "subscribe",
                        "subscriptions": [{
                            "topic": "crypto_prices_chainlink",
                            "type": "*",
                            "filters": '{"symbol":"btc/usd"}',
                        }],
                    }).decode()
                    await ws.send(sub)

                    # Launch keep-alive pinger in background
                    ping_task = asyncio.create_task(self._ping_loop(ws))

                    try:
                        async for msg in ws:
                            if not self._running:
                                break
                            self._process(msg)
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

            except (websockets.ConnectionClosed, OSError) as e:
                log.warning("chainlink_disconnected", error=str(e))
            except Exception as e:
                log.error("chainlink_error", error=str(e))

            if not self._running:
                break
            wait = _backoff(attempt)
            log.info("chainlink_reconnecting", wait=f"{wait:.1f}s")
            await asyncio.sleep(wait)
            attempt += 1

    async def disconnect(self):
        self._running = False
        if self._ws:
            await self._ws.close()

    # -- internals --

    async def _ping_loop(self, ws):
        """Send literal text 'PING' every PING_INTERVAL seconds."""
        while True:
            await asyncio.sleep(self.PING_INTERVAL)
            try:
                await ws.send("PING")
            except Exception:
                return  # connection gone -- outer loop will reconnect

    def _process(self, raw: str):
        if not raw or raw.strip() == "":
            return  # empty PONG
        try:
            data = orjson.loads(raw)
        except Exception:
            return

        # Format 1: {"payload": {"value": 74897.06, ...}, "topic": "crypto_prices_chainlink"}
        payload = data.get("payload")
        if isinstance(payload, dict):
            value = payload.get("value")
            if value is not None:
                try:
                    price = float(value)
                    if price > 0:
                        self.price = price
                        self._last_update = time.time()
                        if self.cycle_open_price == 0.0:
                            self.cycle_open_price = price
                        if self.cycle_open_price > 0:
                            self.delta_pct = ((price - self.cycle_open_price) / self.cycle_open_price) * 100
                            if abs(self.delta_pct) >= CHAINLINK_MIN_DELTA_PCT:
                                self.direction = "Up" if self.delta_pct > 0 else "Down"
                            else:
                                self.direction = None
                except (ValueError, TypeError):
                    pass
                return

            # Format 2: {"payload": {"data": [{"value": ...}, ...]}} -- initial batch
            inner = payload.get("data")
            if isinstance(inner, list) and inner:
                value = inner[-1].get("value")  # Last = most recent
                if value is not None:
                    try:
                        price = float(value)
                        if price > 0:
                            self.price = price
                            self._last_update = time.time()
                            if self.cycle_open_price == 0.0:
                                self.cycle_open_price = price
                    except (ValueError, TypeError):
                        pass
                return

        value = data.get("value") or data.get("price")
        if value is None:
            return

        try:
            price = float(value)
        except (ValueError, TypeError):
            return

        if price <= 0:
            return

        self.price = price
        self._last_update = time.time()

        # Set cycle open on first tick if not yet set
        if self.cycle_open_price == 0.0:
            self.cycle_open_price = price

        # Compute delta
        if self.cycle_open_price > 0:
            self.delta_pct = (price - self.cycle_open_price) / self.cycle_open_price * 100
            self.direction = "Up" if self.delta_pct > 0 else ("Down" if self.delta_pct < 0 else None)


# ---------------------------------------------------------------------------
# 2. VolumeImbalanceFeed
# ---------------------------------------------------------------------------

class VolumeImbalanceFeed:
    """
    Binance aggTrade stream -- taker buy/sell volume ratio over rolling windows.

    imbalance = (buy_vol - sell_vol) / (buy_vol + sell_vol)
    Range: -1.0 (all sells) to +1.0 (all buys)
    """

    WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"

    def __init__(self):
        # Deques of _TimestampedValue with side="buy"|"sell"
        self._trades: deque[_TimestampedValue] = deque()

        # Exposed state
        self.imbalance_30s: float = 0.0
        self.imbalance_60s: float = 0.0
        self.buy_volume_30s: float = 0.0
        self.sell_volume_30s: float = 0.0

        self._running = False
        self._ws = None

    async def connect(self):
        self._running = True
        attempt = 0
        while self._running:
            try:
                async with websockets.connect(
                    self.WS_URL,
                    ping_interval=20,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    attempt = 0
                    log.info("volume_imbalance_connected")

                    async for msg in ws:
                        if not self._running:
                            break
                        self._process(msg)

            except (websockets.ConnectionClosed, OSError) as e:
                log.warning("volume_imbalance_disconnected", error=str(e))
            except Exception as e:
                log.error("volume_imbalance_error", error=str(e))

            if not self._running:
                break
            wait = _backoff(attempt)
            log.info("volume_imbalance_reconnecting", wait=f"{wait:.1f}s")
            await asyncio.sleep(wait)
            attempt += 1

    async def disconnect(self):
        self._running = False
        if self._ws:
            await self._ws.close()

    def _process(self, raw: str):
        try:
            data = orjson.loads(raw)
        except Exception:
            return

        qty_str = data.get("q")
        is_seller_maker = data.get("m")
        if qty_str is None or is_seller_maker is None:
            return

        try:
            qty = float(qty_str)
        except (ValueError, TypeError):
            return

        # m = true  -> taker is seller (sell pressure)
        # m = false -> taker is buyer  (buy pressure)
        side = "sell" if is_seller_maker else "buy"

        now = time.time()
        self._trades.append(_TimestampedValue(timestamp=now, value=qty, side=side))

        # Prune trades older than 60s
        cutoff_60 = now - 60
        while self._trades and self._trades[0].timestamp < cutoff_60:
            self._trades.popleft()

        # Calculate rolling windows
        cutoff_30 = now - 30
        buy_30 = 0.0
        sell_30 = 0.0
        buy_60 = 0.0
        sell_60 = 0.0

        for t in self._trades:
            if t.side == "buy":
                buy_60 += t.value
                if t.timestamp >= cutoff_30:
                    buy_30 += t.value
            else:
                sell_60 += t.value
                if t.timestamp >= cutoff_30:
                    sell_30 += t.value

        self.buy_volume_30s = buy_30
        self.sell_volume_30s = sell_30

        total_30 = buy_30 + sell_30
        total_60 = buy_60 + sell_60

        self.imbalance_30s = (buy_30 - sell_30) / total_30 if total_30 > 0 else 0.0
        self.imbalance_60s = (buy_60 - sell_60) / total_60 if total_60 > 0 else 0.0


# ---------------------------------------------------------------------------
# 3. LiquidationFeed
# ---------------------------------------------------------------------------

class LiquidationFeed:
    """
    Binance futures forceOrder stream -- forced liquidations.

    Tracks rolling 30s liquidation volume (USD) for longs and shorts.
    Detects cascade events when total liquidations exceed threshold.
    """

    WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"

    def __init__(self):
        self._liqs: deque[_TimestampedValue] = deque()

        # Exposed state
        self.long_liqs_30s: float = 0.0   # USD -- longs liquidated (bearish)
        self.short_liqs_30s: float = 0.0  # USD -- shorts liquidated (bullish)
        self.cascade_direction: str | None = None   # "Up", "Down", or None
        self.cascade_magnitude: float = 0.0

        self._running = False
        self._ws = None

    async def connect(self):
        self._running = True
        attempt = 0
        while self._running:
            try:
                async with websockets.connect(
                    self.WS_URL,
                    ping_interval=20,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    attempt = 0
                    log.info("liquidation_feed_connected")

                    async for msg in ws:
                        if not self._running:
                            break
                        self._process(msg)

            except (websockets.ConnectionClosed, OSError) as e:
                log.warning("liquidation_feed_disconnected", error=str(e))
            except Exception as e:
                log.error("liquidation_feed_error", error=str(e))

            if not self._running:
                break
            wait = _backoff(attempt)
            log.info("liquidation_feed_reconnecting", wait=f"{wait:.1f}s")
            await asyncio.sleep(wait)
            attempt += 1

    async def disconnect(self):
        self._running = False
        if self._ws:
            await self._ws.close()

    def _process(self, raw: str):
        try:
            data = orjson.loads(raw)
        except Exception:
            return

        # The stream sends a wrapper; the order data is in "o".
        order = data.get("o")
        if not order:
            return

        symbol = order.get("s", "")
        if symbol != "BTCUSDT":
            return

        side = order.get("S", "")       # "BUY" or "SELL"
        qty_str = order.get("q", "0")
        price_str = order.get("p", "0")

        try:
            qty = float(qty_str)
            price = float(price_str)
        except (ValueError, TypeError):
            return

        usd_value = qty * price
        if usd_value <= 0:
            return

        # side "BUY"  -> shorts being liquidated (bullish for price)
        # side "SELL" -> longs being liquidated  (bearish for price)
        liq_side = "short" if side == "BUY" else "long"

        now = time.time()
        self._liqs.append(_TimestampedValue(timestamp=now, value=usd_value, side=liq_side))

        # Prune older than 30s
        cutoff = now - 30
        while self._liqs and self._liqs[0].timestamp < cutoff:
            self._liqs.popleft()

        # Recalculate
        long_total = 0.0
        short_total = 0.0
        for liq in self._liqs:
            if liq.side == "long":
                long_total += liq.value
            else:
                short_total += liq.value

        self.long_liqs_30s = long_total
        self.short_liqs_30s = short_total

        total = long_total + short_total
        if total >= LIQUIDATION_CASCADE_THRESHOLD:
            if short_total > long_total:
                self.cascade_direction = "Up"    # shorts squeezed -> price up
            else:
                self.cascade_direction = "Down"  # longs rekt -> price down
            self.cascade_magnitude = total
        else:
            self.cascade_direction = None
            self.cascade_magnitude = 0.0


# ---------------------------------------------------------------------------
# 4. SignalAggregator
# ---------------------------------------------------------------------------

@dataclass
class AggregatedSignal:
    direction: str | None       # "Up", "Down", or None (skip)
    confidence: float           # 0.0 to 1.0
    score: int                  # raw score before threshold
    max_score: int              # theoretical max from active signals
    signals: dict = field(default_factory=dict)


class SignalAggregator:
    """
    Combines ChainlinkFeed, VolumeImbalanceFeed, LiquidationFeed and BTC
    slope direction into a single directional score.

    Usage:
        agg = SignalAggregator(chainlink, volume, liquidation)
        signal = agg.evaluate(btc_slope_direction="Up")
    """

    def __init__(
        self,
        chainlink: ChainlinkFeed,
        volume: VolumeImbalanceFeed,
        liquidation: LiquidationFeed,
    ):
        self.chainlink = chainlink
        self.volume = volume
        self.liquidation = liquidation

    def evaluate(self, btc_slope_direction: str | None = None) -> AggregatedSignal:
        score = 0
        max_score = 0
        signals: dict = {}

        # --- Chainlink delta (weight 3) --- strongest signal ---
        if abs(self.chainlink.delta_pct) > CHAINLINK_MIN_DELTA_PCT:
            contribution = 3 if self.chainlink.direction == "Up" else -3
            score += contribution
            max_score += 3
            signals["chainlink"] = {
                "direction": self.chainlink.direction,
                "delta_pct": round(self.chainlink.delta_pct, 4),
                "weight": contribution,
            }

        # --- Volume imbalance (weight 2) ---
        if abs(self.volume.imbalance_30s) > VOLUME_IMBALANCE_THRESHOLD:
            contribution = 2 if self.volume.imbalance_30s > 0 else -2
            score += contribution
            max_score += 2
            signals["volume"] = {
                "imbalance_30s": round(self.volume.imbalance_30s, 4),
                "imbalance_60s": round(self.volume.imbalance_60s, 4),
                "weight": contribution,
            }

        # --- Liquidation cascade (weight 2) ---
        if self.liquidation.cascade_direction:
            contribution = 2 if self.liquidation.cascade_direction == "Up" else -2
            score += contribution
            max_score += 2
            signals["liquidation"] = {
                "cascade_direction": self.liquidation.cascade_direction,
                "magnitude": round(self.liquidation.cascade_magnitude, 2),
                "long_liqs_30s": round(self.liquidation.long_liqs_30s, 2),
                "short_liqs_30s": round(self.liquidation.short_liqs_30s, 2),
                "weight": contribution,
            }

        # --- BTC slope (weight 1) ---
        if btc_slope_direction:
            contribution = 1 if btc_slope_direction == "Up" else -1
            score += contribution
            max_score += 1
            signals["btc_slope"] = {
                "direction": btc_slope_direction,
                "weight": contribution,
            }

        # --- Decision ---
        if max_score == 0:
            return AggregatedSignal(
                direction=None, confidence=0.0,
                score=0, max_score=0, signals=signals,
            )

        if score >= MIN_SIGNAL_SCORE:
            direction = "Up"
            confidence = score / max_score
        elif score <= -MIN_SIGNAL_SCORE:
            direction = "Down"
            confidence = abs(score) / max_score
        else:
            direction = None
            confidence = 0.0

        # Clamp confidence to [0, 1]
        confidence = min(confidence, 1.0)

        return AggregatedSignal(
            direction=direction,
            confidence=confidence,
            score=score,
            max_score=max_score,
            signals=signals,
        )
