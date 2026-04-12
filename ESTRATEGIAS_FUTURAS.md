# Estratégias Futuras — Polymarket BTC 5min

Pesquisa feita em abril/2026 com base em posts do X, Medium, GitHub e docs oficiais do Polymarket.
Nenhuma implementada ainda — aguardando validação do bot atual em live.

---

## 1. Maker Rebates (receita passiva)

**Status**: Já elegível — o bot usa limit orders (maker).

O Polymarket redistribui diariamente as taker fees coletadas para makers proporcionalmente à liquidez fornecida. Makers pagam 0 fees.

**Como funciona:**
- Colocar limit orders (GTC) que ficam no book
- Quando preenchidas, o trade já é lucrativo pela análise
- Adicionalmente, o bot recebe rebate em USDC por ter fornecido liquidez

**O que fazer**: Nada por enquanto. O bot já é maker. Monitorar se os rebates aparecem na carteira após os primeiros dias de operação live.

**Win rate reportado**: 78-85% (market makers puros)
**Retorno**: 1-3%/mês sobre capital + rebates

**Fonte**: [Maker Rebates Program — Polymarket Docs](https://docs.polymarket.com/polymarket-learn/trading/maker-rebates-program)

---

## 2. Oracle Snipe (últimos 10 segundos)

**Status**: Não implementada. Alta competição, win rate live muito abaixo do backtest.

85% da direção do BTC já está determinada ~10s antes do fechamento, mas as odds do Polymarket ainda não refletem totalmente.

**Como funciona:**
1. Esperar até ~10s antes do fechamento do ciclo
2. Consultar o feed Chainlink (mesmo que o Polymarket usa para resolver)
3. Comparar com preço spot da Binance
4. Se direção é clara, colocar maker order a $0.90-$0.95 no lado favorável
5. Lucro de $0.05-$0.10 por contrato se acertar

**Backtest**: 61.9% win rate em 8.876 mercados resolvidos (fev/2026)
**Live**: Caiu para 25-27% em alguns bots — competição brutal nos últimos segundos, slippage real

**Riscos:**
- Taker fees dinâmicas de ~3.15% perto de 50c destroem margem se usar market order
- Bots com execução <100ms dominam esse nicho
- Latência de API pode ser fatal

**Possível implementação futura**: Adicionar como estratégia secundária SOMENTE com maker orders. Nunca como taker. Só entrar se o bot não apostou no ciclo pela estratégia principal.

**Fontes**:
- [Unlocking Edges in Polymarket's 5-Minute Crypto Markets](https://medium.com/@benjamin.bigdev/unlocking-edges-in-polymarkets-5-minute-crypto-markets-last-second-dynamics-bot-strategies-and-db8efcb5c196)
- [Ink Byte on X — Chainlink lag strategy](https://x.com/InkByte/status/2024118051977765290/photo/1)

---

## 3. Chainlink Oracle como fonte de verdade

**Status**: Não implementada. Upgrade de baixo esforço e alto impacto.

O Polymarket resolve mercados usando o feed Chainlink BTC/USD. Atualiza a cada ~10-30s ou em desvios de 0.5%. Se o bot consultar o mesmo oracle, ele sabe EXATAMENTE qual preço será usado na resolução.

**Como integrar:**
- Adicionar feed do Chainlink BTC/USD on-chain (Polygon)
- Comparar com preço Binance em tempo real
- Se divergência > 0.3%, existe edge: o mercado pode estar precificando baseado em dados defasados
- Usar como camada 5 de confirmação no analyzer

**Endpoint**: Chainlink Price Feed no Polygon (0x... contract address)
**Latência**: Feed atualiza em <1s, mas traders demoram ~55s em média para reagir

**Fonte**: [Oracle Lag Sniper — GitHub](https://github.com/JonathanPetersonn/oracle-lag-sniper)

---

## 4. Detecção de Arbitragem YES+NO < $1

**Status**: Não implementada. Oportunidades duram 2.7s em média.

Quando YES + NO somam menos que $1.00, comprar ambos garante lucro na resolução.

**Exemplo:**
- YES = $0.48, NO = $0.50 → total = $0.98
- Comprar 1 YES + 1 NO = $0.98
- Resolução paga $1.00 → lucro = $0.02 (2% risk-free)

**Realidade em 2026:**
- Oportunidades duram 2.7 segundos (era 12.3s em 2024)
- 73% dos lucros capturados por bots <100ms
- Precisa de infraestrutura de co-location para competir

**Possível implementação**: Detectar passivamente enquanto o bot monitora o mercado. Se YES+NO < $0.97, logar a oportunidade. Não priorizar implementação de execução — competição é extrema.

**Fontes**:
- [Beyond Simple Arbitrage: 4 Strategies in 2026](https://medium.com/illumination/beyond-simple-arbitrage-4-polymarket-strategies-bots-actually-profit-from-in-2026-ddacc92c5b4f)
- [Arbitrage Bots Dominate Polymarket](https://finance.yahoo.com/news/arbitrage-bots-dominate-polymarket-millions-100000888.html)

---

## 5. Market Making (dois lados do book)

**Status**: Não implementada. Requer capital maior e lógica diferente.

Colocar limit orders de compra e venda simultaneamente nos dois lados (YES e NO), capturando o spread.

**Como funciona:**
- Bid YES a $0.48, Ask YES a $0.52 (spread de $0.04)
- Se ambos preenchem, lucro = $0.04 por par
- Monitorar feeds externos (Binance) para ajustar preços continuamente
- Loop completo em <100ms para ser competitivo

**Win rate reportado**: 78-85%
**Retorno**: 1-3%/mês + maker rebates
**Capital mínimo**: $500+ para ser relevante no book

**Riscos:**
- Se o preço move rápido num sentido, um lado preenche e o outro não (inventory risk)
- Precisa de gestão de inventário sofisticada
- Precisa de baixa latência

**Possível implementação**: Fase 3 do roadmap. Requer reescrita do order client para suportar gestão de múltiplas ordens simultâneas.

**Fonte**: [Automated Market Making on Polymarket](https://news.polymarket.com/p/automated-market-making-on-polymarket)

---

## 6. CNN-LSTM / ML para predição direcional

**Status**: Não implementada. Fase 2 do roadmap (após $500+ de dados).

Rede neural treinada em indicadores técnicos para prever direção do BTC nos próximos 5 minutos.

**O que existe no mercado:**
- CNN-LSTM com 42+ indicadores, retraining horário em candles 5min da Coinbase
- "Momentum Guard" que detecta condições ruins
- "Bot Brain" que bloqueia entries de baixa qualidade
- Backtest: 60-70% win rate
- **Live: 25-27%** — gap enorme

**Por que esperar:**
- Precisa de dados reais do bot (coleta no Excel já ativa)
- Os 57 trades manuais + dados futuros alimentam XGBoost primeiro (mais simples, mais interpretável)
- CNN-LSTM requer GPU para retraining e mais complexidade operacional
- O gap backtest→live de outros bots sugere que overfitting é o problema #1

**Roadmap:**
1. Primeiro: XGBoost nos dados coletados (substituir pesos fixos por aprendidos)
2. Depois: Feature importance para descobrir quais indicadores importam
3. Por último: LSTM se XGBoost não for suficiente

**Fontes**:
- [Polymarket AI BTC Bot — CNN-LSTM](https://www.polytraderbot.com/crypto5min.html)
- [AI-Augmented Arbitrage in Short-Duration Prediction Markets](https://medium.com/@gwrx2005/ai-augmented-arbitrage-in-short-duration-prediction-markets-live-trading-analysis-of-polymarkets-8ce1b8c5f362)

---

## 7. Cross-Platform Arbitrage (Polymarket vs Kalshi)

**Status**: Quase inexistente em 2026. Não priorizar.

Explorar diferenças de preço entre Polymarket e Kalshi para o mesmo evento.

**Realidade**: Ambas plataformas já têm bots que equalizam preços em <1s. Spread residual não cobre fees + latência.

**Fonte**: [Polymarket-Kalshi BTC Arbitrage Bot — GitHub](https://github.com/CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot)

---

## Mudanças estruturais do Polymarket em 2026

### Dynamic Fees (IMPORTANTE)
- Taker fees variáveis: ~3.15% máximo quando odds perto de 50c
- Mataram arbitragem de latência
- Makers pagam 0 fees
- **Nosso bot já é maker** — não afetado

### Remoção do delay de 500ms
- Polymarket removeu o delay artificial de 500ms em orders
- Beneficia bots rápidos para market making
- Não afeta nossa estratégia (entramos minutos antes, não milissegundos)

### Volume: $60M/dia nos mercados 5min
- 288 ciclos/dia × ~$208K volume médio por ciclo
- Liquidez suficiente para nossas apostas de $1-$3

---

## Prioridade de implementação futura

| # | Estratégia | Esforço | Impacto | Quando |
|---|---|---|---|---|
| 1 | Chainlink como camada 5 | Baixo | Alto | Após 1 semana live |
| 2 | Monitorar maker rebates | Zero | Médio | Já ativo |
| 3 | Detecção passiva YES+NO < $1 | Baixo | Baixo | Após 2 semanas live |
| 4 | Oracle snipe (maker only) | Médio | Médio | Após 1 mês live |
| 5 | XGBoost nos dados coletados | Médio | Alto | Após $500+ de dados |
| 6 | Market making | Alto | Alto | Fase 3 ($500+ capital) |
| 7 | CNN-LSTM | Alto | Incerto | Fase 4 (se XGBoost insuficiente) |
