# Métricas de Sucesso — Configuração Atual

Atualizado: 2026-04-12

## Configuração Validada

| Parâmetro | Valor | Justificativa |
|---|---|---|
| Entry range | $0.50 - $0.62 | Retorno 61-100%, 16+ shares com $10 |
| Sizing (trend 2/3+) | $10 | 16-20 shares, SELL sempre funciona |
| Sizing (trend fraca) | $5 | Conservador |
| Sizing (após losses) | $3 | Proteção de capital |
| Take Profit | 40% gain | $0.55 → vende a $0.77+ |
| EV Optimal | 25% gain, sell > hold×1.30 | Vende quando matematicamente vale |
| Safety Sell | Share >= $0.85 | Max upside $0.15, não vale risco |
| Stop Loss | -35% drop | Sem time guard, ativa imediatamente |
| Emergency Sell | Share < $0.20 | Previne venda a $0.01 |
| Delta Guard | Delta < 10, < 60s, com lucro | 50/50 não vale risco |
| Lock Profit | Perdendo 10%+ | Só proteção, não em trades ganhando |
| Hedge | Share < $0.40 | Fallback quando lock indisponível |

## Métricas de Sucesso

### Meta por sessão de 5 ciclos
- **Win Rate**: >= 60% (baseline: 100% na melhor sessão)
- **PnL total**: >= +$5.00 (baseline: +$12.61)
- **Avg PnL/trade**: >= +$1.00 (baseline: +$2.52)
- **Max single loss**: <= -$4.00 (stop loss limita a ~-$3.65)
- **Full losses ($10)**: 0 (proteções devem prevenir)
- **Early exits**: >= 1 por sessão

### Meta diária (10h operação)
- **PnL**: >= +$100
- **Trades**: 80-100
- **Max drawdown**: <= -$30

## Red Flags (pausar e investigar)
- Loss >= -$8 num único trade (proteção falhou)
- Win rate < 40% em 10 trades
- Early exit com 0 ativações em 10+ ciclos
- 3+ losses consecutivos de -$10 (proteções não ativando)

## Ordem de Prioridade das Proteções
```
1. Emergency Sell (share < $0.20)
2. Safety Sell (share >= $0.85, < 200s)
3. Delta Guard (delta < 10, < 60s, com lucro)
4. Stop Loss (drop >= 35%)
5. Take Profit (gain >= 40%)
6. EV Optimal (gain >= 25%, sell > hold×1.30)
7. Lock Profit (perdendo 10%+, YES+NO < $0.95)
8. Hedge (share < $0.40)
```
