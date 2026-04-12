# Polymarket BTC Bot — Estratégia Completa

---

## 1. Contexto e modelo de mercado

O Polymarket oferece mercados de previsão de 5 minutos sobre o preço do Bitcoin. A cada ciclo de 5 minutos, um novo mercado abre com a pergunta "BTC estará acima de $X às HH:MM?". O trader compra shares YES (acredita que sobe) ou NO (acredita que cai), pagando entre $0.01 e $0.99 por share. Se acertar, recebe $1.00 por share. Se errar, perde o valor pago.

O mercado opera via CLOB (Central Limit Order Book) — existe um book de ordens com bids e asks. As shares são negociáveis durante todo o ciclo de 5 minutos, e o preço flutua conforme a percepção do mercado muda.

**Ciclo temporal do mercado:**

```
0:00  ─────── 1:00 ─────── 2:00 ─────── 3:00 ─────── 4:00 ─────── 5:00
 │              │              │              │              │         │
 Mercado abre   Coleta dados   Análise ativa  JANELA ENTRADA Monitorar Resolve
                                               4:30 → 3:30
```

---

## 2. Resultados históricos — base de calibração

Dados coletados de 57 trades manuais em ~8 horas de operação:

| Métrica | Valor |
|---|---|
| Total de trades | 57 |
| Win rate geral | 78.9% (45 wins / 12 losses) |
| Total apostado | $68.00 |
| Total retorno | $96.59 |
| Lucro líquido | $28.59 |
| ROI geral | 42.0% |

### 2.1 Performance por tempo de entrada

| Tempo | Trades | Win rate | ROI |
|---|---|---|---|
| 4:30 | 4 | 100% | 80.5% |
| 4:00 | 6 | 100% | 57.2% |
| 3:30 | 22 | 73% | 53.1% |
| 2:30 | 13 | 69% | 31.3% |
| 1:30 | 8 | 75% | 15.6% |
| 0:30 | 4 | 100% | 7.6% |

**Insight:** Entradas entre 4:30 e 4:00 tiveram 100% de win rate. O mercado ainda não precificou a tendência, as odds estão favoráveis. A partir de 2:30 o ROI cai rapidamente porque as shares já estão caras.

### 2.2 Performance por direção

| Direção | Trades | Win rate | Lucro |
|---|---|---|---|
| Up | 36 | 89% | $16.18 |
| Down | 21 | 62% | $12.41 |

**Insight:** Apostar em Up é significativamente mais seguro. Down tem mais volatilidade e inversões inesperadas. O bot deve ser mais conservador em trades Down.

### 2.3 Performance por faixa de delta

| Delta | Trades | Win rate | ROI |
|---|---|---|---|
| 0-20 | 13 | 92% | 85.2% |
| 20-40 | 23 | 78% | 41.6% |
| 40-60 | 12 | 58% | 22.3% |
| 60-100 | 6 | 83% | 13.7% |
| 100+ | 3 | 100% | 9.0% |

**Insight crítico:** Delta baixo = melhor performance. Quando o delta é alto, o preço da share já absorveu o movimento e o retorno é menor. O edge está em entrar cedo quando o delta ainda é baixo e as odds são baratas.

### 2.4 Combinações perigosas

| Combo | Win rate | Observação |
|---|---|---|
| Down @ 3:30 | 56% | Zona de risco — reduzir sizing |
| Down @ 2:30 | 50% | Moeda — evitar |
| Up @ 4:00+ | 100% | Sweet spot — sizing máximo |
| Up @ 3:30 | 85% | Sólido — sizing normal |

---

## 3. Motor de análise — 4 camadas

### Camada 1: Trend de 5 minutos (peso 30%)

Calcula a regressão linear dos últimos 10 ticks (amostrados a cada 30s) do preço da share YES no mercado atual. O slope indica direção e força.

```python
slope = np.polyfit(timestamps[-10:], prices[-10:], 1)[0]
trend_5m = "Up" if slope > 0 else "Down"
trend_strength = abs(slope) * 1000  # normalizado
```

**Score:** +2 se slope forte na direção do trade, +1 se slope moderado, 0 se flat, -1 se contra.

### Camada 2: Multi-timeframe alignment (peso 30%)

