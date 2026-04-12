# Análise de Sessões Live — Polymarket BTC 5min Bot

## Sessão Final (10 trades) — MELHOR RESULTADO
- **Win Rate: 90%** (9W / 1L)
- **PnL: +$4.35** (avg +$0.43/trade)
- Lock profit: 5 executados, todos positivos
- Hedge: 1 ativado
- Early exit: 0 (precisa de refinamento)

### Trade-by-trade
```
#1  WIN  Up  main=-3.00 lk=+3.17  pnl=+0.17  ← Lock salvou loss!
#2  WIN  Up  main=+1.96 lk=-1.65  pnl=+0.30
#3  WIN  Up  main=+2.21 lk=-1.91  pnl=+0.30
#4  LOSS Up  main=-2.00           pnl=-2.00  ← Reversão último segundo
#5  WIN  Up  main=+0.60           pnl=+0.60
#6  WIN  Down main=+2.04 lk=-1.16 pnl=+0.88
#7  WIN  Up  main=+1.59           pnl=+1.59
#8  WIN  Up  main=+0.70           pnl=+0.70
#9  WIN  Down main=+2.70 lk=-2.34 pnl=+0.36
#10 WIN  Up  main=+1.45           pnl=+1.45
```

## Dados Agregados (89 ciclos, 58 trades)
- Win rate geral: ~50-57%
- Lock profit contribuiu +$0.78 líquido em sessão de 22 trades
- Take profit teria salvado 55% das losses
- Take profit cortaria wins em ~$0.15/trade (aceitável)
- Net impact de TP estimado: +$14 positivo

## Problemas Identificados
1. **Reversão último segundo** — delta explode em 0.5s, destrói trades ganhos
2. **Early exit nunca ativa** — precisa de >= 5 shares para SELL
3. **Shares a $0.85+ com 2min restantes** — deveria vender (lucro ~70% vs risco de reversão)

## Próximas Melhorias Prioritárias
1. **Safety sell**: se share >= $0.85 com < 2min restantes → vender
2. **Delta guard**: se delta < 15 nos últimos 60s → vender para garantir
3. **Lower TP threshold**: vender quando ganho >= 25% (em vez de 30%)
