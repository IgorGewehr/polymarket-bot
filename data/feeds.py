"""
Feeds de dados em tempo real via WebSocket.
- Polymarket: preço das shares YES/NO do mercado ativo
- Binance: preço spot do BTC para análise multi-timeframe
"""
import asyncio
import time
import orjson
import websockets
import httpx
import structlog

from config.settings import (
    POLYMARKET_CLOB_URL, GAMMA_API_URL, POLYMARKET_WS, BINANCE_WS,
    TICK_INTERVAL
)
from data.price_buffer import PriceBuffer

log = structlog.get_logger()


class BinanceFeed:
    """Feed de preço BTC spot via WebSocket da Binance."""

    def __init__(self, buffer: PriceBuffer):
        self.buffer = buffer
        self.ws = None
        self._running = False
        self.last_price: float = 0.0

    async def connect(self):
        self._running = True
        while self._running:
            try:
                async with websockets.connect(BINANCE_WS) as ws:
                    self.ws = ws
                    log.info("binance_connected")
                    async for msg in ws:
                        if not self._running:
                            break
                        data = orjson.loads(msg)
                        price = float(data["p"])
                        self.last_price = price
                        self.buffer.append(
                            timestamp=time.time(),
                            price=price
                        )
            except (websockets.ConnectionClosed, OSError) as e:
                log.warning("binance_disconnected", error=str(e))
                await asyncio.sleep(2)
            except Exception as e:
                log.error("binance_error", error=str(e))
                await asyncio.sleep(5)

    async def disconnect(self):
        self._running = False
        if self.ws:
            await self.ws.close()


class PolymarketFeed:
    """Feed de preços de shares do mercado ativo no Polymarket."""

    def __init__(self, buffer: PriceBuffer):
        self.buffer = buffer
        self.ws = None
        self._running = False
        self.up_token_id: str | None = None
        self.down_token_id: str | None = None
        self.current_market_id: str | None = None
        self.yes_price: float = 0.0
        self.no_price: float = 0.0

    async def connect(self, market_token_id: str, down_token_id: str | None = None):
        """Conecta ao WebSocket do Polymarket para um mercado específico."""
        self.up_token_id = market_token_id
        self.down_token_id = down_token_id
        self.current_market_id = market_token_id
        self._running = True

        while self._running:
            try:
                async with websockets.connect(
                    POLYMARKET_WS,
                    ping_interval=20,
                    close_timeout=5
                ) as ws:
                    self.ws = ws
                    # Subscribe em ambos YES e NO tokens para preços reais
                    asset_ids = [market_token_id]
                    if down_token_id:
                        asset_ids.append(down_token_id)
                    sub_msg = orjson.dumps({
                        "type": "market",
                        "assets_ids": asset_ids
                    }).decode()
                    await ws.send(sub_msg)
                    log.info("polymarket_subscribed",
                             yes_token=market_token_id[:16],
                             no_token=(down_token_id or "none")[:16])

                    async for msg in ws:
                        if not self._running:
                            break
                        self._process_message(msg)

            except (websockets.ConnectionClosed, OSError) as e:
                log.warning("polymarket_disconnected", error=str(e))
                await asyncio.sleep(2)
            except Exception as e:
                log.error("polymarket_error", error=str(e))
                await asyncio.sleep(5)

    def _process_message(self, raw: str):
        try:
            data = orjson.loads(raw)

            # Formato: lista de orderbook snapshots (primeira mensagem)
            if isinstance(data, list):
                for item in data:
                    asset_id = item.get("asset_id", "")
                    bids = item.get("bids", [])
                    if bids:
                        best_bid = max(float(b["price"]) for b in bids)
                        if 0 < best_bid < 1:
                            self._update_price(asset_id, best_bid)
                return

            # Formato: price_changes (atualizações incrementais)
            changes = data.get("price_changes", [])
            for ch in changes:
                asset_id = ch.get("asset_id", "")
                best_bid = ch.get("best_bid")
                if best_bid:
                    price = float(best_bid)
                    if 0 < price < 1:
                        self._update_price(asset_id, price)
                        return

        except Exception:
            pass

    def _update_price(self, asset_id: str, price: float):
        """Atualiza preços com base no token. Usa dados reais, não derivados."""
        if asset_id == self.up_token_id:
            self.yes_price = price
            # Só deriva NO se não temos dados reais do NO ainda
            if self.no_price <= 0:
                self.no_price = 1 - price
        elif asset_id == self.down_token_id:
            self.no_price = price
            # Só deriva YES se não temos dados reais do YES ainda
            if self.yes_price <= 0:
                self.yes_price = 1 - price
        else:
            self.yes_price = price
            self.no_price = 1 - price
        self.buffer.append(timestamp=time.time(), price=self.yes_price)

    async def disconnect(self):
        self._running = False
        if self.ws:
            await self.ws.close()

    async def switch_market(self, up_token_id: str, down_token_id: str | None = None):
        """Troca para um novo mercado."""
        await self.disconnect()
        await asyncio.sleep(0.5)
        await self.connect(up_token_id, down_token_id)


