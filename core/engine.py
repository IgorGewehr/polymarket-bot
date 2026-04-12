"""
Engine principal do bot.
Orquestra o ciclo completo: coleta → análise → entrada → monitoramento → resolução.
"""
import asyncio
import time
from datetime import datetime
import structlog

from config.settings import (
    TICK_INTERVAL, ENTRY_WINDOW_START, ENTRY_WINDOW_SOFT,
    ENTRY_DEADLINE, ENTRY_CUTOFF, MIN_TIME_REMAINING,
    MIN_DELTA, MIN_RETURN_PCT, MIN_CONFIDENCE,
    BUFFER_SIZE, BTC_BUFFER_SIZE, DRY_RUN
)
from core.analyzer import run_analysis, AnalysisResult, calc_momentum
from core.sizing import calculate_bet_size, get_time_slot
from core.hedger import (
    Position, HedgeOpportunity, HedgeTracker,
    should_evaluate_hedge, should_execute_hedge,
    estimate_loss_probability
)
from core.risk_manager import RiskManager
from data.price_buffer import PriceBuffer, CycleTracker
from data.feeds import BinanceFeed, PolymarketFeed, PolymarketREST
from data.storage import Storage
from data.cycle_collector import CycleCollector
from core.lock_profit import evaluate_lock
from core.early_exit import evaluate_early_exit
from execution.order_client import OrderClient, execute_trade, execute_hedge, execute_sell, execute_lock
from monitoring.notifier import TelegramNotifier

log = structlog.get_logger()