Analisa a tendência do preço SPOT do BTC (não da share) em 3 timeframes simultâneos:
- **5 minutos**: slope dos últimos 10 ticks
- **15 minutos**: slope dos últimos 30 ticks  
- **30 minutos**: slope dos últimos 60 ticks

```python
slope_5m  = calc_slope(btc_prices, window=10)
slope_15m = calc_slope(btc_prices, window=30)
slope_30m = calc_slope(btc_prices, window=60)

alignment = sum(1 for s in [slope_5m, slope_15m, slope_30m] if s > 0)
# alignment = 3 → forte Up, 0 → forte Down, 1-2 → conflito
```

**Score:** +2 se todos concordam na direção do trade, +1 se 2/3 concordam, -1 se 2/3 discordam, -2 se todos discordam.

**Este é o maior upgrade sobre o trading manual** — o humano só olha 5 minutos. O bot vê se a queda de 5min é um pullback dentro de uma alta de 30min, ou se é o início de uma queda maior.

### Camada 3: Mean reversion — Bollinger Bands (peso 20%)

Calcula a média móvel de 20 períodos e 2 desvios padrão do preço spot do BTC. Quando o preço toca a banda inferior, é sinal estatístico de reversão pra cima (e vice-versa).

```python
sma_20 = btc_prices[-20:].mean()
std_20 = btc_prices[-20:].std()
upper_band = sma_20 + 2 * std_20
lower_band = sma_20 - 2 * std_20
current = btc_prices[-1]

z_score = (current - sma_20) / std_20
```

**Score:** +2 se preço tocou banda oposta (reversão provável a favor), +1 se próximo da banda, 0 se no meio, -1 se tocou banda na direção (tendência pode ter esgotado).

**Isto quantifica o instinto de "caiu demais, vai voltar".** Z-score > 2 ou < -2 = extremo estatístico.

### Camada 4: Momentum — segunda derivada (peso 20%)

Mede se a tendência está acelerando ou desacelerando. Não basta saber que "está caindo" — importa se está caindo mais rápido (continuação) ou mais devagar (possível reversão).

```python
# Taxa de variação dos últimos 5 ticks
roc_recent = prices[-1] - prices[-3]
roc_prior  = prices[-3] - prices[-5]

acceleration = roc_recent - roc_prior
# > 0 = acelerando na direção atual
# < 0 = desacelerando (possível reversão)
```

**Score:** +1 se momentum confirma a direção do trade, 0 se neutro, -1 se momentum está contra (trend enfraquecendo).

### Score final de confiança

```python
score = (
    layer1_score * 0.30 +  # Trend 5min
    layer2_score * 0.30 +  # Multi-timeframe
    layer3_score * 0.20 +  # Bollinger
    layer4_score * 0.20    # Momentum
)
# Range teórico: -2.0 a +2.0
# Normalizado para -6 a +6 para facilitar thresholds
confidence = score * 3
```

---

## 4. Regras de entrada — filtros absolutos

O bot NUNCA aposta se qualquer uma dessas condições for verdadeira:

| Regra | Motivo | Fonte |
|---|---|---|
| Delta < 4 | Movimento insuficiente, ruído | Regra do operador |
| Retorno < 5% | Não compensa o risco (dados mostram que trades entre 5-10% ainda são lucrativos) | Dados históricos |
| Confidence entre -2 e +2 | Sinal muito ambíguo | Modelo de análise |
| Tempo restante < 1:00 | ROI histórico de 7.6%, risco alto | Dados (0:30 = 7.6% ROI) |
| Perdas consecutivas >= 3 | Cooldown para evitar tilt | Risk management |

### Janela de entrada ideal

```
Prioridade 1: 4:30 → 4:00 (win rate 100%, ROI 58-81%)
Prioridade 2: 4:00 → 3:30 (win rate 73-100%, ROI 53-57%)
Prioridade 3: 3:30 → 2:30 (win rate 69-73%, ROI 31-53%)
Evitar:        < 2:00 (ROI cai rapidamente)
```

### Lógica de entrada na janela

O bot não entra cego no primeiro segundo da janela. Ele usa uma estratégia de "threshold com deadline":

