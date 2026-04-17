"""
Market Maker Engine — Buy cheap sides, lock profit, or take profit.

Strategy modeled after top whale traders (3500+ trades analyzed):
  Frequent-Jack: +$206  |  0x20d2309cd9: +$245

Core idea:
  1. Buy one side when cheap (<= $0.48) guided by signals
  2. If the OTHER side also becomes cheap (<= $0.45), buy it too
     -> total cost < $1.00 = guaranteed profit at resolution
  3. Sell positions when price rises (take profit)
  4. NEVER sell at a loss — hold to resolution instead

Phases (by time remaining in 5-min cycle):
  COLLECT    5:00 -> 4:00  Accumulate data, track prices
  FIRST_BUY  4:00 -> 3:00  Buy one side if cheap + signals agree
  MANAGE     3:00 -> 1:30  Lock profit (buy other side) or take profit (sell)
  EXIT       1:30 -> 0:00  Late sell if profitable, else hold to resolution
"""
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
import structlog

from config.settings import (
    TICK_INTERVAL, DRY_RUN,
    PHASE_COLLECT_START, PHASE_FIRST_BUY_START, PHASE_FIRST_BUY_END,
    PHASE_SECOND_BUY_START, PHASE_SECOND_BUY_END, PHASE_EXIT_START,
    SHARES_PER_TRADE, MIN_BUY_PRICE, MAX_BUY_PRICE, LOCK_BUY_PRICE,
    TAKE_PROFIT_PCT, LATE_SELL_PCT,
    MAKER_FILL_TIMEOUT,
    BUFFER_SIZE, BTC_BUFFER_SIZE,
)
from core.risk_manager import RiskManager
from core.analyzer import calc_slope
from data.price_buffer import PriceBuffer, CycleTracker
from data.feeds import BinanceFeed, PolymarketFeed, PolymarketREST
from data.signals import (
    ChainlinkFeed, VolumeImbalanceFeed, LiquidationFeed, SignalAggregator,
)
from data.storage import Storage
from data.cycle_collector import CycleCollector
from execution.order_client import OrderClient, execute_trade, execute_sell, execute_sell_taker, post_lock_limit
from monitoring.notifier import TelegramNotifier
from core.btc_stop_loss import is_sideways_market

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Position dataclass — tracks YES and NO sides independently
# ---------------------------------------------------------------------------

