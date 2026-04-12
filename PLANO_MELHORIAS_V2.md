# Plano de Melhorias V2 — Aprovado pelo operador

Data: 2026-04-11
Baseado em: 16 ciclos de dados reais + pesquisa de mercado

---

## Status: O que funciona e NÃO mexer

- Trend-following com multi-timeframe BTC (1m, 2m, 3m) — funcionando
- Entrada na janela 4:30-3:30 quando trend + mercado concordam — funcionando
- Shares entre $0.50-$0.75 — range correto
- Hedge com preço mínimo $0.50 — protege contra apostas ruins
- Stop total após 5 losses consecutivos — proteção de capital

---

## Melhoria 1: Kelly Fracionário por Convicção — APROVADO

### Problema
Sizing atual é $1-$3 baseado em fórmula complexa de 5 fatores.
O sizing não reflete bem a qualidade real do sinal.

### Solução
Usar Kelly fracionário (1/4 Kelly) baseado em:
- **Força da trend**: 3/3 TF concordam = full Kelly, 2/3 = half Kelly
- **Preço da share**: $0.55 = mais edge que $0.72
- **Concordância trend/mercado**: trend e mercado alinhados desde início = mais confiança

### Fórmula
```
edge = (probabilidade estimada - preço da share) / (1 - preço da share)
kelly_fraction = edge * 0.25  (quarter Kelly)
bet_size = min(max(kelly_fraction * bankroll, 1), 3)  # $1-$3
```

### Exemplo
- Share Up @ $0.55, trend 3/3 Up → prob estimada ~65% → edge = (0.65-0.55)/0.45 = 0.22 → kelly = 0.055 → $3 bet
- Share Up @ $0.72, trend 2/3 Up → prob estimada ~60% → edge = (0.60-0.72)/0.28 = -0.43 → skip (edge negativo!)

---

## Melhoria 2: Entry Tardio em Mercado Incerto — APROVADO (com condições)

### Problema
Em mercados laterais ($0.45-$0.55), apostar aos 4:30 é moeda.
Mas NÃO queremos parar de apostar aos 4:00 com share $0.55+ e trend clara.

### Solução
Duas janelas de entrada:

**Janela 1 (4:30-3:30): Entry normal**
- Trend 2/3+ concordam E share > $0.55 → entrar imediato (como hoje)
- Mantém comportamento atual que funciona

**Janela 2 (3:30-2:30): Entry tardio para mercado indeciso**
- Se NÃO entrou na Janela 1 (mercado lateral)
- E agora a share saiu de $0.45-$0.55 para > $0.55
- → Entrar com sizing reduzido ($1-$2 max)

### Regra
```
if janela_1 and trend_clara and share > 0.55:
    entrar_normal()  # Como hoje

elif janela_2 and não_entrou_janela_1 and share > 0.55:
    entrar_tardio(max_size=2)  # Oportunidade tardia
```

---

## Melhoria 3: Lock de Lucro Assimétrico (YES+NO < $1) — PARA ESTUDO

### Dados reais (16 ciclos analisados)
- 93% dos ciclos têm oportunidade (YES+NO < $0.95)
- Lucro médio: $0.44-$2.85 por ciclo (5 shares, descontando 10% fee)
- Melhor caso: $2.85 em um ciclo com 5 shares

### Como funciona
1. Comprar o lado favorecido (ex: Up @ $0.55) na janela normal
2. Se durante o ciclo o mercado reverter e o outro lado ficar barato (Down @ $0.30)
3. Verificar: $0.55 (já pago) + $0.30 = $0.85 < $1.00
4. Comprar Down também → lucro garantido de $0.15/share = $0.75 em 5 shares

### Diferença do hedge atual
- Hedge atual: protege contra perda, pode ter EV negativo
- Lock assimétrico: GARANTE lucro, independente do resultado
- Condição: YES_comprado + NO_disponível < $0.95 (descontando fee)

### Riscos
- Liquidez: shares baratas ($0.15-$0.30) podem ter pouca liquidez
- Spread: bid-ask pode ser 5-10c em shares baratas
- Timing: precisa executar rápido antes do preço subir
- Fee: 10% maker fee come parte do lucro

### Próximo passo
Monitorar em tempo real (sem executar) por 50 ciclos para validar:
- Quantas vezes YES+NO < $0.95 com liquidez real no book
- Quanto tempo a janela de arb dura
- Se o spread bid-ask permite execução lucrativa

---

## Melhoria 4: Take Profit (vender shares antes da resolução) — PARA ESTUDO

### Problema
Reversões de último segundo (-10 delta em 0.5s) destroem trades ganhos.

### Solução
Se a share que compramos subiu significativamente, VENDER antes da resolução:
- Comprou Up @ $0.55 → subiu para $0.85 → vender por $0.85
- Lucro travado: $0.30/share, sem risco de reversão

### Riscos
- Spread no SELL pode ser 3-5c (bid price é menor que ask)
- Taker fee de até 3.15% nos mercados 5min
- Se vender e o mercado continuar subindo, perdeu upside
- Latência: colocar SELL order pode demorar 1-2s

### Regra proposta
```
if share_current >= entry_price * 1.40:  # Lucro de 40%+
    sell_shares()  # Travar lucro
elif share_current <= entry_price * 0.60:  # Perda de 40%+
    sell_shares()  # Stop loss ativo
```

### Próximo passo
Calcular: com spread + taker fee, qual o lucro mínimo que compensa vender?
Se share subiu de $0.55 para $0.80, bid pode ser $0.77 e fee = $0.77 * 3.15% = $0.024.
Net sell = $0.77 - $0.024 = $0.746. Lucro = $0.746 - $0.55 = $0.196/share.
Vs. esperar resolução: se ganhar = $0.45/share, se perder = -$0.55/share.

---

## Prioridade de implementação

| # | Melhoria | Status | Quando |
|---|---|---|---|
| 1 | Kelly fracionário | APROVADO | Próxima sessão |
| 2 | Entry tardio (mercado indeciso) | APROVADO | Próxima sessão |
| 3 | Lock assimétrico YES+NO | MONITORAR | Após 50 ciclos de dados |
| 4 | Take Profit | ESTUDAR | Após cálculo de spread/fee |
