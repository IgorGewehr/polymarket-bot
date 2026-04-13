"""
Cliente de execução de ordens no Polymarket CLOB.
Usa py-clob-client com autenticação EIP-712 (private key + proxy wallet).
Suporta dry-run para testes sem dinheiro real.
"""
import asyncio
import time
import structlog
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs
from config.settings import (
    POLYMARKET_CLOB_URL, POLYMARKET_PRIVATE_KEY,
    POLYMARKET_PROXY_ADDRESS, POLYMARKET_CHAIN_ID,
    DRY_RUN
)

log = structlog.get_logger()


class OrderClient:
    """Cliente para execução de ordens no Polymarket via py-clob-client."""

    def __init__(self):
        self.dry_run = DRY_RUN
        self._clob: ClobClient | None = None

    async def initialize(self):
        """Inicializa o ClobClient com credenciais da carteira."""
        if self.dry_run:
            log.info("order_client_init", mode="dry_run")
            return

        if not POLYMARKET_PRIVATE_KEY:
            raise RuntimeError("POLYMARKET_PRIVATE_KEY não configurada no .env")

        loop = asyncio.get_running_loop()

        def _init():
            client = ClobClient(
                POLYMARKET_CLOB_URL,
                key=POLYMARKET_PRIVATE_KEY,
                chain_id=POLYMARKET_CHAIN_ID,
                signature_type=2,
                funder=POLYMARKET_PROXY_ADDRESS or None,
            )
            client.set_api_creds(client.create_or_derive_api_creds())
            return client

        self._clob = await loop.run_in_executor(None, _init)
        log.info("order_client_init", mode="live", chain_id=POLYMARKET_CHAIN_ID)

    def _ensure_clob(self) -> ClobClient:
        if self._clob is None:
            raise RuntimeError("OrderClient não inicializado — chame initialize() primeiro")
        return self._clob

    async def place_order(
        self,
        token_id: str,
        side: str,        # "BUY" ou "SELL"
        price: float,
        size: float,       # Quantidade de shares (amount / price)
        fee_rate_bps: int = 0,
    ) -> dict | None:
        """
        Coloca uma limit order GTC no CLOB e verifica fill.

        Após postar, poll get_order por até 5s para confirmar MATCHED.
        Se não fill, cancela a ordem e retorna None.
        """
        # Garantir mínimo de 5 shares para BUY (Polymarket minimum_order_size)
        if side.upper() == "BUY":
            size = max(size, 5.0)

        if self.dry_run:
            order = {
                "id": f"dry-{int(time.time()*1000)}",
                "token_id": token_id,
                "side": side,
                "price": price,
                "size": size,
                "status": "MATCHED",
                "dry_run": True,
                "timestamp": time.time()
            }
            log.info("dry_run_order",
                     side=side,
                     price=f"${price:.4f}",
                     size=f"{size:.2f}",
                     cost=f"${price * size:.2f}")
            return order

        try:
            clob = self._ensure_clob()

            order_args = OrderArgs(
                token_id=token_id,
                price=round(price, 2),
                size=round(size, 2),
                side=side.upper(),
                fee_rate_bps=fee_rate_bps,
            )

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                clob.create_and_post_order,
                order_args,
            )

            log.info("order_placed",
                     order_id=str(result)[:40],
                     side=side,
                     price=price,
                     size=size)

            # ── Verificar fill ──
            order_id = None
            if isinstance(result, dict):
                order_id = result.get("orderID") or result.get("id")

            if order_id:
                if side.upper() == "BUY":
                    # BUY: esperar 15s pelo fill, cancelar se não preencher
                    filled = await self._wait_for_fill(order_id, timeout=15.0)
                    if not filled:
                        log.warning("order_not_filled",
                                    order_id=order_id[:20],
                                    side=side,
                                    price=price)
                        await self.cancel_order(order_id)
                        return None
                    log.info("order_filled",
                             order_id=order_id[:20],
                             side=side,
                             price=price,
                             size=size)
                else:
                    # SELL: NÃO fazer fill check — deixar no book como GTC
                    # Profit sells e stop loss sells ficam esperando fill
                    log.info("sell_order_posted",
                             order_id=order_id[:20],
                             side=side,
                             price=price,
                             size=size)

            return result

        except Exception as e:
            log.error("order_failed", error=str(e))
            return None

    async def _wait_for_fill(self, order_id: str, timeout: float = 5.0) -> bool:
        """Poll get_order até fill ou timeout. Retorna True se MATCHED."""
        clob = self._ensure_clob()
        loop = asyncio.get_running_loop()
        start = time.time()

        while time.time() - start < timeout:
            try:
                order = await loop.run_in_executor(
                    None, clob.get_order, order_id
                )
                status = ""
                if isinstance(order, dict):
                    status = order.get("status", "")
                    size_matched = float(order.get("size_matched", 0))
                elif hasattr(order, "status"):
                    status = order.status
                    size_matched = float(getattr(order, "size_matched", 0))
                else:
                    status = str(order)
                    size_matched = 0

                if status == "MATCHED" or size_matched > 0:
                    return True
                if status in ("CANCELLED", "EXPIRED"):
                    return False
            except Exception:
                pass
            await asyncio.sleep(0.5)

        return False

    async def cancel_order(self, order_id: str) -> bool:
        """Cancela uma ordem pendente."""
        if self.dry_run:
            log.info("dry_run_cancel", order_id=order_id)
            return True

        try:
            clob = self._ensure_clob()
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, clob.cancel, order_id)
            log.info("order_cancelled", order_id=order_id)
            return bool(result)
        except Exception as e:
            log.error("cancel_failed", order_id=order_id, error=str(e))
            return False

    async def get_positions(self) -> list[dict]:
        """Lista posições abertas."""
        if self.dry_run:
            return []

        try:
            clob = self._ensure_clob()
            loop = asyncio.get_running_loop()
            positions = await loop.run_in_executor(None, clob.get_positions)
            return positions if isinstance(positions, list) else []
        except Exception as e:
            log.error("positions_error", error=str(e))
            return []

    async def get_midpoint(self, token_id: str) -> float | None:
        """Retorna o preço médio (midpoint) de um token."""
        if self.dry_run:
            return None

        try:
            clob = self._ensure_clob()
            loop = asyncio.get_running_loop()
            midpoint = await loop.run_in_executor(None, clob.get_midpoint, token_id)
            return float(midpoint)
        except Exception as e:
            log.error("midpoint_error", error=str(e))
            return None

    async def close(self):
        """Libera recursos."""
        self._clob = None


