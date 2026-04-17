"""
Cliente de execução — Estratégia "O Disciplinado"

Simplificado vs anterior:
- SÓ BUY orders (sem SELL — hold to resolution)
- Fill verification robusta (anti-ghost orders)
- Retry com preço ajustado se não fill
- Tratamento de allowance errors
"""
import asyncio
import time
import structlog
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from config.settings import (
    POLYMARKET_CLOB_URL, POLYMARKET_PRIVATE_KEY,
    POLYMARKET_PROXY_ADDRESS, POLYMARKET_CHAIN_ID,
    DRY_RUN, MAKER_FILL_TIMEOUT
)

log = structlog.get_logger()

# Fee rate para mercados 5min BTC
MAKER_FEE_BPS = 1000  # 10%


class OrderClient:
    def __init__(self):
        self.dry_run = DRY_RUN
        self._clob: ClobClient | None = None

    async def initialize(self):
        """Inicializa o ClobClient com credenciais."""
        if self.dry_run:
            log.info("order_client_init", mode="dry_run")
            return

        if not POLYMARKET_PRIVATE_KEY:
            raise RuntimeError("POLYMARKET_PRIVATE_KEY não configurada")

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
            raise RuntimeError("OrderClient não inicializado")
        return self._clob

    async def place_buy_order(
        self,
        token_id: str,
        price: float,
        size: float,
    ) -> dict | None:
        """
        Coloca BUY order e VERIFICA fill rigorosamente.

        Anti-ghost orders:
        1. Posta a ordem
        2. Poll por 15s verificando MATCHED status
        3. Se não fill → cancela e retorna None
        4. SÓ retorna dict se tiver CERTEZA que foi filled

        Retorna dict com campos: id, size, price, status
        """
        # Mínimo 5 shares (Polymarket minimum)
        size = max(size, 5.0)

        if self.dry_run:
            order = {
                "id": f"dry-{int(time.time()*1000)}",
                "token_id": token_id,
                "side": "BUY",
                "price": price,
                "size": size,
                "status": "MATCHED",
                "size_matched": size,
                "dry_run": True,
            }
            log.info("dry_run_buy",
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
                side="BUY",
                fee_rate_bps=MAKER_FEE_BPS,
            )

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, clob.create_and_post_order, order_args,
            )

            # Extrair order_id
            order_id = None
            if isinstance(result, dict):
                order_id = result.get("orderID") or result.get("id")

            if not order_id:
                log.error("no_order_id_returned", result=str(result)[:100])
                return None

            log.info("buy_order_posted",
                     order_id=order_id[:20],
                     price=f"${price:.2f}",
                     size=f"{size:.2f}")

            # ── VERIFICAÇÃO RIGOROSA DE FILL (maker = mais tempo) ──
            filled = await self._verify_fill(order_id, timeout=MAKER_FILL_TIMEOUT)

            if not filled:
                log.warning("buy_NOT_filled_cancelling",
                            order_id=order_id[:20],
                            price=f"${price:.2f}")
                await self.cancel_order(order_id)
                return None

            log.info("buy_CONFIRMED_filled",
                     order_id=order_id[:20],
                     price=f"${price:.2f}",
                     size=f"{size:.2f}")

            return {"id": order_id, "price": price, "size": size, "status": "MATCHED"}

        except Exception as e:
            error_msg = str(e).lower()

            # Tratar allowance errors
            if "allowance" in error_msg or "insufficient" in error_msg:
                log.error("allowance_error",
                          error=str(e),
                          msg="Saldo insuficiente ou allowance não aprovada")
            else:
                log.error("buy_order_failed", error=str(e))

            return None

    async def _verify_fill(self, order_id: str, timeout: float = 15.0) -> bool:
        """
        Verifica se order foi filled de verdade.

        Anti-ghost: poll a cada 0.5s por até 15s.
        Retorna True SOMENTE se status == MATCHED ou size_matched > 0.
        """
        clob = self._ensure_clob()
        loop = asyncio.get_running_loop()
        start = time.time()

        while time.time() - start < timeout:
            try:
                order = await loop.run_in_executor(
                    None, clob.get_order, order_id
                )

                status = ""
                size_matched = 0.0

                if isinstance(order, dict):
                    status = order.get("status", "")
                    size_matched = float(order.get("size_matched", 0) or 0)
                elif hasattr(order, "status"):
                    status = order.status
                    size_matched = float(getattr(order, "size_matched", 0) or 0)

                if status == "MATCHED" or size_matched > 0:
                    return True
                if status in ("CANCELLED", "EXPIRED"):
                    log.warning("order_cancelled_or_expired",
                                order_id=order_id[:20], status=status)
                    return False

            except Exception as e:
                log.debug("fill_check_error", error=str(e))

            await asyncio.sleep(0.5)

        return False

    async def cancel_order(self, order_id: str) -> bool:
        """Cancela uma ordem pendente."""
        if self.dry_run:
            log.info("dry_run_cancel", order_id=str(order_id)[:20])
            return True

        try:
            clob = self._ensure_clob()
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, clob.cancel, order_id)
            log.info("order_cancelled", order_id=str(order_id)[:20])
            return bool(result)
        except Exception as e:
            log.error("cancel_failed", order_id=str(order_id)[:20], error=str(e))
            return False

    async def close(self):
        self._clob = None


