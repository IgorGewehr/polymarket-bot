# Polymarket BTC Trading Bot

Bot automatizado para mercados de previsão de 5 minutos sobre Bitcoin no Polymarket.

## Arquitetura

```
main.py → Engine (loop 2s)
              ├── Analyzer (4 camadas + regime)
              ├── Sizing ($1-$3 dinâmico)
              ├── Hedger (EV-driven, max 2/dia)
              ├── Risk Manager (limits + cooldowns)
              ├── Feeds (Binance WS + Polymarket WS)
              ├── Order Client (CLOB API)
              ├── Storage (DuckDB)
              └── Notifier (Telegram)
```

## Setup rápido

```bash
# 1. Clonar e configurar
cp .env.example .env
# Editar .env com suas credenciais

# 2. Rodar com Docker
docker compose up -d

# 3. Ver logs
docker compose logs -f bot
```

## Setup local (sem Docker)

```bash
# Instalar dependências
pip install -r requirements.txt

# Iniciar Redis local
redis-server &

# Rodar em DRY_RUN (sem dinheiro real)
python main.py

# Rodar em modo live (com dinheiro real)
python main.py --live

# Rodar backtest com dados históricos
python main.py --backtest
```

## Credenciais do Polymarket

Para operar via API, você precisa:

1. Criar uma conta no Polymarket (https://polymarket.com)
2. Conectar sua carteira (Polygon)
3. Exportar a private key da carteira
4. Depositar USDC na sua conta

Preencha no `.env`:
- `POLYMARKET_PRIVATE_KEY`: private key da carteira (0x...)
- `POLYMARKET_PROXY_ADDRESS`: endereço do proxy wallet
- `POLYMARKET_CHAIN_ID`: 137 (Polygon mainnet)

## Alertas Telegram (opcional)

1. Crie um bot via @BotFather no Telegram
2. Pegue o token do bot
3. Envie uma mensagem para o bot e pegue seu chat_id
4. Preencha `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` no `.env`

## Estratégia

### 4 Camadas de Análise
1. **Trend 5min**: slope linear dos últimos 10 ticks
2. **Multi-timeframe**: alignment de 5/15/30 min do BTC spot
3. **Bollinger Bands**: z-score para detectar extremos
4. **Momentum**: aceleração (segunda derivada)

### Filtros Absolutos
- Delta >= 4 (sempre)
- Retorno >= 10%
- Confidence >= |3|
- Tempo restante >= 1:00

### Sizing Dinâmico
- $1: confiança baixa
- $2: setup sólido
- $3: tudo alinhado (confidence alto + Up + tempo cedo)

### Detecção de Regime
- **Trending**: opera normalmente
- **Lateral**: pula ciclos, espera squeeze breakout

### Hedge
- Max 2 por dia
- Só quando prob. de perder > 55%
- Só quando melhora o EV matematicamente
- Cooldown de 15min entre hedges

## Monitoramento

O bot loga tudo em formato estruturado. Cada trade registra:
- Scores das 4 camadas
- Delta, preço, retorno esperado
- Regime detectado
- Resultado e P&L

Use `python main.py --backtest` para validar mudanças nos parâmetros
contra os dados históricos antes de rodar em produção.

## Limites de Risco

| Parâmetro | Valor |
|---|---|
| Max loss diário | $15 |
| Max trades/dia | 200 |
| Cooldown após 3 losses | 15 min |
| Drawdown → sizing $1 | Queda de $8 do pico |