```
4:30 — janela abre
  │
  ├─ Amostrar preço da share a cada 2 segundos
  ├─ Calcular preço-alvo baseado no retorno desejado
  ├─ Se share atinge preço bom → ENTRAR
  │
4:00 — se não entrou, relaxar threshold em 10%
  │
  ├─ Continuar amostrando
  ├─ Se share atinge novo threshold → ENTRAR
  │
3:35 — DEADLINE: entrar no preço atual se confidence >= 4
  │
3:30 — se não entrou, PULAR este ciclo
```

---

## 5. Sistema de sizing dinâmico — $1 a $3

### Fórmula de sizing

Cinco fatores calculam um score bruto que mapeia para o valor da aposta:

```python
def calculate_bet_size(confidence, expected_return, time_slot, direction, recent_losses):
    
    # Fator 1: Confiança (peso 40%)
    conf_score = abs(confidence) / 6  # normaliza 0-1
    
    # Fator 2: Retorno esperado (peso 25%)
    if expected_return >= 30:
        ret_score = 1.0
    elif expected_return >= 15:
        ret_score = 0.6
    else:
        ret_score = 0.3
    
    # Fator 3: Bonus de tempo (peso 20%)
    time_bonus = {
        "4:30": 1.0, "4:00": 0.9, "3:30": 0.7,
        "2:30": 0.5, "1:30": 0.3
    }[time_slot]
    
    # Fator 4: Bonus de direção (peso 15%)
    dir_bonus = 1.0 if direction == "Up" else 0.65
    
    # Fator 5: Penalty por losses recentes
    loss_penalty = max(0.2, 1 - (recent_losses * 0.2))
    
    # Score composto
    raw = (
        conf_score * 0.40 +
        ret_score  * 0.25 +
        time_bonus * 0.20 +
        dir_bonus  * 0.15
    ) * loss_penalty
    
    # Mapeamento para valor
    if raw >= 0.70:
        return 3  # Alta confiança
    elif raw >= 0.45:
        return 2  # Confiança média
    else:
        return 1  # Confiança baixa
```

### Tabela de referência rápida

| Cenário | Confidence | Direção | Tempo | Sizing |
|---|---|---|---|---|
| Tudo alinhado, entrada cedo | +5 a +6 | Up | 4:30-4:00 | $3 |
| Bom setup, timing normal | +4 a +5 | Up | 3:30 | $2 |
| Setup ok, direção arriscada | +4 | Down | 3:30 | $1-2 |
| Sinal fraco mas positivo | +3 | Up | 2:30 | $1 |
| Down em horário arriscado | +3 | Down | 3:30 | $1 |
| Após 2 losses seguidos | qualquer | qualquer | qualquer | $1 (penalty) |

---

## 6. Sistema de hedge — proteção EV-driven

### Quando o bot considera hedge

Após entrar numa posição, o bot monitora continuamente (a cada 2-3 segundos) o estado do mercado. Se detecta reversão, calcula se vale hedgear.

### Condições para trigger de avaliação de hedge

```python
def should_evaluate_hedge(position, current_market):
    # O momentum inverteu?
    momentum_reversed = (
        position.direction == "Up" and current_momentum < -threshold
    ) or (
        position.direction == "Down" and current_momentum > threshold
    )
    
    # Multi-timeframe mudou?
    tf_changed = current_alignment != position.entry_alignment
    
    # Delta inverteu?
    delta_reversed = current_delta_direction != position.direction
    
    return momentum_reversed or tf_changed or delta_reversed
```

### 4 condições para execução do hedge

TODAS devem ser verdadeiras para o bot executar o hedge:

```python
def should_execute_hedge(position, hedge_opportunity, daily_stats):
    
    # 1. Probabilidade de perder > 55%
    #    (se é 50/50, a original ainda pode ganhar)
    loss_probability = estimate_loss_prob(position, current_market)
    if loss_probability < 0.55:
        return False, "Prob. de perder baixa — segurar"
    
    # 2. Máximo 2 hedges por dia
    #    (evita erosão por over-hedging)
    if daily_stats.hedge_count >= 2:
        return False, "Limite diário de hedges atingido"
    
    # 3. Odds do hedge > 15% retorno
    #    (hedge em odds ruins é jogar dinheiro fora)
    hedge_roi = (hedge_opportunity.return - hedge_opportunity.cost) / hedge_opportunity.cost
    if hedge_roi < 0.15:
        return False, "Odds do hedge ruins"
    
    # 4. Hedge melhora o EV
    #    (cálculo matemático, não emocional)
    ev_without = calc_ev_no_hedge(position, loss_probability)
    ev_with = calc_ev_with_hedge(position, hedge_opportunity, loss_probability)
    
    if ev_with <= ev_without:
        return False, "Hedge piora o EV"
    
    return True, f"Hedge aprovado — savings ${ev_with - ev_without:.2f}"
```

