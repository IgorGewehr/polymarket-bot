# Estratégias Futuras — Polymarket BTC 5min

Atualizado: 2026-04-12

---

## Implementadas e Funcionando

### 1. Trend-Following Multi-Timeframe
- BTC slope em 3 janelas (1m, 2m, 3m)
- 2/3 concordando = trend confirmada
- Espera alinhamento entre BTC e mercado Polymarket

### 2. Early Exit (Take Profit / Stop Loss)
- Take profit a +40% gain
- Stop loss a -35% drop (sem time guard)
- Safety sell: share >= $0.85
- Delta guard: mercado lateral nos últimos 60s
- Emergency sell: share < $0.20
- EV optimal: gain >= 25% E sell > hold_ev × 1.30
- SELL com retry (100% → 95% → 90% → 85%)

### 3. Lock Profit
- Compra lado oposto quando perdendo 10%+
- YES + NO < $0.95 = lucro garantido
- Só ativa quando trade está perdendo (não come lucro de wins)

### 4. Hedge
- Compra oposta quando share < $0.40
- Fallback quando lock não disponível

### 5. Maker Rebates
- Bot usa limit orders (maker) = 0 fees + rebate diário
- Já elegível automaticamente

---

## Para Implementar Futuramente

### Prioridade Alta

**Chainlink Oracle como fonte de verdade**
- Comparar preço Chainlink vs Binance para detectar divergências
- O oracle é o mesmo que resolve o mercado
- Baixo esforço, alto impacto

**Filtro de delta mínimo**
- Não entrar quando delta < 10 (mercado 50/50)
- Dados mostram que delta baixo = trades ruins

### Prioridade Média

**CNN-LSTM / XGBoost para predição**
- Treinar nos dados coletados (120+ ciclos com snapshots)
- Substituir slopes lineares por modelo aprendido
- Precisa de mais dados (~500 ciclos)

**Volume analysis**
- BTC volume alto + movimento = convicção real
- BTC volume baixo + movimento = ruído
- Adicionar via Binance WS

### Prioridade Baixa

**Cross-platform arbitrage**
- Polymarket vs Kalshi
- Oportunidades < 2.7s em 2026
- Precisa de infra de baixa latência

**Market making**
- Colocar ordens nos dois lados
- Capturar spread + rebates
- Capital mínimo $500+

---

## Mudanças Estruturais do Polymarket (2026)

- **Dynamic fees**: taker ~3.15%, maker 0% + rebates
- **Remoção delay 500ms**: beneficia bots rápidos
- **Volume $60M/dia**: liquidez suficiente para $10 bets
- **Minimum order**: 5 shares

---

## Fontes
- [Unlocking Edges in 5-Min Crypto Markets](https://medium.com/@benjamin.bigdev/unlocking-edges-in-polymarkets-5-minute-crypto-markets-last-second-dynamics-bot-strategies-and-db8efcb5c196)
- [Beyond Simple Arbitrage: 4 Strategies in 2026](https://medium.com/illumination/beyond-simple-arbitrage-4-polymarket-strategies-bots-actually-profit-from-in-2026-ddacc92c5b4f)
- [Maker Rebates Program](https://docs.polymarket.com/polymarket-learn/trading/maker-rebates-program)
- [Can I Sell Early?](https://docs.polymarket.com/polymarket-learn/FAQ/sell-early)
