# Reporte Oficial de Backtest — intraday_momentum

### Resultados del Backtest: intraday_momentum
**Periodo:** 2020-01-01 al 2022-12-30 | **Puerta de Decision:** [RECHAZADO]

| Metrica | Valor | Criterio Puerta Fase 2 |
|---|---|---|
| **Capital Inicial / Final** | $2,000.00 -> $1,651.05 | -- |
| **Retorno Neto Total** | -17.45% ($-348.95) | Expectativa Neta > 0 |
| **Operaciones Totales** | 592 (Win: 248, Loss: 344) | -- |
| **Win Rate (%)** | 41.89% | -- |
| **Profit Factor** | 0.50 | -- |
| **Expectativa por Trade** | -0.16 R ($-0.59) | Expectativa > 0 |
| **Max Drawdown** | 17.67% ($353.95) | <= 15.00% |
| **Duracion Max. Drawdown** | 774 dias | -- |
| **Sharpe Ratio Anualizado** | -3.77 | -- |
| **Deflated Sharpe (DSR)** | **0.0000** | **>= 0.9000** |
| **Comisiones y Slippage** | $23.28 fees / $166.21 slippage | Costos deducidos |

*Observaciones de la puerta:* Expectativa neta no positiva (P&L=$-348.95, ExpR=-0.16), DSR insuficiente: 0.0000 < 0.90, Max Drawdown superado: 17.67% > 15.00%


### Detalle de Primeras 10 Operaciones

| Trade ID | Símbolo | Entrada | Salida | Cantidad | P&L ($) | P&L (R) | Motivo Salida |
|---|---|---|---|---|---|---|---|
| T00001 | SPY | $635.98 | $636.22 | 3 | $+0.68 | +0.14R | exit_at_close |
| T00002 | SPY | $624.31 | $624.30 | 3 | $-0.11 | -0.02R | exit_at_close |
| T00003 | QQQ | $613.18 | $612.64 | 3 | $-1.69 | -0.34R | exit_at_close |
| T00004 | SPY | $620.72 | $620.14 | 3 | $-1.82 | -0.35R | exit_at_close |
| T00005 | QQQ | $619.07 | $618.83 | 3 | $-0.79 | -0.14R | exit_at_close |
| T00006 | SPY | $616.22 | $616.41 | 3 | $+0.50 | +0.09R | exit_at_close |
| T00007 | SPY | $625.58 | $627.54 | 3 | $+5.86 | +1.05R | exit_at_close |
| T00008 | SPY | $632.13 | $631.39 | 3 | $-2.25 | -0.48R | exit_at_close |
| T00009 | SPY | $631.89 | $632.00 | 3 | $+0.30 | +0.06R | exit_at_close |
| T00010 | SPY | $639.82 | $638.15 | 3 | $-5.05 | -1.09R | stop_loss |

*Total de operaciones en el período:* 592