### Matemática do hedge

```
Cenário do operador:
- Apostou $5 em DOWN, retorno potencial $8.77
- Trend inverteu, prob. de perder ~65%

SEM HEDGE:
  Se ganha (35%): +$3.77 lucro
  Se perde (65%): -$5.00
  EV = 0.35 × 3.77 + 0.65 × (-5.00) = -$1.93

COM HEDGE ($3 em UP, retorno $5.50):
  Se DOWN ganha (35%): +$3.77 - $3.00 (hedge perdido) = +$0.77
  Se UP ganha (65%): +$2.50 - $5.00 (original perdida) = -$2.50
  EV = 0.35 × 0.77 + 0.65 × (-2.50) = -$1.36

  Savings: -$1.36 - (-$1.93) = +$0.57 ← hedge compensa
```

### Regra anti-erosão

```python
# Tracking de impacto do hedging no lucro
class HedgeTracker:
    def __init__(self):
        self.hedges_today = 0
        self.total_hedge_cost = 0
        self.total_hedge_savings = 0
        self.last_hedge_time = None
    
    def can_hedge(self):
        # Max 2 por dia
        if self.hedges_today >= 2:
            return False
        # Cooldown de 15 min entre hedges
        if self.last_hedge_time and (now - self.last_hedge_time) < 900:
            return False
        # Se hedges hoje já custaram mais que salvaram, parar
        if self.total_hedge_cost > self.total_hedge_savings * 1.5:
            return False
        return True
```

---

## 7. Risk management

### Limites diários

| Parâmetro | Valor | Motivo |
|---|---|---|
| Max loss diário | $15 | ~75% do lucro médio diário esperado ($20) |
| Max trades/dia | 200 | ~1 a cada 2-3 ciclos de 5min em 10h |
| Max trades/hora | 12 | Máximo de ciclos por hora |
| Max sizing após drawdown | $1 | Se loss > $8, sizing cai para $1 fixo |
| Cooldown após 3 losses | 15 min | Pausa para evitar tilt mecânico |
| Max hedges/dia | 2 | Anti-erosão |
| Pausa se API falhar | 5 min | Proteção contra dados corrompidos |

### Trailing stop diário

```python
class DailyRiskManager:
    def __init__(self, max_daily_loss=15, starting_balance=0):
        self.pnl_today = 0
        self.peak_pnl = 0
        self.consecutive_losses = 0
        self.trades_today = 0
        self.cooldown_until = None
    
    def update(self, trade_result):
        self.pnl_today += trade_result
        self.trades_today += 1
        
        if trade_result > 0:
            self.consecutive_losses = 0
            self.peak_pnl = max(self.peak_pnl, self.pnl_today)
        else:
            self.consecutive_losses += 1
        
    def can_trade(self):
        # Hard stop: loss máximo
        if self.pnl_today <= -self.max_daily_loss:
            return False, "Max loss diário atingido"
        
        # Trailing: se caiu $8 do pico do dia, sizing = $1
        if self.peak_pnl - self.pnl_today >= 8:
            return True, "Sizing reduzido para $1 (drawdown)"
        
        # Cooldown após losses consecutivos
        if self.consecutive_losses >= 3:
            self.cooldown_until = now + timedelta(minutes=15)
            return False, "Cooldown 15min após 3 losses"
        
        if self.cooldown_until and now < self.cooldown_until:
            return False, f"Em cooldown até {self.cooldown_until}"
        
        return True, "OK"
```

---

## 8. Loop principal do bot