@dataclass
class MarketMakerPosition:
    """Tracks both sides of a binary market within a single cycle."""

    # YES side
    yes_shares: float = 0.0
    yes_entry: float = 0.0
    yes_cost: float = 0.0
    yes_token_id: str = ""
    yes_order_id: str = ""

    # NO side
    no_shares: float = 0.0
    no_entry: float = 0.0
    no_cost: float = 0.0
    no_token_id: str = ""
    no_order_id: str = ""

    # Flags
    yes_sold: bool = False
    no_sold: bool = False
    yes_sell_price: float = 0.0
    no_sell_price: float = 0.0
    yes_sl_attempts: int = 0
    no_sl_attempts: int = 0
    pending_lock_order_id: str = ""
    pending_lock_posted_at: float = 0.0

    @property
    def is_locked(self) -> bool:
        """True if we hold both YES and NO (guaranteed profit)."""
        return self.yes_shares > 0 and self.no_shares > 0

    @property
    def locked_profit(self) -> float:
        """Guaranteed profit if holding both sides and resolution occurs."""
        if not self.is_locked:
            return 0.0
        # At resolution, min(yes, no) shares pay out $1 each
        matched = min(self.yes_shares, self.no_shares)
        return matched * 1.0 - self.yes_cost - self.no_cost

    @property
    def total_cost(self) -> float:
        return self.yes_cost + self.no_cost

    @property
    def has_yes(self) -> bool:
        return self.yes_shares > 0 and not self.yes_sold

    @property
    def has_no(self) -> bool:
        return self.no_shares > 0 and not self.no_sold

    @property
    def is_empty(self) -> bool:
        return not self.has_yes and not self.has_no


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class TradingEngine:
    def __init__(self):
        # Buffers
        self.share_buffer = PriceBuffer(BUFFER_SIZE)
        self.btc_buffer = PriceBuffer(BTC_BUFFER_SIZE)
        self.cycle_tracker = CycleTracker(max_cycles=10)
        for _ in range(3):
            self.cycle_tracker.current_cycle_max_delta = 20
            self.cycle_tracker.end_cycle()

        # Risk
        self.risk_manager = RiskManager()

        # Data feeds
        self.btc_feed = BinanceFeed(self.btc_buffer)
        self.poly_feed = PolymarketFeed(self.share_buffer)
        self.poly_rest = PolymarketREST()

        # Signal feeds
        self.chainlink_feed = ChainlinkFeed()
        self.volume_feed = VolumeImbalanceFeed()
        self.liquidation_feed = LiquidationFeed()
        self.signal_agg = SignalAggregator(
            self.chainlink_feed, self.volume_feed, self.liquidation_feed,
        )

        # Persistence
        self.storage = Storage()
        self.order_client = OrderClient()
        self.notifier = TelegramNotifier()
        self.cycle_collector = CycleCollector()

        # State
        self.position: MarketMakerPosition | None = None
        self.current_market: dict | None = None
        self.running = False
        self._bought_yes_this_cycle: bool = False
        self._bought_no_this_cycle: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        log.info("engine_starting",
                 dry_run=DRY_RUN,
                 strategy="market_maker",
                 shares=SHARES_PER_TRADE,
                 max_buy=f"${MAX_BUY_PRICE}",
                 lock_mode="dynamic (entry+other ≤ $0.90)",
                 tp=f"{TAKE_PROFIT_PCT:.0%}",
                 late_sell=f"{LATE_SELL_PCT:.0%}")
        self.storage.connect()
        await self.order_client.initialize()

        # Launch background feed connections
        asyncio.create_task(self.btc_feed.connect())
        asyncio.create_task(self.chainlink_feed.connect())
        asyncio.create_task(self.volume_feed.connect())
        asyncio.create_task(self.liquidation_feed.connect())

        self.running = True
        try:
            await self._main_loop()
        except KeyboardInterrupt:
            log.info("engine_stopping_keyboard")
        finally:
            self.running = False
            await self.shutdown()

    async def shutdown(self):
        await self.btc_feed.disconnect()
        await self.poly_feed.disconnect()
        await self.poly_rest.close()
        await self.chainlink_feed.disconnect()
        await self.volume_feed.disconnect()
        await self.liquidation_feed.disconnect()
        await self.order_client.close()
        await self.notifier.close()
        self.storage.close()
        log.info("engine_stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _main_loop(self):
        log.info("main_loop_started")
        last_daily_reset = ""

        while self.running:
            try:
                # Daily reset
                today = datetime.now().strftime("%Y-%m-%d")
                if today != last_daily_reset:
                    self.risk_manager.reset_daily()
                    last_daily_reset = today

                # Resolve expired position
                if self.position and self.current_market:
                    remaining = self._get_time_remaining(self.current_market)
                    if remaining <= 0:
                        await self._phase_resolve()

                # Find active market
                market = await self._find_active_market()
                if not market:
                    await asyncio.sleep(5)
                    continue

                remaining = self._get_time_remaining(market)

                # Route to correct phase
                if remaining > PHASE_FIRST_BUY_START:
                    await self._phase_collect(market, remaining)

                elif remaining > PHASE_FIRST_BUY_END:
                    await self._phase_collect(market, remaining)
                    # Se já tem posição, checar lock mesmo durante first_buy
                    if self.position and not self.position.is_empty and not self.position.is_locked:
                        await self._phase_manage(market, remaining)
                    else:
                        await self._phase_first_buy(market, remaining)

                elif remaining > PHASE_EXIT_START:
                    await self._phase_collect(market, remaining)
                    await self._phase_manage(market, remaining)

                elif remaining > 0:
                    await self._phase_collect(market, remaining)
                    await self._phase_exit(market, remaining)

                elif remaining <= 0:
                    if self.position and not self.position.is_empty:
                        await self._phase_resolve()
                    else:
                        # No position — just clean up the cycle
                        final_price = self.poly_feed.yes_price
                        if final_price > 0:
                            self.cycle_collector.end_cycle(final_price, 0.0)
                        self.cycle_tracker.end_cycle()
                        self.share_buffer.clear()
                        self._reset_cycle_flags()
                        await asyncio.sleep(3)
                        continue

                await asyncio.sleep(TICK_INTERVAL)

            except Exception as e:
                log.error("main_loop_error", error=str(e), exc_info=True)
                await asyncio.sleep(5)

    # ------------------------------------------------------------------
    # Phase 1: COLLECT (5:00 -> 4:00)
    # ------------------------------------------------------------------

    async def _phase_collect(self, market: dict, remaining: float):
        """Accumulate price data and snapshots."""
        yes_price = self.poly_feed.yes_price
        if yes_price <= 0:
            return

        delta = self._calculate_delta(yes_price)
        self.share_buffer.append(time.time(), yes_price, delta)
        self.cycle_tracker.update_tick(delta)
        self.cycle_collector.capture_snapshot(
            time_remaining=remaining,
            delta=delta,
            yes_price=yes_price,
            btc_price=self.btc_feed.last_price,
        )

    # ------------------------------------------------------------------
    # Phase 2: FIRST BUY (4:00 -> 3:00)
    # ------------------------------------------------------------------

    async def _phase_first_buy(self, market: dict, remaining: float):
        """Buy one side if cheap and signals agree on direction."""
        # Already bought both sides or risk limit hit
        if self._bought_yes_this_cycle and self._bought_no_this_cycle:
            return
        can_trade, reason = self.risk_manager.can_trade()
        if not can_trade:
            return

        # Filtro de Regime: Pula entrada se o mercado estiver direcional/volatil
        if not is_sideways_market(self.btc_buffer):
            log.info("skipping_first_buy_regime", reason="Market is trending or highly volatile.")
            return

        yes_price = self.poly_feed.yes_price
        no_price = self.poly_feed.no_price
        if yes_price <= 0 or no_price <= 0:
            return

        # Comprar o lado mais barato no range $0.38-$0.45
        if yes_price <= no_price and MIN_BUY_PRICE <= yes_price <= MAX_BUY_PRICE and not self._bought_yes_this_cycle:
            await self._buy_yes(market, yes_price, remaining, "first_buy")
            return

        if no_price < yes_price and MIN_BUY_PRICE <= no_price <= MAX_BUY_PRICE and not self._bought_no_this_cycle:
            await self._buy_no(market, no_price, remaining, "first_buy")
            return

    # ------------------------------------------------------------------
    # Phase 3: MANAGE (3:00 -> 1:30) — Lock profit or take profit
    # ------------------------------------------------------------------

    async def _phase_manage(self, market: dict, remaining: float):
        """
        Two possible actions:
          a) LOCK PROFIT: if we hold one side and the other becomes cheap,
             buy the other side -> guaranteed profit
          b) TAKE PROFIT: if our side's price rose to >= TAKE_PROFIT_PRICE,
             sell it
        """
        pos = self.position
        yes_price = self.poly_feed.yes_price
        no_price = self.poly_feed.no_price
        if yes_price <= 0 or no_price <= 0:
            return

        # --- Check if pending lock order filled ---
        if pos and pos.pending_lock_order_id and not pos.is_locked:
            filled = await self.order_client._verify_fill(
                pos.pending_lock_order_id, timeout=0.5
            )
            if filled:
                # Lock order filled! Update position
                if pos.has_yes and not pos.has_no:
                    lock_price = 0.90 - pos.yes_entry  # approximate
                    pos.no_shares = SHARES_PER_TRADE
                    pos.no_entry = lock_price
                    pos.no_cost = SHARES_PER_TRADE * lock_price
                    pos.no_token_id = self._get_no_token(market) or ""
                    self._bought_no_this_cycle = True
                elif pos.has_no and not pos.has_yes:
                    lock_price = 0.90 - pos.no_entry
                    pos.yes_shares = SHARES_PER_TRADE
                    pos.yes_entry = lock_price
                    pos.yes_cost = SHARES_PER_TRADE * lock_price
                    pos.yes_token_id = self._get_yes_token(market) or ""
                    self._bought_yes_this_cycle = True
                pos.pending_lock_order_id = ""
                log.info("pending_lock_FILLED",
                         locked=pos.is_locked,
                         profit=f"${pos.locked_profit:+.2f}")

        # --- Already locked: just wait for resolution ---
        if pos and pos.is_locked:
            if int(remaining) % 30 < 3:
                log.info("locked_position_holding",
                         profit=f"${pos.locked_profit:+.2f}",
                         remaining=f"{remaining:.0f}s")
            return

        # --- Holding YES only ---
        if pos and pos.has_yes and not pos.has_no:
            # Lock opportunity: dynamic threshold based on entry
            # Lock if total per-share cost < $0.90 → guaranteed +$0.50 profit
            lock_max_no = 0.90 - pos.yes_entry
            has_pending_lock = bool(pos.pending_lock_order_id)
            if no_price <= lock_max_no and not self._bought_no_this_cycle and not has_pending_lock:
                lock_profit = (1.0 - pos.yes_entry - no_price) * SHARES_PER_TRADE
                log.info("lock_opportunity_detected",
                         yes_entry=f"${pos.yes_entry:.2f}",
                         no_price=f"${no_price:.2f}",
                         lock_max=f"${lock_max_no:.2f}",
                         lock_profit=f"${lock_profit:+.2f}")
                await self._buy_no(market, no_price, remaining, "lock_profit")
                return

            # Take profit: YES rose
            yes_tp = pos.yes_entry * (1 + TAKE_PROFIT_PCT) if pos.yes_entry > 0 else 99
            if yes_price >= yes_tp:
                await self._sell_yes(pos, yes_price, remaining, "take_profit")
                return

            # Time-Stop aos 40s
            time_stop_triggered = (pos.pending_lock_posted_at > 0 and
                                   time.time() - pos.pending_lock_posted_at > 40 and
                                   not pos.is_locked)
            if time_stop_triggered:
                log.info("manage_time_stop_yes", price=f"${yes_price:.2f}", elapsed=time.time() - pos.pending_lock_posted_at)
                await self._sell_yes(pos, yes_price, remaining, "time_stop")
                return

            # Stop loss -20% (após 1:45)
            yes_gain = (yes_price - pos.yes_entry) / pos.yes_entry if pos.yes_entry > 0 else 0
            if yes_gain <= -0.20 and remaining <= 105 and pos.yes_sl_attempts < 3:
                pos.yes_sl_attempts += 1
                log.info("manage_sl_yes", price=f"${yes_price:.2f}", gain=f"{yes_gain:+.0%}",
                         attempt=f"{pos.yes_sl_attempts}/3")
                await self._sell_yes(pos, yes_price, remaining, "stop_loss")
                return

        # --- Holding NO only ---
        if pos and pos.has_no and not pos.has_yes:
            # Lock opportunity: dynamic threshold based on entry
            lock_max_yes = 0.90 - pos.no_entry
            has_pending_lock = bool(pos.pending_lock_order_id)
            if yes_price <= lock_max_yes and not self._bought_yes_this_cycle and not has_pending_lock:
                lock_profit = (1.0 - pos.no_entry - yes_price) * SHARES_PER_TRADE
                log.info("lock_opportunity_detected",
                         no_entry=f"${pos.no_entry:.2f}",
                         yes_price=f"${yes_price:.2f}",
                         lock_max=f"${lock_max_yes:.2f}",
                         lock_profit=f"${lock_profit:+.2f}")
                await self._buy_yes(market, yes_price, remaining, "lock_profit")
                return

            # Take profit: NO rose
            no_tp = pos.no_entry * (1 + TAKE_PROFIT_PCT) if pos.no_entry > 0 else 99
            if no_price >= no_tp:
                await self._sell_no(pos, no_price, remaining, "take_profit")
                return

            # Time-Stop aos 40s
            time_stop_triggered = (pos.pending_lock_posted_at > 0 and
                                   time.time() - pos.pending_lock_posted_at > 40 and
                                   not pos.is_locked)
            if time_stop_triggered:
                log.info("manage_time_stop_no", price=f"${no_price:.2f}", elapsed=time.time() - pos.pending_lock_posted_at)
                await self._sell_no(pos, no_price, remaining, "time_stop")
                return

            # Stop loss -20% (após 1:45)
            no_gain = (no_price - pos.no_entry) / pos.no_entry if pos.no_entry > 0 else 0
            if no_gain <= -0.20 and remaining <= 105 and pos.no_sl_attempts < 3:
                pos.no_sl_attempts += 1
                log.info("manage_sl_no", price=f"${no_price:.2f}", gain=f"{no_gain:+.0%}",
                         attempt=f"{pos.no_sl_attempts}/3")
                await self._sell_no(pos, no_price, remaining, "stop_loss")
                return

        # --- No position yet: try first buy if still in window ---
        has_traded = self._bought_yes_this_cycle or self._bought_no_this_cycle
        if not has_traded and remaining > PHASE_FIRST_BUY_END:
            await self._phase_first_buy(market, remaining)

    # ------------------------------------------------------------------
    # Phase 4: EXIT (1:30 -> 0:00)
    # ------------------------------------------------------------------

    async def _phase_exit(self, market: dict, remaining: float):
        """
        Exit phase logic:
          - Locked position -> hold (guaranteed profit)
          - Single side with profit >= LATE_SELL_PRICE -> sell
          - Single side losing -> hold to resolution (NEVER sell at a loss)
        """
        pos = self.position
        yes_price = self.poly_feed.yes_price
        no_price = self.poly_feed.no_price
        if yes_price <= 0:
            return

        if not pos or pos.is_empty:
            return

        # Locked: just hold
        if pos.is_locked:
            if int(remaining) % 30 < 3:
                log.info("exit_locked_holding",
                         profit=f"${pos.locked_profit:+.2f}",
                         remaining=f"{remaining:.0f}s")
            return

        # Holding YES only
        if pos.has_yes:
            gain_pct = (yes_price - pos.yes_entry) / pos.yes_entry if pos.yes_entry > 0 else 0
            yes_late = pos.yes_entry * (1 + LATE_SELL_PCT) if pos.yes_entry > 0 else 99
            # Late sell (profit)
            if yes_price >= yes_late and yes_price > pos.yes_entry:
                await self._sell_yes(pos, yes_price, remaining, "late_sell")
                return
            # Time-Stop aos 40s
            time_stop_triggered = (pos.pending_lock_posted_at > 0 and
                                   time.time() - pos.pending_lock_posted_at > 40 and
                                   not pos.is_locked)
            if time_stop_triggered:
                log.info("exit_time_stop_yes", price=f"${yes_price:.2f}", elapsed=time.time() - pos.pending_lock_posted_at)
                await self._sell_yes(pos, yes_price, remaining, "time_stop")
                return

            # Stop loss -20% (max 3 tentativas, depois hold to resolution)
            if gain_pct <= -0.20 and pos.yes_sl_attempts < 3:
                pos.yes_sl_attempts += 1
                log.info("exit_sl_yes", price=f"${yes_price:.2f}", gain=f"{gain_pct:+.0%}",
                         attempt=f"{pos.yes_sl_attempts}/3")
                await self._sell_yes(pos, yes_price, remaining, "stop_loss")
                return
            if int(remaining) % 30 < 3:
                log.info("exit_holding_yes",
                         price=f"${yes_price:.2f}",
                         entry=f"${pos.yes_entry:.2f}",
                         gain=f"{gain_pct:+.0%}",
                         remaining=f"{remaining:.0f}s")

        # Holding NO only
        if pos.has_no:
            no_gain_pct = (no_price - pos.no_entry) / pos.no_entry if pos.no_entry > 0 else 0
            no_late = pos.no_entry * (1 + LATE_SELL_PCT) if pos.no_entry > 0 else 99
            if no_price >= no_late and no_price > pos.no_entry:
                await self._sell_no(pos, no_price, remaining, "late_sell")
                return
            # Time-Stop aos 40s
            time_stop_triggered = (pos.pending_lock_posted_at > 0 and
                                   time.time() - pos.pending_lock_posted_at > 40 and
                                   not pos.is_locked)
            if time_stop_triggered:
                log.info("exit_time_stop_no", price=f"${no_price:.2f}", elapsed=time.time() - pos.pending_lock_posted_at)
                await self._sell_no(pos, no_price, remaining, "time_stop")
                return

            # Stop loss -20% (max 3 tentativas, depois hold to resolution)
            if no_gain_pct <= -0.20 and pos.no_sl_attempts < 3:
                pos.no_sl_attempts += 1
                log.info("exit_sl_no", price=f"${no_price:.2f}", gain=f"{no_gain_pct:+.0%}",
                         attempt=f"{pos.no_sl_attempts}/3")
                await self._sell_no(pos, no_price, remaining, "stop_loss")
                return
            if int(remaining) % 30 < 3:
                gain_pct = (no_price - pos.no_entry) / pos.no_entry if pos.no_entry > 0 else 0
                log.info("exit_holding_no",
                         price=f"${no_price:.2f}",
                         entry=f"${pos.no_entry:.2f}",
                         gain=f"{gain_pct:+.0%}",
                         remaining=f"{remaining:.0f}s")

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    async def _phase_resolve(self):
        """Cycle ended. Compute PnL based on final prices and position."""
        pos = self.position
        if not pos or pos.is_empty:
            self._clean_up_cycle()
            return

        final_yes = self.poly_feed.yes_price
        yes_won = final_yes > 0.5

        pnl = 0.0
        result_parts = []

        # --- Locked position: guaranteed payout ---
        if pos.is_locked:
            matched = min(pos.yes_shares, pos.no_shares)
            pnl = matched * 1.0 - pos.total_cost
            result = "LOCK_WIN" if pnl > 0 else "LOCK_LOSS"
            result_parts.append(f"locked matched={matched:.0f}")
        else:
            # --- YES side ---
            if pos.has_yes:
                if yes_won:
                    pnl += pos.yes_shares * 1.0 - pos.yes_cost
                    result_parts.append("YES_WIN")
                else:
                    pnl += -pos.yes_cost
                    result_parts.append("YES_LOSS")

            # --- NO side ---
            if pos.has_no:
                if not yes_won:
                    pnl += pos.no_shares * 1.0 - pos.no_cost
                    result_parts.append("NO_WIN")
                else:
                    pnl += -pos.no_cost
                    result_parts.append("NO_LOSS")

            # --- Already-sold sides: add their realized PnL ---
            if pos.yes_sold:
                sell_proceeds = pos.yes_shares * pos.yes_sell_price * (1 - 0.0315)
                sell_pnl = sell_proceeds - pos.yes_cost
                pnl += sell_pnl
                result_parts.append(f"YES_SOLD(${sell_pnl:+.2f})")

            if pos.no_sold:
                sell_proceeds = pos.no_shares * pos.no_sell_price * (1 - 0.0315)
                sell_pnl = sell_proceeds - pos.no_cost
                pnl += sell_pnl
                result_parts.append(f"NO_SOLD(${sell_pnl:+.2f})")

            result = " + ".join(result_parts) if result_parts else "EMPTY"

        self.risk_manager.update(pnl)

        log.info("cycle_resolved",
                 result=result,
                 pnl=f"${pnl:+.2f}",
                 final_yes=f"${final_yes:.2f}",
                 yes_won=yes_won,
                 had_yes=pos.yes_shares > 0,
                 had_no=pos.no_shares > 0,
                 locked=pos.is_locked,
                 pnl_today=f"${self.risk_manager.state.pnl_today:+.2f}")

        # Notify
        await self.notifier.notify_result(
            won=pnl > 0,
            pnl=pnl,
            pnl_today=self.risk_manager.state.pnl_today,
        )

        # Persist
        try:
            self.storage.conn.execute(
                "UPDATE trades SET result = ?, pnl = ?, resolution_price = ? "
                "WHERE timestamp = (SELECT MAX(timestamp) FROM trades)",
                [result, round(pnl, 2), round(final_yes, 4)]
            )
        except Exception:
            pass

        self.cycle_collector.end_cycle(final_yes, pnl)
        self._clean_up_cycle()

    # ------------------------------------------------------------------
    # Buy / Sell helpers
    # ------------------------------------------------------------------

    async def _buy_yes(self, market: dict, price: float, remaining: float, reason: str):
        """Place a maker BUY order for YES shares."""
        token_id = self._get_yes_token(market)
        if not token_id:
            log.warning("no_yes_token")
            return

        cost = SHARES_PER_TRADE * price
        log.info("buy_yes_attempt",
                 reason=reason,
                 price=f"${price:.2f}",
                 cost=f"${cost:.2f}",
                 remaining=f"{remaining:.0f}s")

        order = await execute_trade(
            self.order_client, token_id, "Up", cost, price
        )
        if not order:
            log.warning("buy_yes_failed", price=f"${price:.2f}", reason=reason)
            return

        order_id = ""
        if isinstance(order, dict):
            order_id = order.get("id") or order.get("orderID") or ""

        # Initialize position if needed
        if not self.position:
            self.position = MarketMakerPosition()

        self.position.yes_shares = SHARES_PER_TRADE
        self.position.yes_entry = price
        self.position.yes_cost = cost
        self.position.yes_token_id = token_id
        self.position.yes_order_id = str(order_id)

        self._bought_yes_this_cycle = True

        log.info("buy_yes_filled",
                 reason=reason,
                 price=f"${price:.2f}",
                 cost=f"${cost:.2f}",
                 locked=self.position.is_locked,
                 locked_profit=f"${self.position.locked_profit:+.2f}" if self.position.is_locked else "N/A")

        # Persist trade
        market_id = market.get("conditionId", market.get("condition_id", ""))
        self.storage.log_trade({
            "timestamp": time.time(),
            "market_id": market_id,
            "direction": "Up",
            "bet_size": cost,
            "entry_price": price,
            "entry_time_remaining": remaining,
        })
        self.cycle_collector.record_trade("Up", cost, price)

        await self.notifier.send(
            f"<b>BUY YES</b> ({reason})\n"
            f"${price:.2f} x {SHARES_PER_TRADE} = ${cost:.2f}\n"
            f"Locked: {'YES' if self.position.is_locked else 'NO'}"
        )

        # Postar lock limit imediato pro outro lado (GTD, expira no fim do ciclo)
        if not self.position.is_locked and not self._bought_no_this_cycle:
            lock_max_price = round(0.90 - price, 2)
            no_token = self._get_no_token(market)
            if no_token and lock_max_price > 0.15:
                lock_order_id = await post_lock_limit(
                    self.order_client, no_token,
                    lock_max_price, SHARES_PER_TRADE,
                    expiry_seconds=remaining,
                )
                if lock_order_id:
                    self.position.pending_lock_order_id = lock_order_id
                    self.position.pending_lock_posted_at = time.time()
                    log.info("lock_limit_auto_posted",
                             side="NO", max_price=f"${lock_max_price:.2f}",
                             yes_entry=f"${price:.2f}")

    async def _buy_no(self, market: dict, price: float, remaining: float, reason: str):
        """Place a maker BUY order for NO shares."""
        token_id = self._get_no_token(market)
        if not token_id:
            log.warning("no_no_token")
            return

        cost = SHARES_PER_TRADE * price
        log.info("buy_no_attempt",
                 reason=reason,
                 price=f"${price:.2f}",
                 cost=f"${cost:.2f}",
                 remaining=f"{remaining:.0f}s")

        order = await execute_trade(
            self.order_client, token_id, "Down", cost, price
        )
        if not order:
            log.warning("buy_no_failed", price=f"${price:.2f}", reason=reason)
            return

        order_id = ""
        if isinstance(order, dict):
            order_id = order.get("id") or order.get("orderID") or ""

        if not self.position:
            self.position = MarketMakerPosition()

        self.position.no_shares = SHARES_PER_TRADE
        self.position.no_entry = price
        self.position.no_cost = cost
        self.position.no_token_id = token_id
        self.position.no_order_id = str(order_id)

        self._bought_no_this_cycle = True

        log.info("buy_no_filled",
                 reason=reason,
                 price=f"${price:.2f}",
                 cost=f"${cost:.2f}",
                 locked=self.position.is_locked,
                 locked_profit=f"${self.position.locked_profit:+.2f}" if self.position.is_locked else "N/A")

        market_id = market.get("conditionId", market.get("condition_id", ""))
        self.storage.log_trade({
            "timestamp": time.time(),
            "market_id": market_id,
            "direction": "Down",
            "bet_size": cost,
            "entry_price": price,
            "entry_time_remaining": remaining,
        })
        self.cycle_collector.record_trade("Down", cost, price)

        await self.notifier.send(
            f"<b>BUY NO</b> ({reason})\n"
            f"${price:.2f} x {SHARES_PER_TRADE} = ${cost:.2f}\n"
            f"Locked: {'YES' if self.position.is_locked else 'NO'}"
        )

        # Postar lock limit imediato pro outro lado (GTD)
        if not self.position.is_locked and not self._bought_yes_this_cycle:
            lock_max_price = round(0.90 - price, 2)
            yes_token = self._get_yes_token(market)
            if yes_token and lock_max_price > 0.15:
                lock_order_id = await post_lock_limit(
                    self.order_client, yes_token,
                    lock_max_price, SHARES_PER_TRADE,
                    expiry_seconds=remaining,
                )
                if lock_order_id:
                    self.position.pending_lock_order_id = lock_order_id
                    self.position.pending_lock_posted_at = time.time()
                    log.info("lock_limit_auto_posted",
                             side="YES", max_price=f"${lock_max_price:.2f}",
                             no_entry=f"${price:.2f}")

    async def _sell_yes(self, pos: MarketMakerPosition, price: float,
                        remaining: float, reason: str):
        """Sell YES shares. Allows stop_loss even at a loss."""
        if not pos.has_yes:
            return
        if price <= pos.yes_entry and reason not in ("stop_loss", "time_stop", "force_timeout_30s", "force_cycle_end"):
            log.info("sell_yes_skipped_would_lose",
                     price=f"${price:.2f}",
                     entry=f"${pos.yes_entry:.2f}")
            return

        gain_pct = (price - pos.yes_entry) / pos.yes_entry
        log.info("sell_yes_attempt",
                 reason=reason,
                 price=f"${price:.2f}",
                 entry=f"${pos.yes_entry:.2f}",
                 gain=f"{gain_pct:+.0%}",
                 remaining=f"{remaining:.0f}s")

        # SL/Time-Stop: tenta taker FOK primeiro, fallback pra maker se falhar
        if reason in ("stop_loss", "time_stop"):
            order = await execute_sell_taker(
                self.order_client, pos.yes_token_id,
                pos.yes_shares, price
            )
            if not order:
                log.info("sell_yes_taker_failed_trying_maker", price=f"${price:.2f}")
                order = await execute_sell(
                    self.order_client, pos.yes_token_id,
                    pos.yes_shares, price
                )
        else:
            order = await execute_sell(
                self.order_client, pos.yes_token_id,
                pos.yes_shares, price
            )
        if not order:
            log.warning("sell_yes_failed", price=f"${price:.2f}")
            return

        fill_price = price
        if isinstance(order, dict):
            fill_price = float(order.get("price", price))

        proceeds = pos.yes_shares * fill_price * (1 - 0.0315)
        sell_pnl = proceeds - pos.yes_cost

        pos.yes_sold = True
        pos.yes_sell_price = fill_price

        # Cancelar GTD lock pendente pra evitar compra órfã
        if pos.pending_lock_order_id:
            log.info("cancelling_pending_lock_after_sell", order_id=pos.pending_lock_order_id[:20])
            await self.order_client.cancel_order(pos.pending_lock_order_id)
            pos.pending_lock_order_id = ""

        log.info("sell_yes_filled",
                 reason=reason,
                 pnl=f"${sell_pnl:+.2f}",
                 sell=f"${fill_price:.2f}",
                 entry=f"${pos.yes_entry:.2f}")

        # If no other side held, realize PnL now
        if not pos.has_no:
            self.risk_manager.update(sell_pnl)
            try:
                self.storage.conn.execute(
                    "UPDATE trades SET result = ?, pnl = ? "
                    "WHERE timestamp = (SELECT MAX(timestamp) FROM trades)",
                    [f"SELL_YES_{reason.upper()}", round(sell_pnl, 2)]
                )
            except Exception:
                pass
            self.cycle_collector.end_cycle(self.poly_feed.yes_price, sell_pnl)

        await self.notifier.send(
            f"<b>SELL YES</b> ({reason})\n"
            f"${fill_price:.2f} | PnL: ${sell_pnl:+.2f}"
        )

    async def _sell_no(self, pos: MarketMakerPosition, price: float,
                       remaining: float, reason: str):
        """Sell NO shares. Allows stop_loss even at a loss."""
        if not pos.has_no:
            return
        if price <= pos.no_entry and reason not in ("stop_loss", "time_stop", "force_timeout_30s", "force_cycle_end"):
            log.info("sell_no_skipped_would_lose",
                     price=f"${price:.2f}",
                     entry=f"${pos.no_entry:.2f}")
            return

        gain_pct = (price - pos.no_entry) / pos.no_entry
        log.info("sell_no_attempt",
                 reason=reason,
                 price=f"${price:.2f}",
                 entry=f"${pos.no_entry:.2f}",
                 gain=f"{gain_pct:+.0%}",
                 remaining=f"{remaining:.0f}s")

        # SL/Time-Stop: tenta taker FOK primeiro, fallback pra maker se falhar
        if reason in ("stop_loss", "time_stop"):
            order = await execute_sell_taker(
                self.order_client, pos.no_token_id,
                pos.no_shares, price
            )
            if not order:
                log.info("sell_no_taker_failed_trying_maker", price=f"${price:.2f}")
                order = await execute_sell(
                    self.order_client, pos.no_token_id,
                    pos.no_shares, price
                )
        else:
            order = await execute_sell(
                self.order_client, pos.no_token_id,
                pos.no_shares, price
            )
        if not order:
            log.warning("sell_no_failed", price=f"${price:.2f}")
            return

        fill_price = price
        if isinstance(order, dict):
            fill_price = float(order.get("price", price))

        proceeds = pos.no_shares * fill_price * (1 - 0.0315)
        sell_pnl = proceeds - pos.no_cost

        pos.no_sold = True
        pos.no_sell_price = fill_price

        # Cancelar GTD lock pendente pra evitar compra órfã
        if pos.pending_lock_order_id:
            log.info("cancelling_pending_lock_after_sell", order_id=pos.pending_lock_order_id[:20])
            await self.order_client.cancel_order(pos.pending_lock_order_id)
            pos.pending_lock_order_id = ""

        log.info("sell_no_filled",
                 reason=reason,
                 pnl=f"${sell_pnl:+.2f}",
                 sell=f"${fill_price:.2f}",
                 entry=f"${pos.no_entry:.2f}")

        if not pos.has_yes:
            self.risk_manager.update(sell_pnl)
            try:
                self.storage.conn.execute(
                    "UPDATE trades SET result = ?, pnl = ? "
                    "WHERE timestamp = (SELECT MAX(timestamp) FROM trades)",
                    [f"SELL_NO_{reason.upper()}", round(sell_pnl, 2)]
                )
            except Exception:
                pass
            self.cycle_collector.end_cycle(self.poly_feed.yes_price, sell_pnl)

        await self.notifier.send(
            f"<b>SELL NO</b> ({reason})\n"
            f"${fill_price:.2f} | PnL: ${sell_pnl:+.2f}"
        )

    # ------------------------------------------------------------------
    # Signal evaluation
    # ------------------------------------------------------------------

    def _get_signal_direction(self) -> str | None:
        """
        Combine all signals to determine trade direction.

        Priority:
          1. SignalAggregator (Chainlink + Volume + Liquidation + BTC slope)
          2. Fallback to BTC 2/3 slope if aggregator is indecisive
        """
        # BTC slope direction (used by both paths)
        btc_slope_dir = self._get_btc_slope_direction()

        # Try full signal aggregator first
        signal = self.signal_agg.evaluate(btc_slope_direction=btc_slope_dir)
        if signal.direction:
            log.info("signal_aggregator",
                     direction=signal.direction,
                     score=signal.score,
                     max_score=signal.max_score,
                     confidence=f"{signal.confidence:.2f}",
                     signals=signal.signals)
            return signal.direction

        # Fallback: BTC 2/3 alone
        if btc_slope_dir:
            log.info("signal_btc_fallback", direction=btc_slope_dir)
            return btc_slope_dir

        return None

    def _get_btc_slope_direction(self) -> str | None:
        """BTC 2/3 filter: majority of 1m/2m/3m slopes agree on direction."""
        btc_prices = self.btc_buffer.get_prices()
        if len(btc_prices) < 60:
            return None

        slopes = {}
        for label, window in [("1m", 60), ("2m", 120), ("3m", 180)]:
            n = min(window, len(btc_prices))
            slopes[label] = calc_slope(btc_prices[-n:])

        up_votes = sum(1 for s in slopes.values() if s > 0)
        down_votes = 3 - up_votes

        if up_votes >= 2:
            return "Up"
        elif down_votes >= 2:
            return "Down"
        return None

    # ------------------------------------------------------------------
    # Market discovery
    # ------------------------------------------------------------------

    async def _find_active_market(self) -> dict | None:
        """Find the currently active 5-min BTC market."""
        if self.current_market:
            remaining = self._get_time_remaining(self.current_market)
            if remaining > -10:
                return self.current_market

        markets = await self.poly_rest.get_markets("Bitcoin")
        if not markets:
            return None

        best = None
        for m in markets:
            remaining = self._get_time_remaining(m)
            if remaining > 0:
                if not best or remaining < self._get_time_remaining(best):
                    best = m

        if best and best != self.current_market:
            self.current_market = best
            self._reset_cycle_flags()
            self.share_buffer.clear()

            # Reset Chainlink cycle for new window
            self.chainlink_feed.reset_cycle()

            # Connect price feed to new market tokens
            up_token = self._get_yes_token(best)
            down_token = self._get_no_token(best)
            if up_token:
                asyncio.create_task(self.poly_feed.switch_market(up_token, down_token))

            self.cycle_collector.start_cycle(
                market_id=best.get("conditionId", best.get("condition_id", "")),
                question=best.get("question", "?"),
            )
            log.info("new_cycle",
                     question=best.get("question", "?")[:60],
                     remaining=f"{self._get_time_remaining(best):.0f}s")

        return best

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _calculate_delta(self, current_price: float) -> float:
        prices = self.share_buffer.get_prices()
        if len(prices) < 2:
            return 0.0
        return (current_price - prices[0]) * 10000

    def _get_time_remaining(self, market: dict) -> float:
        window_end = market.get("_window_end_ts")
        if window_end:
            return window_end - time.time()
        end_str = market.get("endDateIso", market.get("end_date_iso", ""))
        if not end_str:
            return 0
        try:
            from datetime import datetime as dt, timezone
            end_dt = dt.fromisoformat(end_str.replace("Z", "+00:00"))
            return (end_dt - dt.now(timezone.utc)).total_seconds()
        except Exception:
            return 0

    def _get_token_ids(self, market: dict) -> list[str]:
        token_ids = market.get("clobTokenIds", [])
        if isinstance(token_ids, str):
            import json
            try:
                token_ids = json.loads(token_ids)
            except (ValueError, TypeError):
                token_ids = []
        return token_ids

    def _get_yes_token(self, market: dict) -> str | None:
        token_ids = self._get_token_ids(market)
        return token_ids[0] if token_ids else None

    def _get_no_token(self, market: dict) -> str | None:
        token_ids = self._get_token_ids(market)
        return token_ids[1] if len(token_ids) > 1 else None

    def _reset_cycle_flags(self):
        """Reset per-cycle state."""
        # Cancel pending lock order if exists
        if self.position and self.position.pending_lock_order_id:
            asyncio.ensure_future(
                self.order_client.cancel_order(self.position.pending_lock_order_id)
            )
        self._bought_yes_this_cycle = False
        self._bought_no_this_cycle = False
        self.position = None

    def _clean_up_cycle(self):
        """Full cleanup after a cycle resolves."""
        self.position = None
        self.cycle_tracker.end_cycle()
        self.share_buffer.clear()
        self._reset_cycle_flags()