async def execute_trade(
    client: OrderClient,
    token_id: str,
    direction: str,
    amount: float,
    price: float
) -> dict | None:
    """
    Executa BUY com verificação de fill.
    Retorna dict SOMENTE se filled de verdade.
    """
    if price <= 0 or price >= 1:
        log.warning("invalid_price", price=price)
        return None

    size = amount / price

    return await client.place_buy_order(
        token_id=token_id,
        price=price,
        size=round(size, 2),
    )


async def execute_trade_no(
    client: OrderClient,
    token_id: str,
    amount: float,
    price: float
) -> dict | None:
    """
    Compra NO token para dynamic lock.
    Mesmo que execute_trade mas para o token NO.
    Timeout menor (10s) porque lock precisa ser rápido.
    """
    if price <= 0 or price >= 1:
        log.warning("invalid_no_price", price=price)
        return None

    size = amount / price

    if client.dry_run:
        log.info("dry_run_lock_no", price=f"${price:.4f}", size=f"{size:.2f}")
        return {"id": f"dry-lock-{int(time.time()*1000)}", "status": "MATCHED"}

    try:
        clob = client._ensure_clob()
        order_args = OrderArgs(
            token_id=token_id,
            price=round(price, 2),
            size=max(round(size, 2), 5.0),
            side="BUY",
            fee_rate_bps=MAKER_FEE_BPS,
        )

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, clob.create_and_post_order, order_args,
        )

        order_id = None
        if isinstance(result, dict):
            order_id = result.get("orderID") or result.get("id")

        if not order_id:
            log.error("lock_no_order_id", result=str(result)[:100])
            return None

        # Lock precisa ser rápido — 10s timeout
        filled = await client._verify_fill(order_id, timeout=10.0)
        if not filled:
            log.warning("lock_NOT_filled_cancelling", order_id=order_id[:20])
            await client.cancel_order(order_id)
            return None

        log.info("lock_CONFIRMED_filled",
                 order_id=order_id[:20], price=f"${price:.2f}")
        return {"id": order_id, "price": price, "size": size, "status": "MATCHED"}

    except Exception as e:
        log.error("lock_order_failed", error=str(e))
        return None


async def post_lock_limit(
    client: OrderClient,
    token_id: str,
    price: float,
    shares: float = 5.0,
    expiry_seconds: float = 180.0,
) -> str | None:
    """
    Posta lock order como GTD (expira automaticamente).
    NÃO espera fill — retorna order_id pra monitorar depois.
    Zero fee (maker).
    """
    if price <= 0 or price >= 1 or shares < 1.0:
        return None

    if client.dry_run:
        log.info("dry_run_lock_limit", price=f"${price:.2f}", shares=f"{shares:.2f}")
        return f"dry-lock-{int(time.time()*1000)}"

    try:
        clob = client._ensure_clob()
        import math
        expiry_ts = math.floor(time.time() + 60 + expiry_seconds)

        order_args = OrderArgs(
            token_id=token_id,
            price=round(price, 2),
            size=round(shares, 2),
            side="BUY",
            fee_rate_bps=MAKER_FEE_BPS,
            expiration=expiry_ts,
        )

        loop = asyncio.get_running_loop()
        order = await loop.run_in_executor(
            None, clob.create_order, order_args,
        )
        result = await loop.run_in_executor(
            None, clob.post_order, order, OrderType.GTD,
        )

        order_id = None
        if isinstance(result, dict):
            order_id = result.get("orderID") or result.get("id")

        if order_id:
            log.info("lock_limit_posted",
                     order_id=order_id[:20],
                     price=f"${price:.2f}",
                     expiry=f"{expiry_seconds:.0f}s")
        return order_id

    except Exception as e:
        log.error("lock_limit_failed", error=str(e))
        return None