class TradingEngine:
    def __init__(self):
        # Buffers
        self.share_buffer = PriceBuffer(BUFFER_SIZE)
        self.btc_buffer = PriceBuffer(BTC_BUFFER_SIZE)
        self.cycle_tracker = CycleTracker(max_cycles=10)
        # Seed com deltas razoáveis para evitar regime "lateral" no startup
        for _ in range(3):
            self.cycle_tracker.current_cycle_max_delta = 20
            self.cycle_tracker.end_cycle()

        # Core
        self.risk_manager = RiskManager()
        self.hedge_tracker = HedgeTracker()

        # Data
        self.btc_feed = BinanceFeed(self.btc_buffer)
        self.poly_feed = PolymarketFeed(self.share_buffer)
        self.poly_rest = PolymarketREST()
        self.storage = Storage()

        # Execution
        self.order_client = OrderClient()

        # Monitoring
        self.notifier = TelegramNotifier()

        # Data collection (Excel)
        self.cycle_collector = CycleCollector()

        # State
        self.current_position: Position | None = None
        self.current_market: dict | None = None
        self.running = False

    async def start(self):
        """Inicializa conexões e inicia o loop principal."""
        log.info("engine_starting", dry_run=DRY_RUN)
        self.storage.connect()

        # Inicializar cliente de ordens (CLOB auth)
        await self.order_client.initialize()

        # Iniciar feed de BTC em background
        btc_task = asyncio.create_task(self.btc_feed.connect())

        self.running = True
        try:
            await self.main_loop()
        except KeyboardInterrupt:
            log.info("engine_stopping")
        finally:
            self.running = False
            await self.shutdown()

    async def shutdown(self):
        """Fecha todas as conexões."""
        await self.btc_feed.disconnect()
        await self.poly_feed.disconnect()
        await self.poly_rest.close()
        await self.order_client.close()
        await self.notifier.close()
        self.storage.close()
        log.info("engine_stopped")

    async def main_loop(self):
        """
        Loop principal — roda indefinidamente.
        A cada iteração (2s), determina em que fase está e age.
        """
        log.info("main_loop_started")
        last_daily_reset = ""

        while self.running:
            try:
                now = time.time()
                today = datetime.now().strftime("%Y-%m-%d")

                # Reset diário
                if today != last_daily_reset:
                    self.risk_manager.reset_daily()
                    self.hedge_tracker.reset_daily()
                    last_daily_reset = today
                    log.info("daily_reset", date=today)

                # Se tem posição aberta e o mercado dela expirou → resolver PRIMEIRO
                if self.current_position and self.current_market:
                    old_remaining = self._get_time_remaining(self.current_market)
                    if old_remaining <= 0:
                        await self._phase_resolve(self.current_market)

                # Encontrar mercado ativo
                market = await self._find_active_market()
                if not market:
                    await asyncio.sleep(5)
                    continue

                time_remaining = self._get_time_remaining(market)

                # ── Fase 1: Coleta (5:00 → 4:30) ──
                if time_remaining > ENTRY_WINDOW_START:
                    await self._phase_collect(market)

                # ── Fase 2: Análise + Entrada (4:30 → 3:30) ──
                elif time_remaining > ENTRY_CUTOFF and not self.current_position:
                    await self._phase_analyze_and_enter(market, time_remaining)

                # ── Fase 2b: Entry tardio (3:30 → 2:00) para mercado indeciso ──
                elif time_remaining > 120 and not self.current_position:
                    await self._phase_late_entry(market, time_remaining)

                # ── Fase 3: Monitoramento + Hedge (até 0:00) ──
                elif self.current_position and time_remaining > 0:
                    await self._phase_monitor(market, time_remaining)

                # ── Fase 4: Resolução ──
                elif time_remaining <= 0 and self.current_position:
                    await self._phase_resolve(market)

                # ── Ciclo acabou sem posição ──
                elif time_remaining <= 0:
                    # Salvar dados do ciclo no Excel (mesmo sem trade)
                    final_price = self.poly_feed.yes_price
                    if final_price > 0:
                        self.cycle_collector.end_cycle(
                            final_yes_price=final_price,
                            pnl=0.0,
                        )
                    self.cycle_tracker.end_cycle()
                    self.share_buffer.clear()
                    await asyncio.sleep(3)
                    continue

                await asyncio.sleep(TICK_INTERVAL)

            except Exception as e:
                log.error("main_loop_error", error=str(e))
                await asyncio.sleep(5)

    async def _find_active_market(self) -> dict | None:
        """Encontra o mercado BTC 5min ativo mais próximo de resolver."""
        if self.current_market:
            remaining = self._get_time_remaining(self.current_market)
            if remaining > -10:  # Ainda ativo (com margem)
                return self.current_market

        markets = await self.poly_rest.get_markets("Bitcoin")
        if not markets:
            return None

        # Pegar o mercado que resolve mais cedo mas ainda tem tempo
        best = None
        for m in markets:
            remaining = self._get_time_remaining(m)
            if remaining > 0:
                if not best or remaining < self._get_time_remaining(best):
                    best = m

        if best and best != self.current_market:
            self.current_market = best
            self.share_buffer.clear()
            up_token = self._get_yes_token(best)
            down_token = self._get_no_token(best)
            if up_token:
                asyncio.create_task(self.poly_feed.connect(up_token, down_token))
            # Iniciar coleta de dados para Excel
            self.cycle_collector.start_cycle(
                market_id=best.get("conditionId", best.get("condition_id", "")),
                question=best.get("question", "?"),
            )
            log.info("market_found",
                     question=best.get("question", "?")[:60],
                     remaining=f"{self._get_time_remaining(best):.0f}s")

        return best

    async def _phase_collect(self, market: dict):
        """Fase de coleta: acumular ticks + capturar snapshots para Excel."""
        price = self.poly_feed.yes_price
        if price > 0:
            delta = self._calculate_delta(price)
            self.share_buffer.append(time.time(), price, delta)
            self.cycle_tracker.update_tick(delta)
            # Snapshot para o Excel
            time_remaining = self._get_time_remaining(market)
            self.cycle_collector.capture_snapshot(
                time_remaining=time_remaining,
                delta=delta,
                yes_price=price,
                btc_price=self.btc_feed.last_price,
            )

    async def _phase_analyze_and_enter(self, market: dict, time_remaining: float):
        """
        Fase de análise e entrada (4:30 → 3:30).

        Estratégia:
        1. Determinar trend REAL do BTC via Binance (slope dos últimos minutos)
        2. Ver o que o mercado Polymarket acha (YES price)
        3. Se mercado CONCORDA com a trend e share está 0.50-0.68 → ENTRAR
        4. Se mercado DISCORDA da trend → ESPERAR até 3:30 para ver se reverte
        5. Se não reverteu até ~3:30 → apostar no lado do mercado (>0.50)
        """
        # Continuar capturando snapshots para o Excel
        current_price = self.poly_feed.yes_price
        if current_price > 0:
            delta = self._calculate_delta(current_price)
            self.cycle_collector.capture_snapshot(
                time_remaining=time_remaining,
                delta=delta,
                yes_price=current_price,
                btc_price=self.btc_feed.last_price,
            )

        # Risk check
        can_trade, reason = self.risk_manager.can_trade()
        if not can_trade:
            log.debug("trade_blocked", reason=reason)
            return

        yes_price = self.poly_feed.yes_price
        no_price = self.poly_feed.no_price
        if yes_price <= 0:
            return

        # ── 1. Determinar trend real do BTC (Binance) — Multi-timeframe ──
        btc_prices = self.btc_buffer.get_prices()
        if len(btc_prices) < 60:
            return

        from core.analyzer import calc_slope

        # 3 timeframes: 1min (60 ticks), 2min (120 ticks), 3min (180 ticks)
        slopes = {}
        for label, window in [("1m", 60), ("2m", 120), ("3m", 180)]:
            n = min(window, len(btc_prices))
            slopes[label] = calc_slope(btc_prices[-n:])

        # Votos: quantos timeframes dizem Up?
        up_votes = sum(1 for s in slopes.values() if s > 0)
        down_votes = 3 - up_votes

        # Trend só é confirmada se 2/3 ou 3/3 concordam
        if up_votes >= 2:
            btc_trend = "Up"
            trend_strength = up_votes  # 2 = moderado, 3 = forte
        elif down_votes >= 2:
            btc_trend = "Down"
            trend_strength = down_votes
        else:
            btc_trend = "Neutral"
            trend_strength = 0

        # ── 2. Ver o que o mercado acha ──
        if yes_price > 0.50:
            market_trend = "Up"
            market_price = yes_price
        else:
            market_trend = "Down"
            market_price = no_price

        # ── 3. Decisão de entrada ──

        # Sem trend clara no BTC → esperar ou seguir mercado no deadline
        if btc_trend == "Neutral":
            if time_remaining > ENTRY_CUTOFF + 10:
                log.info("waiting_trend",
                         slopes=f"1m={slopes['1m']:.4f} 2m={slopes['2m']:.4f} 3m={slopes['3m']:.4f}",
                         msg="BTC sem trend clara, esperando")
                return
            else:
                # Deadline: seguir o mercado
                if 0.50 <= market_price <= 0.68:
                    direction = market_trend
                    entry_price = market_price
                    log.info("entry_deadline_neutral",
                             market=market_trend,
                             price=f"${market_price:.2f}",
                             msg="Sem trend BTC, seguindo mercado no deadline")
                else:
                    return

        # BTC trend e mercado CONCORDAM + preço na faixa → ENTRAR
        elif btc_trend == market_trend and 0.50 <= market_price <= 0.68:
            direction = market_trend
            entry_price = market_price
            log.info("entry_aligned",
                     btc_trend=btc_trend,
                     strength=f"{trend_strength}/3",
                     market=f"${market_price:.2f}",
                     msg="Trend e mercado concordam")

        # BTC e mercado DISCORDAM → esperar reversão
        elif btc_trend != market_trend:
            if time_remaining > ENTRY_CUTOFF + 10:
                log.info("waiting_reversal",
                         btc=btc_trend,
                         strength=f"{trend_strength}/3",
                         market=market_trend,
                         price=f"${market_price:.2f}",
                         remaining=f"{time_remaining:.0f}s")
                return
            else:
                # Deadline: seguir mercado
                if 0.50 <= market_price <= 0.68:
                    direction = market_trend
                    entry_price = market_price
                    log.info("entry_deadline",
                             btc=btc_trend,
                             market=market_trend,
                             price=f"${market_price:.2f}",
                             msg="Deadline, seguindo mercado")
                else:
                    return

        # Preço fora da faixa
        elif not (0.50 <= market_price <= 0.68):
            log.info("skip_price_range",
                     price=f"${market_price:.2f}")
            return
        else:
            direction = market_trend
            entry_price = market_price

        expected_return = (1.0 - entry_price) / entry_price

        log.info("entry_signal",
                 dir=direction,
                 entry=f"${entry_price:.2f}",
                 ret=f"{expected_return:.0%}",
                 btc=f"{btc_trend}({trend_strength}/3)",
                 yes=f"${yes_price:.2f}")

        # ── Calcular Sizing ──
        # Rodar análise completa para confiança e dados
        analysis = run_analysis(
            self.share_buffer,
            self.btc_buffer,
            self.cycle_tracker,
            current_price
        )
        confidence = analysis.confidence if analysis else 0.0

        bet_size = calculate_bet_size(
            confidence=confidence,
            expected_return=expected_return,
            time_remaining=time_remaining,
            direction=direction,
            consecutive_losses=self.risk_manager.state.consecutive_losses,
            is_drawdown=self.risk_manager.is_drawdown,
            is_squeeze_breakout=analysis.is_squeeze_breakout if analysis else False,
            entry_price=entry_price,
            trend_strength=trend_strength,
        )

        # ── EXECUTAR TRADE ──
        token_id = self._get_yes_token(market) if direction == "Up" \
            else self._get_no_token(market)

        if not token_id:
            return

        order = await execute_trade(
            self.order_client, token_id,
            direction, bet_size, entry_price
        )

        if order:
            shares = max(bet_size / entry_price, 5.0)  # Mínimo 5 (Polymarket enforces)
            actual_cost = shares * entry_price
            self.current_position = Position(
                direction=direction,
                bet_size=actual_cost,
                entry_price=entry_price,
                potential_return=shares,
                shares=shares,
                entry_time=time.time(),
                market_id=market.get("conditionId", market.get("condition_id", "")),
                token_id=token_id,
                entry_confidence=analysis.confidence,
                entry_alignment=int(analysis.layer2_alignment)
            )

            self.cycle_collector.record_trade(
                direction=direction,
                size=bet_size,
                entry_price=entry_price,
            )

            log.info("trade_executed",
                     direction=direction,
                     size=f"${bet_size}",
                     price=f"${entry_price:.2f}",
                     ret=f"{expected_return:.0%}",
                     time_slot=get_time_slot(time_remaining))

            await self.notifier.notify_trade(
                direction, bet_size, entry_price,
                analysis.confidence, analysis.regime
            )

            # Log para storage
            self.storage.log_trade({
                "timestamp": time.time(),
                "market_id": market.get("conditionId", market.get("condition_id", "")),
                "direction": direction,
                "bet_size": bet_size,
                "entry_price": entry_price,
                "entry_time_remaining": time_remaining,
                "confidence_score": confidence,
                "expected_return": expected_return,
            })

    async def _phase_late_entry(self, market: dict, time_remaining: float):
        """
        Entry tardio (3:30 → 2:00) para mercados que ficaram indecisos.
        Só entra se agora a share saiu do range lateral ($0.45-$0.55) e tem direção.
        Sizing máximo $2 (menos edge que entry cedo).
        """
        yes_price = self.poly_feed.yes_price
        no_price = self.poly_feed.no_price
        if yes_price <= 0:
            return

        # Snapshot para Excel
        delta = self._calculate_delta(yes_price)
        self.cycle_collector.capture_snapshot(
            time_remaining=time_remaining,
            delta=delta,
            yes_price=yes_price,
            btc_price=self.btc_feed.last_price,
        )

        # Risk check
        can_trade, reason = self.risk_manager.can_trade()
        if not can_trade:
            return

        # Só entrar se a share SAIU do range indeciso
        # Se ainda está $0.45-$0.55, continuar esperando
        if 0.55 < yes_price <= 0.68:
            direction = "Up"
            entry_price = yes_price
        elif 0.55 < no_price <= 0.68:
            direction = "Down"
            entry_price = no_price
        else:
            return  # Ainda indeciso ou fora de range

        expected_return = (1.0 - entry_price) / entry_price

        log.info("late_entry_signal",
                 dir=direction,
                 entry=f"${entry_price:.2f}",
                 ret=f"{expected_return:.0%}",
                 remaining=f"{time_remaining:.0f}s")

        # Sizing $3 para ter 5+ shares (habilita early exit)
        bet_size = calculate_bet_size(
            confidence=0.0,
            expected_return=expected_return,
            time_remaining=time_remaining,
            direction=direction,
            consecutive_losses=self.risk_manager.state.consecutive_losses,
            is_drawdown=self.risk_manager.is_drawdown,
            entry_price=entry_price,
            trend_strength=2,
        )

        token_id = self._get_yes_token(market) if direction == "Up" \
            else self._get_no_token(market)
        if not token_id:
            return

        order = await execute_trade(
            self.order_client, token_id,
            direction, bet_size, entry_price
        )

        if order:
            shares = max(bet_size / entry_price, 5.0)
            actual_cost = shares * entry_price
            self.current_position = Position(
                direction=direction,
                bet_size=actual_cost,
                entry_price=entry_price,
                potential_return=shares,
                shares=shares,
                entry_time=time.time(),
                market_id=market.get("conditionId", market.get("condition_id", "")),
                token_id=token_id,
            )
            self.cycle_collector.record_trade(direction, actual_cost, entry_price)
            log.info("late_trade_executed",
                     direction=direction,
                     size=f"${bet_size}",
                     price=f"${entry_price:.2f}",
                     ret=f"{expected_return:.0%}")
            self.storage.log_trade({
                "timestamp": time.time(),
                "market_id": market.get("conditionId", market.get("condition_id", "")),
                "direction": direction,
                "bet_size": bet_size,
                "entry_price": entry_price,
                "entry_time_remaining": time_remaining,
                "expected_return": expected_return,
            })

    async def _phase_monitor(self, market: dict, time_remaining: float):
        """Fase de monitoramento: early exit → lock profit → hedge."""
        # Snapshots para Excel
        yes_price = self.poly_feed.yes_price
        if yes_price > 0:
            delta = self._calculate_delta(yes_price)
            self.cycle_collector.capture_snapshot(
                time_remaining=time_remaining,
                delta=delta,
                yes_price=yes_price,
                btc_price=self.btc_feed.last_price,
            )

        pos = self.current_position
        if not pos or pos.exited_early:
            return

        # ── 1. EARLY EXIT (safety sell / delta guard / TP / SL) ──
        # Sempre avaliar, mesmo com lock ou hedge ativo
        current_delta = abs(self._calculate_delta(yes_price)) if yes_price > 0 else 0
        if yes_price > 0:
            exit_eval = evaluate_early_exit(
                direction=pos.direction,
                entry_price=pos.entry_price,
                shares=pos.shares,
                cost_basis=pos.bet_size,
                current_yes_price=yes_price,
                time_remaining=time_remaining,
                current_delta=current_delta,
            )

            # Log avaliação a cada 30s para debug
            if int(time_remaining) % 30 < 3:
                our_price = yes_price if pos.direction == "Up" else (1 - yes_price)
                gain = (our_price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0
                drop = (pos.entry_price - our_price) / pos.entry_price if pos.entry_price > 0 else 0
                log.info("exit_eval",
                         our=f"${our_price:.2f}",
                         gain=f"{gain:.0%}",
                         shares=f"{pos.shares:.1f}",
                         can_sell=pos.shares >= 5,
                         should=exit_eval.should_exit,
                         reason=exit_eval.reason or "none",
                         remaining=f"{time_remaining:.0f}s")

            if exit_eval.should_exit:
                # Vender todas as shares — retry com quantidade menor se falhar
                order = None
                for pct in [1.0, 0.95, 0.90, 0.85]:
                    sell_qty = round(pos.shares * pct, 2)
                    order = await execute_sell(
                        self.order_client, pos.token_id,
                        sell_qty, exit_eval.sell_price,
                    )
                    if order:
                        log.info("sell_executed",
                                 qty=sell_qty,
                                 price=f"${exit_eval.sell_price:.2f}",
                                 pct=f"{pct:.0%}")
                        break
                if order:
                    pnl = exit_eval.sell_pnl
                    pos.exited_early = True
                    pos.exit_price = exit_eval.sell_price
                    pos.exit_proceeds = exit_eval.sell_proceeds
                    pos.exit_reason = exit_eval.reason
                    self.risk_manager.update(pnl)

                    log.info("early_exit",
                             reason=exit_eval.reason,
                             pnl=f"${pnl:+.2f}",
                             sell=f"${exit_eval.sell_price:.2f}",
                             entry=f"${pos.entry_price:.2f}",
                             gain=f"{exit_eval.gain_pct:.0%}")

                    try:
                        self.storage.conn.execute(
                            "UPDATE trades SET result = ?, pnl = ? "
                            "WHERE timestamp = (SELECT MAX(timestamp) FROM trades)",
                            [f"EARLY_{exit_eval.reason.upper()}", round(pnl, 2)]
                        )
                    except Exception:
                        pass

                    self.cycle_collector.end_cycle(yes_price, pnl)
                    self.current_position = None
                    self.share_buffer.clear()
                    return

        # ── 2. LOCK PROFIT — SÓ quando estamos PERDENDO ──
        # Se share está acima do entry (ganhando) → não fazer lock, deixar take profit/safety sell agir
        # Se share caiu abaixo do entry (perdendo) → lock para garantir que não perde tudo
        our_price = yes_price if pos.direction == "Up" else (1 - yes_price)
        is_losing = our_price < pos.entry_price * 0.90  # Share caiu 10%+ do entry

        if not pos.has_lock and not pos.has_hedge and time_remaining > 30 and is_losing:
            opp_dir = "Down" if pos.direction == "Up" else "Up"
            opp_token = self._get_no_token(market) if pos.direction == "Up" \
                else self._get_yes_token(market)

            if opp_token:
                # Buscar preço real do lado oposto
                price_b = await self.poly_rest.get_best_ask(opp_token)
                if price_b is None:
                    # Fallback: derivar do yes_price + buffer de spread
                    derived = self.poly_feed.no_price if pos.direction == "Up" \
                        else self.poly_feed.yes_price
                    price_b = derived + 0.02  # Conservative spread

                lock_opp = evaluate_lock(
                    price_a=pos.entry_price,
                    price_b=price_b,
                    direction_b=opp_dir,
                    token_id_b=opp_token,
                    shares_a=pos.shares,
                )

                if lock_opp:
                    order = await execute_lock(
                        self.order_client, opp_token,
                        lock_opp.price_b, lock_opp.shares,
                    )
                    if order:
                        pos.has_lock = True
                        pos.lock_price_b = lock_opp.price_b
                        pos.lock_shares = lock_opp.shares
                        pos.lock_guaranteed_profit = lock_opp.profit_total
                        pos.lock_side_b_direction = opp_dir
                        pos.lock_side_b_token_id = opp_token

                        log.info("lock_profit_executed",
                                 profit=f"${lock_opp.profit_total:.2f}",
                                 a=f"${pos.entry_price:.2f}",
                                 b=f"${lock_opp.price_b:.2f}",
                                 sum=f"${pos.entry_price + lock_opp.price_b:.2f}")
                        return  # Lock acquired, skip hedge

        # Se lock ativo, não precisa de hedge
        if pos.has_lock:
            return

        # ── 3. HEDGE ──
        # Avaliar hedge se: momentum inverteu OU nossa share caiu muito
        share_prices = self.share_buffer.get_prices()
        btc_prices = self.btc_buffer.get_prices()
        if len(share_prices) < 5:
            return

        # Check direto: nossa share caiu abaixo de $0.40?
        our_price = yes_price if pos.direction == "Up" else (1 - yes_price)
        price_dropped = our_price < 0.40

        current_momentum = calc_momentum(share_prices)
        from core.analyzer import analyze_layer2_multiTF
        current_alignment_score, current_alignment = analyze_layer2_multiTF(
            btc_prices, pos.direction
        )

        should_eval = should_evaluate_hedge(pos, current_momentum, current_alignment) or price_dropped
        if should_eval:
            loss_prob = estimate_loss_probability(
                pos, current_momentum,
                current_alignment, pos.entry_alignment
            )

            opposite_direction = "Down" if pos.direction == "Up" else "Up"
            hedge_token = self._get_no_token(market) if pos.direction == "Up" \
                else self._get_yes_token(market)

            if hedge_token:
                hedge_price = 1 - self.poly_feed.yes_price if pos.direction == "Up" \
                    else self.poly_feed.yes_price

                if hedge_price < 0.50:
                    return

                hedge_cost = min(pos.bet_size * 0.6, 3)  # Max 60% da original ou $3
                hedge_return = hedge_cost / hedge_price if hedge_price > 0 else 0

                hedge_opp = HedgeOpportunity(
                    direction=opposite_direction,
                    cost=hedge_cost,
                    potential_return=hedge_return,
                    price=hedge_price,
                    token_id=hedge_token
                )

                should_hedge, reason, savings = should_execute_hedge(
                    pos, hedge_opp, loss_prob, self.hedge_tracker
                )

                if should_hedge:
                    order = await execute_hedge(
                        self.order_client, hedge_token,
                        hedge_cost, hedge_price
                    )
                    if order:
                        # Registrar hedge na posição para PnL correto
                        pos.has_hedge = True
                        pos.hedge_cost = hedge_cost
                        pos.hedge_price = hedge_price
                        pos.hedge_direction = opposite_direction
                        pos.hedge_potential_return = hedge_return

                        self.hedge_tracker.record_hedge(hedge_cost, savings)
                        log.info("hedge_executed",
                                 cost=f"${hedge_cost:.2f}",
                                 savings=f"${savings:.2f}",
                                 loss_prob=f"{loss_prob:.0%}")
                        await self.notifier.notify_hedge(hedge_cost, savings)
                else:
                    log.debug("hedge_skipped", reason=reason)

    async def _phase_resolve(self, market: dict):
        """Fase de resolução: verificar resultado e atualizar stats."""
        pos = self.current_position
        if not pos:
            self.cycle_tracker.end_cycle()
            self.share_buffer.clear()
            return

        # Determinar resultado
        final_price = self.poly_feed.yes_price
        up_won = final_price > 0.5

        # PnL da posição principal
        main_won = (pos.direction == "Up" and up_won) or \
                   (pos.direction == "Down" and not up_won)

        if main_won:
            # Shares * $1 - custo, menos fee 10% sobre lucro
            fee = (1.0 - pos.entry_price) * 0.10 * pos.shares
            main_pnl = pos.shares * 1.0 - pos.bet_size - fee
        else:
            main_pnl = -pos.bet_size

        # PnL do lock profit (se existir) — um lado sempre ganha
        lock_pnl = 0.0
        if pos.has_lock:
            lock_won = (pos.lock_side_b_direction == "Up" and up_won) or \
                       (pos.lock_side_b_direction == "Down" and not up_won)
            lock_cost = pos.lock_price_b * pos.lock_shares
            if lock_won:
                lock_fee = (1.0 - pos.lock_price_b) * 0.10 * pos.lock_shares
                lock_pnl = pos.lock_shares * 1.0 - lock_cost - lock_fee
            else:
                lock_pnl = -lock_cost

        # PnL do hedge (se existir)
        hedge_pnl = 0.0
        if pos.has_hedge:
            hedge_won = (pos.hedge_direction == "Up" and up_won) or \
                        (pos.hedge_direction == "Down" and not up_won)
            if hedge_won:
                hedge_pnl = pos.hedge_potential_return - pos.hedge_cost
            else:
                hedge_pnl = -pos.hedge_cost

        # PnL total
        pnl = main_pnl + lock_pnl + hedge_pnl
        won = pnl > 0

        self.risk_manager.update(pnl)

        result = "WIN" if won else "LOSS"
        log.info("trade_resolved",
                 result=result,
                 pnl=f"${pnl:+.2f}",
                 main=f"${main_pnl:+.2f}",
                 lock=f"${lock_pnl:+.2f}" if pos.has_lock else "none",
                 hedge=f"${hedge_pnl:+.2f}" if pos.has_hedge else "none",
                 pnl_today=f"${self.risk_manager.state.pnl_today:+.2f}",
                 direction=pos.direction)

        # Atualizar resultado no DuckDB
        try:
            self.storage.conn.execute(
                "UPDATE trades SET result = ?, pnl = ?, resolution_price = ? "
                "WHERE timestamp = (SELECT MAX(timestamp) FROM trades)",
                [result, round(pnl, 2), round(final_price, 4)]
            )
        except Exception as e:
            log.error("storage_update_error", error=str(e))

        await self.notifier.notify_result(
            won, pnl, self.risk_manager.state.pnl_today
        )

        # Salvar dados do ciclo no Excel
        self.cycle_collector.end_cycle(
            final_yes_price=final_price,
            pnl=pnl,
        )

        # Cleanup
        self.current_position = None
        self.cycle_tracker.end_cycle()
        self.share_buffer.clear()

    # ── Helpers ──────────────────────────────────────────────────

    def _calculate_delta(self, current_price: float) -> float:
        """Calcula delta: diferença do preço atual vs início do ciclo."""
        prices = self.share_buffer.get_prices()
        if len(prices) < 2:
            return 0.0
        first_price = prices[0]
        return (current_price - first_price) * 10000  # Em "pontos"

    def _find_entry_price(
        self, direction: str, current_price: float, time_remaining: float
    ) -> float | None:
        """
        Encontra o preço de entrada.
        Range: $0.50 a $0.68 (retorno de 33% a 100%).
        """
        if direction == "Up":
            price = current_price
            if price < 0.50 or price > 0.68:
                return None
            return price
        else:
            price = 1 - current_price  # Preço da NO share
            if price < 0.50 or price > 0.68:
                return None
            return price

    def _get_time_remaining(self, market: dict) -> float:
        """Calcula segundos restantes até resolução."""
        # Mercados 5min usam _window_end_ts (timestamp Unix do fim da janela)
        window_end = market.get("_window_end_ts")
        if window_end:
            return window_end - time.time()
        # Fallback para endDateIso
        end_str = market.get("endDateIso", market.get("end_date_iso", ""))
        if not end_str:
            return 0
        try:
            from datetime import datetime as dt, timezone
            end_dt = dt.fromisoformat(end_str.replace("Z", "+00:00"))
            return (end_dt - dt.now(timezone.utc)).total_seconds()
        except Exception:
            return 0

    def _parse_remaining(self, end_str: str) -> float | None:
        try:
            from datetime import datetime as dt, timezone
            end_dt = dt.fromisoformat(end_str.replace("Z", "+00:00"))
            return (end_dt - dt.now(timezone.utc)).total_seconds()
        except Exception:
            return None

    def _get_token_ids(self, market: dict) -> list[str]:
        """Extrai clobTokenIds como lista."""
        token_ids = market.get("clobTokenIds", [])
        if isinstance(token_ids, str):
            import json
            try:
                token_ids = json.loads(token_ids)
            except (ValueError, TypeError):
                token_ids = []
        return token_ids

    def _get_yes_token(self, market: dict) -> str | None:
        """Retorna o token ID de Up/YES (índice 0 em clobTokenIds)."""
        token_ids = self._get_token_ids(market)
        # Mercados 5min BTC: outcomes = ["Up", "Down"] → index 0 = Up
        if token_ids:
            return token_ids[0]
        return None

    def _get_no_token(self, market: dict) -> str | None:
        """Retorna o token ID de Down/NO (índice 1 em clobTokenIds)."""
        token_ids = self._get_token_ids(market)
        # Mercados 5min BTC: outcomes = ["Up", "Down"] → index 1 = Down
        if len(token_ids) > 1:
            return token_ids[1]
        return None
