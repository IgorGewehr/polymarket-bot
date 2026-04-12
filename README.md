# Polymarket BTC 5min Trading Bot

Bot automatizado para mercados de previsão de 5 minutos sobre Bitcoin no Polymarket. Trend-following com multi-timeframe analysis, early exit (take profit / stop loss), lock profit e hedge.

**Melhor sessão: 5/5 wins, +$12.61, 100% WR, avg +$2.52/trade**

## Arquitetura

```
main.py → Engine (loop 2s) + Dashboard (FastAPI :8888)
              ├── Trend Detection (multi-TF: 1m/2m/3m BTC via Binance)
              ├── Entry Logic (trend-following, $0.50-$0.62 range)
              ├── Early Exit (safety sell, take profit, stop loss, delta guard)
              ├── Lock Profit (compra assimétrica YES+NO quando perdendo)
              ├── Hedge (compra oposta quando share < $0.40)
              ├── Sizing (Kelly: $10/$5/$3 por convicção)
              ├── Risk Manager (5 losses → stop total)
              ├── Feeds (Binance WS + Polymarket WS)
              ├── Order Client (py-clob-client, EIP-712 signing)
              ├── Cycle Collector (Excel com 10 snapshots/ciclo)
              └── Storage (DuckDB + Excel)
```

## Setup

```bash
# 1. Clonar e configurar
git clone https://github.com/IgorGewehr/polymarket-bot.git
cd polymarket-bot
cp .env.example .env
# Editar .env com suas credenciais

# 2. Criar venv com Python 3.12
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Iniciar Redis
redis-server --daemonize yes

# 4. Rodar
python main.py --live        # Live trading
python main.py               # Dry run (DRY_RUN=true no .env)
python main.py --backtest    # Backtest com dados históricos
```

## Dashboard

Acesse **http://localhost:8888** para:
- Status em tempo real (mercado, posição, preços)
- P&L cumulativo com gráfico
- Tabela de trades recentes
- Histórico de ciclos
- Controles pause/resume

## Credenciais

```
POLYMARKET_PRIVATE_KEY=0x...     # Private key da carteira (Polygon)
POLYMARKET_PROXY_ADDRESS=0x...   # Proxy wallet
POLYMARKET_CHAIN_ID=137          # Polygon mainnet
```

## Estratégia

### Como o bot opera

1. **Detecta trend** do BTC via Binance em 3 timeframes (1m, 2m, 3m)
2. **Espera alinhamento**: trend BTC + mercado Polymarket concordam
3. **Entra** quando share está entre $0.50-$0.62 (retorno 61-100%)
4. **Monitora** posição a cada 2 segundos com 6 camadas de proteção
5. **Sai** por take profit, safety sell, stop loss ou delta guard

### Camadas de proteção (ordem de prioridade)

| # | Proteção | Trigger | Ação |
|---|---|---|---|
| 1 | Emergency Sell | Share < $0.20 | Vende imediatamente |
| 2 | Safety Sell | Share >= $0.85, < 200s | Vende (lucro quase certo) |
| 3 | Delta Guard | Delta < 10, < 60s, com lucro | Vende (50/50 não vale) |
| 4 | Stop Loss | Share caiu 35%+ do entry | Vende para limitar loss |
| 5 | Take Profit | Gain >= 40% E sell > hold_ev | Vende para travar lucro |
| 6 | EV Optimal | Gain >= 25% E sell > hold_ev×1.3 | Vende quando matematicamente vale |
| 7 | Lock Profit | Perdendo 10%+ E YES+NO < $0.95 | Compra lado oposto (lucro garantido) |
| 8 | Hedge | Share < $0.40 | Compra oposta para limitar loss |

### Sizing

| Condição | Bet Size | Shares @ $0.55 |
|---|---|---|
| Trend 2/3+ | $10 | 18.2 shares |
| Trend fraca | $5 | 9.1 shares |
| Após 2+ losses | $3 | 5.5 shares |

### Risk Management

| Parâmetro | Valor |
|---|---|
| Max loss diário | $15 |
| Stop total | 5 losses consecutivos |
| Cooldown | 15 min após 3 losses |
| Max trades/dia | 200 |
| Entry range | $0.50 - $0.62 |

## Performance (dados reais)

### Melhor sessão (5 trades)
```
#1 EXIT:ev_optimal   +$2.36 (28%)
#2 EXIT:ev_optimal   +$2.48 (29%)
#3 EXIT:ev_optimal   +$2.40 (28%)
#4 EXIT:delta_guard  +$2.03 (24%)
#5 EXIT:ev_optimal   +$3.34 (38%)

WR: 100% | PnL: +$12.61 | Avg: +$2.52/trade
```

### Dados agregados (120+ ciclos)
- 112+ ciclos coletados com snapshots
- Early exit funcional: take profit, stop loss, safety sell, delta guard
- Lock profit: salvou -$3 loss → +$0.17 win
- Win rate: 60-100% dependendo do mercado

## Estrutura de arquivos

```
polymarket-bot/
├── config/settings.py           # Thresholds e configurações
├── core/
│   ├── engine.py                # Loop principal + fases do ciclo
│   ├── analyzer.py              # 4 camadas de análise técnica
│   ├── sizing.py                # Kelly fracionário
│   ├── early_exit.py            # Take profit, stop loss, safety sell
│   ├── lock_profit.py           # Lock assimétrico YES+NO
│   ├── hedger.py                # Hedge EV-driven
│   └── risk_manager.py          # Limites e cooldowns
├── data/
│   ├── feeds.py                 # WebSocket Binance + Polymarket
│   ├── cycle_collector.py       # Snapshots para Excel
│   ├── price_buffer.py          # Buffer circular numpy
│   └── storage.py               # DuckDB
├── execution/
│   └── order_client.py          # py-clob-client (BUY/SELL/LOCK)
├── dashboard/
│   ├── api.py                   # FastAPI endpoints
│   └── index.html               # UI glassmorphism dark theme
├── monitoring/notifier.py       # Telegram alerts
├── main.py                      # Entry point
├── ESTRATEGIAS_FUTURAS.md       # Roadmap de melhorias
├── PLANO_MELHORIAS_V2.md        # Plano detalhado com dados
├── METRICAS.md                  # Métricas de sucesso
└── ANALISE_SESSOES.md           # Análise de sessões live
```

## Tecnologias

- Python 3.12 + asyncio + uvloop
- py-clob-client (Polymarket CLOB, EIP-712 signing)
- WebSocket (Binance + Polymarket real-time)
- NumPy (análise técnica)
- DuckDB (storage)
- openpyxl (Excel export)
- FastAPI + Chart.js (dashboard)
- Redis (cache)