async def execute_sell(
    client: OrderClient,
    token_id: str,
    shares: float,
    price: float,
) -> dict | None:
    """
    Safety exit SELL — usado SOMENTE no safety exit @1:30.
    Posta GTC sell e verifica fill por 5s.
    Se não fill, cancela (não deixa ghost order).
    """
    # Reduz shares levemente pra evitar erro de saldo por rounding
    shares = round(shares * 0.99, 2)

    if price <= 0 or price >= 1 or shares < 1.0:
        log.warning("invalid_sell", price=price, shares=shares)
        return None

    if client.dry_run:
        log.info("dry_run_sell", price=f"${price:.4f}", shares=f"{shares:.2f}")
        return {"id": f"dry-sell-{int(time.time()*1000)}", "status": "MATCHED"}

    try:
        clob = client._ensure_clob()
        order_args = OrderArgs(
            token_id=token_id,
            price=round(price, 2),
            size=round(shares, 2),
            side="SELL",
            fee_rate_bps=MAKER_FEE_BPS,
        )

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, clob.create_and_post_order, order_args,
        )

        order_id = None
        if isinstance(result, dict):
            order_id = result.get("orderID") or result.get("id")

        if not order_id:
            log.error("sell_no_order_id", result=str(result)[:100])
            return None

        # Verificar fill por 5s — se não fill, CANCELAR (anti-ghost)
        filled = await client._verify_fill(order_id, timeout=5.0)
        if not filled:
            log.warning("sell_NOT_filled_cancelling", order_id=order_id[:20])
            await client.cancel_order(order_id)
            return None

        log.info("sell_CONFIRMED_filled",
                 order_id=order_id[:20],
                 price=f"${price:.2f}",
                 shares=f"{shares:.2f}")
        return {"id": order_id, "price": price, "size": shares, "status": "MATCHED"}

    except Exception as e:
        log.error("sell_order_failed", error=str(e))
        return None


TAKER_FEE_BPS = 1000  # 10% taker fee (mercados BTC 5min)


async def execute_sell_taker(
    client: OrderClient,
    token_id: str,
    shares: float,
    price: float,
) -> dict | None:
    """
    Stop loss SELL — FOK (Fill or Kill) taker order.
    Preenche imediatamente ou falha. Sem ghost orders.
    Preço postado 5% abaixo do atual para garantir fill.
    """
    # Preço agressivo: 5% abaixo pra garantir match
    aggressive_price = round(max(price * 0.95, 0.01), 2)
    # Reduz shares levemente pra evitar erro de saldo por rounding
    shares = round(shares * 0.99, 2)

    if aggressive_price <= 0 or aggressive_price >= 1 or shares < 1.0:
        log.warning("invalid_sell_taker", price=aggressive_price, shares=shares)
        return None

    if client.dry_run:
        log.info("dry_run_sell_taker", price=f"${aggressive_price:.4f}", shares=f"{shares:.2f}")
        return {"id": f"dry-sell-{int(time.time()*1000)}", "status": "MATCHED"}

    try:
        clob = client._ensure_clob()
        order_args = OrderArgs(
            token_id=token_id,
            price=aggressive_price,
            size=round(shares, 2),
            side="SELL",
            fee_rate_bps=TAKER_FEE_BPS,
        )

        loop = asyncio.get_running_loop()
        # Cria a ordem e posta como FOK (fill imediato ou cancela)
        order = await loop.run_in_executor(
            None, clob.create_order, order_args,
        )
        result = await loop.run_in_executor(
            None, clob.post_order, order, OrderType.FOK,
        )

        order_id = None
        if isinstance(result, dict):
            order_id = result.get("orderID") or result.get("id")

        if not order_id:
            log.warning("sell_taker_no_order_id", result=str(result)[:100])
            return None

        # FOK é imediato — se retornou order_id, preencheu
        log.info("sell_taker_filled",
                 order_id=order_id[:20],
                 price=f"${aggressive_price:.2f}",
                 shares=f"{shares:.2f}")
        return {"id": order_id, "price": aggressive_price, "size": shares, "status": "MATCHED"}

    except Exception as e:
        log.error("sell_taker_failed", error=str(e))
        return None