```python
async def main_loop():
    """
    Loop principal — roda indefinidamente.
    A cada ciclo de 5 minutos do Polymarket, executa o pipeline completo.
    """
    while True:
        # 1. Identificar o mercado ativo
        market = await polymarket.get_active_btc_market()
        if not market:
            await asyncio.sleep(5)
            continue
        
        time_remaining = market.resolution_time - now()
        
        # 2. Fase de coleta (5:00 → 4:30)
        # Coletar ticks de preço a cada 2-3 segundos
        if time_remaining > 270:  # > 4:30
            tick = await collect_tick(market)
            price_buffer.append(tick)
            await asyncio.sleep(2)
            continue
        
        # 3. Fase de análise (4:30 → 3:30)
        if time_remaining > 210 and not position_open:  # 4:30 → 3:30
            
            # Verificar risk manager
            can_trade, reason = risk_manager.can_trade()
            if not can_trade:
                logger.info(f"Bloqueado: {reason}")
                await asyncio.sleep(30)
                continue
            
            # Rodar 4 camadas de análise
            analysis = analyze(price_buffer, btc_feed)
            
            # Filtros absolutos
            if analysis.delta < 4:
                continue
            if analysis.expected_return < 0.05:
                continue
            if abs(analysis.confidence) < 3:
                continue
            
            # Calcular sizing
            bet_size = calculate_bet_size(
                confidence=analysis.confidence,
                expected_return=analysis.expected_return,
                time_slot=get_time_slot(time_remaining),
                direction=analysis.direction,
                recent_losses=risk_manager.consecutive_losses
            )
            
            # Buscar melhor preço na janela
            best_price = await find_best_entry(
                market, analysis.direction,
                target_return=analysis.expected_return,
                deadline_seconds=time_remaining - 210  # até 3:30
            )
            
            if best_price:
                # EXECUTAR TRADE
                position = await execute_trade(
                    market, analysis.direction,
                    amount=bet_size, price=best_price
                )
                position_open = True
                logger.info(f"TRADE: {analysis.direction} ${bet_size} @ {best_price}")
        
        # 4. Fase de monitoramento (3:30 → 0:00)
        if position_open and time_remaining <= 210:
            
            # Monitorar posição a cada 2 segundos
            current_state = await monitor_position(position, market)
            
            # Avaliar hedge se condições mudaram
            if should_evaluate_hedge(position, current_state):
                hedge_opp = await find_hedge_opportunity(market, position)
                
                if hedge_opp and should_execute_hedge(position, hedge_opp, daily_stats):
                    await execute_hedge(hedge_opp)
                    hedge_tracker.record_hedge(hedge_opp)
            
            await asyncio.sleep(2)
            continue
        
        # 5. Resolução do mercado
        if time_remaining <= 0:
            result = await wait_for_resolution(market)
            risk_manager.update(result.pnl)
            db.log_trade(position, result)
            position_open = False
            
            logger.info(f"RESULTADO: {'WIN' if result.won else 'LOSS'} ${result.pnl:.2f}")
            logger.info(f"P&L hoje: ${risk_manager.pnl_today:.2f}")
        
        await asyncio.sleep(2)
```

---

## 9. Logging e aprendizado

### Dados logados em cada trade

```python
trade_log = {
    "timestamp": datetime.now(),
    "market_id": market.id,
    "direction": "Up",
    "bet_size": 2,
    "entry_price": 0.55,
    "entry_time_remaining": 245,  # segundos
    "confidence_score": 4.2,
    "layer1_trend": 1.5,
    "layer2_alignment": 2,
    "layer3_bollinger": 0.8,
    "layer4_momentum": 0.5,
    "delta_at_entry": 28,
    "btc_price_at_entry": 84523.50,
    "expected_return": 0.45,
    "hedge_executed": False,
    "hedge_cost": None,
    "result": "WIN",
    "pnl": 0.90,
    "resolution_price": 84612.00
}
```

Estes dados alimentam o backtesting e, futuramente, o modelo de ML que substitui as regras fixas por pesos aprendidos.

---

## 10. Stack técnica

### Fase 1 (Atual)