class PolymarketREST:
    """Cliente REST para descoberta de mercados (Gamma API) e dados do CLOB."""

    def __init__(self):
        # Gamma API para descoberta de mercados (pública, sem auth)
        self.gamma_client = httpx.AsyncClient(
            base_url=GAMMA_API_URL,
            timeout=10.0,
            http2=True
        )
        # CLOB API para orderbook e midpoint
        self.clob_client = httpx.AsyncClient(
            base_url=POLYMARKET_CLOB_URL,
            timeout=10.0,
            http2=True
        )

    async def get_markets(self, keyword: str = "Bitcoin") -> list[dict]:
        """Busca mercados BTC 5min ativos via slug dinâmico na Gamma API."""
        import time as _time
        now = int(_time.time())
        markets = []

        # Buscar janela atual e próxima via slug determinístico
        for offset in [0, 300]:
            window_ts = ((now + offset) // 300) * 300
            slug = f"btc-updown-5m-{window_ts}"
            try:
                resp = await self.gamma_client.get("/events", params={"slug": slug})
                resp.raise_for_status()
                events = resp.json()
                for event in events:
                    for m in event.get("markets", []):
                        # Converter endDateIso para end_date_iso + calcular end timestamp
                        m["end_date_iso"] = f"{m.get('endDateIso', '')}T00:00:00Z"
                        # O end real é window_ts + 300 (fim da janela de 5min)
                        m["_window_end_ts"] = window_ts + 300
                        markets.append(m)
            except Exception as e:
                log.error("markets_fetch_error", slug=slug, error=str(e))

        return markets

    async def get_market(self, condition_id: str) -> dict | None:
        """Busca detalhes de um mercado específico via Gamma API."""
        try:
            resp = await self.gamma_client.get("/markets", params={
                "conditionId": condition_id,
                "limit": 1,
            })
            resp.raise_for_status()
            markets = resp.json()
            if isinstance(markets, list) and markets:
                return markets[0]
            return None
        except Exception as e:
            log.error("market_fetch_error", error=str(e))
            return None

    async def get_orderbook(self, token_id: str) -> dict | None:
        """Busca order book de um token via CLOB API."""
        try:
            resp = await self.clob_client.get("/book", params={
                "token_id": token_id
            })
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error("orderbook_error", error=str(e))
            return None

    async def get_midpoint(self, token_id: str) -> float | None:
        """Retorna o preço médio (midpoint) de um token via CLOB API."""
        try:
            resp = await self.clob_client.get("/midpoint", params={
                "token_id": token_id
            })
            resp.raise_for_status()
            data = resp.json()
            return float(data.get("mid", 0))
        except Exception as e:
            log.error("midpoint_error", error=str(e))
            return None

    async def get_best_ask(self, token_id: str) -> float | None:
        """Retorna o melhor ask (menor oferta de venda) para um token."""
        book = await self.get_orderbook(token_id)
        if not book:
            return None
        asks = book.get("asks", [])
        if not asks:
            return None
        try:
            best = min(float(a["price"]) for a in asks)
            return best if 0 < best < 1 else None
        except (ValueError, KeyError):
            return None

    async def close(self):
        await self.gamma_client.aclose()
        await self.clob_client.aclose()
