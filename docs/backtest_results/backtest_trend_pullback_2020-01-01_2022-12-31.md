# Reporte Oficial de Backtest — trend_pullback

### Resultados del Backtest: trend_pullback
**Periodo:** 2020-01-02 al 2022-12-30 | **Puerta de Decision:** [RECHAZADO]

| Metrica | Valor | Criterio Puerta Fase 2 |
|---|---|---|
| **Capital Inicial / Final** | $2,000.00 -> $1,785.79 | -- |
| **Retorno Neto Total** | -10.71% ($-214.21) | Expectativa Neta > 0 |
| **Operaciones Totales** | 171 (Win: 53, Loss: 118) | -- |
| **Win Rate (%)** | 30.99% | -- |
| **Profit Factor** | 0.79 | -- |
| **Expectativa por Trade** | -0.14 R ($-1.25) | Expectativa > 0 |
| **Max Drawdown** | 13.50% ($278.81) | <= 15.00% |
| **Duracion Max. Drawdown** | 585 dias | -- |
| **Sharpe Ratio Anualizado** | -0.67 | -- |
| **Deflated Sharpe (DSR)** | **0.0094** | **>= 0.9000** |
| **Comisiones y Slippage** | $1.95 fees / $21.70 slippage | Costos deducidos |

*Observaciones de la puerta:* Expectativa neta no positiva (P&L=$-214.21, ExpR=-0.14), DSR insuficiente: 0.0094 < 0.90


### Detalle de Primeras 10 Operaciones

| Trade ID | Símbolo | Entrada | Salida | Cantidad | P&L ($) | P&L (R) | Motivo Salida |
|---|---|---|---|---|---|---|---|
| T00001 | NVDA | $6.20 | $6.04 | 62 | $-10.36 | -1.02R | stop_loss |
| T00002 | SPY | $326.96 | $324.10 | 3 | $-8.61 | -1.03R | stop_loss |
| T00003 | SPY | $326.69 | $323.86 | 3 | $-8.50 | -1.03R | stop_loss |
| T00004 | NVDA | $6.14 | $5.98 | 62 | $-10.19 | -1.02R | stop_loss |
| T00005 | AAPL | $77.20 | $80.84 | 5 | $+18.17 | +2.03R | take_profit |
| T00006 | SPY | $324.18 | $332.20 | 3 | $+24.03 | +2.45R | take_profit |
| T00007 | NVDA | $6.01 | $6.34 | 59 | $+19.15 | +1.93R | take_profit |
| T00008 | AAPL | $79.94 | $78.13 | 5 | $-9.09 | -1.02R | stop_loss |
| T00009 | NVDA | $6.69 | $6.37 | 33 | $-10.74 | -1.06R | stop_loss |
| T00010 | NVDA | $6.76 | $6.06 | 28 | $-19.51 | -1.98R | stop_loss |

*Total de operaciones en el período:* 171