| Componente | Tecnologia | Função |
|---|---|---|
| Runtime | Python 3.12 + asyncio | Event loop principal |
| Dados em memória | Polars | Cálculos de janela deslizante |
| Indicadores | NumPy + cálculos custom | Bollinger, slope, momentum |
| Storage | DuckDB | Time-series e logs de trades |
| Cache | Redis | Ticks recentes em RAM |
| API Polymarket | py-clob-client + httpx | CLOB (ordens) + Gamma API (mercados) + WebSocket |
| API BTC Price | WebSocket Binance | Feed de preço spot |
| Deploy | Docker + VPS | Uptime 24/7 |
| Monitor | Logs estruturados + alertas Telegram | Notificações de trades |

### Fase 2 (Após $500+ de dados)

- XGBoost treinado nos dados de trades para substituir pesos fixos por pesos aprendidos
- Feature importance analysis para descobrir quais indicadores realmente importam
- Backtesting engine para testar estratégias contra dados históricos

### Fase 3 (Após validação do modelo)

- APIs pagas: order book depth, funding rates, open interest
- Mais features para o modelo: volume, spread bid-ask, velocidade de preenchimento
- LLM para análise de sentimento de notícias crypto (opcional, alto custo)

### Fase 4 (Expansão)

- CCXT para conexão com Bybit/Binance (mesma interface, múltiplas corretoras)
- PyTorch para modelos mais complexos (LSTM, Transformer)
- Infrastructure: Kubernetes para scaling horizontal

---

## 11. Estrutura de arquivos do projeto

```
polymarket-bot/
├── config/
│   ├── settings.py          # Constantes, thresholds, API keys
│   └── markets.py           # Config de mercados monitorados
├── core/
│   ├── engine.py             # Loop principal (main_loop)
│   ├── analyzer.py           # 4 camadas de análise
│   ├── sizing.py             # Sistema de sizing $1-$3
│   ├── hedger.py             # Sistema de hedge
│   └── risk_manager.py       # Risk management
├── data/
│   ├── feeds.py              # WebSocket Polymarket + Binance
│   ├── price_buffer.py       # Buffer circular de ticks
│   └── storage.py            # DuckDB interface
├── execution/
│   ├── order_client.py       # py-clob-client — ordens no CLOB (EIP-712 signing)
│   └── __init__.py
├── monitoring/
│   ├── telegram_bot.py       # Alertas via Telegram
│   ├── dashboard.py          # Dashboard web (FastAPI)
│   └── logger.py             # Logging estruturado
├── backtesting/
│   ├── simulator.py          # Replay de trades históricos
│   └── optimizer.py          # Otimização de parâmetros
├── main.py                   # Entry point
├── requirements.txt
├── Dockerfile
└── docker-compose.yml        # Bot + Redis + DuckDB
```

---

## 12. Métricas de sucesso

### Meta diária

| Métrica | Target | Mínimo aceitável |
|---|---|---|
| Lucro diário | $20 | $10 |
| Win rate | 75%+ | 65% |
| ROI por trade | 30%+ | 15% |
| Max drawdown diário | < $10 | < $15 |
| Trades executados | 50-100 | 30 |
| Hedges usados | 0-1 | max 2 |
| Uptime | 99%+ | 95% |

### Sinais de que algo está errado

- Win rate caiu abaixo de 60% por 3 dias seguidos → pausar e revisar parâmetros
- ROI abaixo de 10% consistentemente → odds estão caras, mercado mudou
- Hedges chegando ao limite diário frequentemente → modelo de análise não está detectando reversões a tempo
- Max loss diário atingido antes das 4h de operação → volatilidade anormal, reduzir sizing global

---

## 13. Resumo executivo da estratégia

1. **Coletar** ticks de preço a cada 2-3 segundos via WebSocket
2. **Analisar** com 4 camadas (trend, multi-TF, Bollinger, momentum) gerando score -6 a +6
3. **Filtrar** por regras absolutas (delta >= 4, retorno >= 10%, confidence >= 3)
4. **Dimensionar** aposta $1-$3 baseado em 5 fatores ponderados
5. **Entrar** na janela 4:30-3:30, buscando melhor preço com threshold + deadline
6. **Monitorar** posição aberta continuamente
7. **Hedgear** somente se 4 condições simultâneas forem satisfeitas (EV-driven)
8. **Registrar** tudo para aprendizado contínuo e backtesting futuro
