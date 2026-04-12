# Métricas de Sucesso — Monitoramento Live

## Estratégias e Métricas

### 1. Trend-Following (Entry principal)
- **Métrica**: Win rate dos trades sem proteção (main > 0)
- **Meta**: >= 55%
- **Baseline**: 59% (112 ciclos)
- **O que avaliar**: Direção correta na maioria dos trades

### 2. Safety Sell (share >= $0.85)
- **Métrica**: Quantas vezes ativou / quantas vezes poderia ter ativado
- **Meta**: >= 1 ativação a cada 5 ciclos (quando share sobe alto)
- **O que avaliar**: Se está vendendo antes de reversões de último segundo
- **Atenção**: Só funciona com >= 5 shares (entry <= $0.60 com $3 bet)

### 3. Take Profit (gain >= 30%)
- **Métrica**: PnL dos trades com TP vs PnL sem TP
- **Meta**: TP trades devem ter PnL positivo médio >= $0.50
- **O que avaliar**: Se está cortando wins muito cedo

### 4. Delta Guard (delta < 10 nos últimos 60s)
- **Métrica**: Quantas losses evitadas em mercados laterais
- **Meta**: >= 1 ativação por sessão de 10 ciclos
- **O que avaliar**: Está vendendo quando mercado é 50/50 perto do fim

### 5. Stop Loss (drop >= 35%)
- **Métrica**: Loss média com SL vs loss média sem SL
- **Meta**: Loss média < $1.50 (vs $3.00 sem proteção)
- **Atenção**: Só funciona com >= 5 shares

### 6. Lock Profit (YES+NO < payout)
- **Métrica**: PnL dos trades com lock (sempre deveria ser positivo)
- **Meta**: Lock PnL > $0 em >= 80% dos trades com lock
- **O que avaliar**: Lock está sendo usado no momento certo (não quando safety sell seria melhor)

### 7. Hedge (compra oposta quando share < $0.40)
- **Métrica**: Redução de loss ($loss_sem_hedge - $loss_com_hedge)
- **Meta**: Hedge salva >= $1.00 por ativação
- **O que avaliar**: Não está hedgeando quando deveria estar vendendo

### 8. Sizing ($3 com trend 2/3+)
- **Métrica**: PnL/trade proporcional ao sizing
- **Meta**: Avg PnL/trade > $0.30
- **O que avaliar**: $3 não está amplificando losses demais

## Métrica Global
- **Win Rate**: >= 60%
- **PnL/trade**: >= +$0.30
- **PnL/hora**: >= +$2.00 (4 trades/hora × $0.50)
- **Max Loss Streak**: <= 3 seguidos
- **Max Single Loss**: <= $2.00 (com proteções ativas)

## Red Flags (parar e investigar)
- Win rate < 45% em 10 trades
- Early exit com 0 ativações em 10+ ciclos
- Lock profit PnL negativo consistente
- 3+ losses full de $3.00 sem proteção