# Fee rate para mercados 5min BTC (dynamic fees do Polymarket)
MAKER_FEE_BPS = 1000  # 10% — obtido de /markets/{condition_id}.maker_base_fee


async def execute_trade(
    client: OrderClient,
    token_id: str,
    direction: str,
    amount: float,
    price: float
) -> dict | None:
    """
    Executa um trade completo.

    Args:
        client: OrderClient
        token_id: Token ID do mercado
        direction: "Up" (compra YES) ou "Down" (compra NO)
        amount: Valor em dólares ($1-$3)
        price: Preço por share

    Returns:
        Detalhes da ordem
    """
    if price <= 0 or price >= 1:
        log.warning("invalid_price", price=price)
        return None

    size = amount / price  # Quantidade de shares

    result = await client.place_order(
        token_id=token_id,
        side="BUY",
        price=price,
        size=round(size, 2),
        fee_rate_bps=MAKER_FEE_BPS,
    )

    return result


async def execute_hedge(
    client: OrderClient,
    token_id: str,
    amount: float,
    price: float
) -> dict | None:
    """Executa um hedge (compra share do lado oposto)."""
    return await execute_trade(client, token_id, "hedge", amount, price)


async def execute_sell(
    client: OrderClient,
    token_id: str,
    shares: float,
    price: float,
) -> dict | None:
    """Vende shares para sair da posição (take profit / stop loss)."""
    if price <= 0 or price >= 1 or shares < 1.0:
        log.warning("invalid_sell", price=price, shares=shares)
        return None

    result = await client.place_order(
        token_id=token_id,
        side="SELL",
        price=price,
        size=round(shares, 2),
        fee_rate_bps=MAKER_FEE_BPS,
    )
    return result


async def execute_lock(
    client: OrderClient,
    token_id: str,
    price: float,
    shares: float,
) -> dict | None:
    """Compra lado oposto para lock profit garantido."""
    if price <= 0 or price >= 1:
        return None

    result = await client.place_order(
        token_id=token_id,
        side="BUY",
        price=round(price, 2),
        size=max(round(shares, 2), 5.0),
        fee_rate_bps=MAKER_FEE_BPS,
    )
    return result
