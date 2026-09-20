"""Métricas cuantitativas y evaluación estadística de estrategias de trading.

Implementa:
- Retornos, P&L, Profit Factor, Win Rate, Expectativa en R y USD.
- Drawdown máximo y duración.
- Ratio de Sharpe anualizado y Ratio de Sortino.
- Deflated Sharpe Ratio (DSR) de Bailey y López de Prado (2014) para control de sobreajuste.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from tbot.backtest.simulated_broker import SimulatedTrade

EULER_MASCHERONI = 0.57721566490153286060


@dataclass
class BacktestMetrics:
    """Reporte estructurado de métricas cuantitativas de un backtest."""

    strategy_id: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_net_pnl: float
    total_return_pct: float
    cagr_pct: float

    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    avg_trade_pnl: float
    avg_win_usd: float
    avg_loss_usd: float
    win_loss_ratio: float
    expectancy_r: float
    expectancy_usd: float

    max_drawdown_usd: float
    max_drawdown_pct: float
    max_drawdown_duration_days: int

    sharpe_ratio: float
    sortino_ratio: float
    deflated_sharpe_ratio: float
    num_tested_trials: int

    total_fees_paid: float
    total_slippage_cost: float

    # Puerta de decisión (Gate)
    passed_gate: bool
    gate_failures: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convierte las métricas a diccionario serializable."""
        return asdict(self)

    def summary_markdown(self) -> str:
        """Genera una tabla resumen en formato Markdown."""
        gate_status = "[APROBADO]" if self.passed_gate else "[RECHAZADO]"
        reasons = (
            ", ".join(self.gate_failures)
            if self.gate_failures
            else "Ninguna (todos los criterios cumplidos)"
        )

        return f"""### Resultados del Backtest: {self.strategy_id}
**Periodo:** {self.start_date} al {self.end_date} | **Puerta de Decision:** {gate_status}

| Metrica | Valor | Criterio Puerta Fase 2 |
|---|---|---|
| **Capital Inicial / Final** | ${self.initial_capital:,.2f} -> ${self.final_capital:,.2f} | -- |
| **Retorno Neto Total** | {self.total_return_pct:+.2f}% (${self.total_net_pnl:+,.2f}) | Expectativa Neta > 0 |
| **Operaciones Totales** | {self.total_trades} (Win: {self.winning_trades}, Loss: {self.losing_trades}) | -- |
| **Win Rate (%)** | {self.win_rate_pct:.2f}% | -- |
| **Profit Factor** | {self.profit_factor:.2f} | -- |
| **Expectativa por Trade** | {self.expectancy_r:+.2f} R (${self.expectancy_usd:+,.2f}) | Expectativa > 0 |
| **Max Drawdown** | {self.max_drawdown_pct:.2f}% (${self.max_drawdown_usd:,.2f}) | <= 15.00% |
| **Duracion Max. Drawdown** | {self.max_drawdown_duration_days} dias | -- |
| **Sharpe Ratio Anualizado** | {self.sharpe_ratio:.2f} | -- |
| **Deflated Sharpe (DSR)** | **{self.deflated_sharpe_ratio:.4f}** | **>= 0.9000** |
| **Comisiones y Slippage** | ${self.total_fees_paid:,.2f} fees / ${self.total_slippage_cost:,.2f} slippage | Costos deducidos |

*Observaciones de la puerta:* {reasons}
"""


def calculate_deflated_sharpe_ratio(
    returns: pd.Series | np.ndarray,
    num_trials: int = 1,
    benchmark_sr: float = 0.0,
    annualization_factor: float = 252.0,
) -> float:
    """Calcula el Deflated Sharpe Ratio (DSR) de Bailey y López de Prado (2014).

    Ajusta el ratio de Sharpe estimado por sesgo de selección y no normalidad
    de los retornos considerando el número de combinaciones probadas (num_trials).

    Args:
        returns: Serie de retornos periódicos.
        num_trials: Número de configuraciones o variantes probadas (N >= 1).
        benchmark_sr: Sharpe ratio nulo o de referencia (default 0.0).
        annualization_factor: Factor para des-anualizar si fuera necesario (252 para diario).

    Returns:
        Valor de probabilidad en [0.0, 1.0]. Un valor >= 0.90 indica significancia estadística al 90%.
    """
    clean_rets = pd.Series(returns).dropna().values
    n = len(clean_rets)

    if n < 5 or np.std(clean_rets, ddof=1) <= 1e-12:
        return 0.0

    mean_ret = np.mean(clean_rets)
    std_ret = np.std(clean_rets, ddof=1)
    sr_periodic = mean_ret / std_ret

    # Momentos estadísticos: Asimetría (skewness) y Kurtosis
    skew = float(stats.skew(clean_rets, bias=False))
    # Fisher kurtosis en scipy es excess kurtosis (normal = 0), necesitamos kurtosis estándar (normal = 3)
    kurt = float(stats.kurtosis(clean_rets, fisher=False, bias=False))

    # Estimación del máximo esperado bajo la hipótesis nula de N ensayos
    if num_trials > 1:
        z1 = stats.norm.ppf(1.0 - 1.0 / num_trials)
        z2 = stats.norm.ppf(1.0 - 1.0 / (num_trials * math.e))
        sr_star_periodic = (1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2
        # Normalizado por la desviación estándar muestral del Sharpe
        sr_star_periodic /= math.sqrt(n - 1)
    else:
        sr_star_periodic = benchmark_sr / math.sqrt(annualization_factor)

    # Denominador de la varianza asintótica de Mertens (2002) / Lo (2002)
    var_sr = 1.0 - skew * sr_periodic + ((kurt - 1.0) / 4.0) * (sr_periodic**2)

    if var_sr <= 0:
        var_sr = 1.0

    denom = math.sqrt(var_sr / (n - 1))
    z_score = (sr_periodic - sr_star_periodic) / denom
    dsr = float(stats.norm.cdf(z_score))

    return max(0.0, min(1.0, dsr))


def compute_backtest_metrics(
    strategy_id: str,
    trades: list[SimulatedTrade],
    equity_curve: pd.Series,
    initial_capital: float = 2000.0,
    num_tested_trials: int = 1,
    max_dd_threshold_pct: float = 15.0,
    min_dsr_threshold: float = 0.90,
) -> BacktestMetrics:
    """Calcula todas las métricas cuantitativas y evalúa la Puerta de Decisión de Fase 2."""
    if equity_curve.empty:
        start_date = "N/A"
        end_date = "N/A"
        final_capital = initial_capital
    else:
        start_date = (
            str(equity_curve.index[0].date())
            if hasattr(equity_curve.index[0], "date")
            else str(equity_curve.index[0])
        )
        end_date = (
            str(equity_curve.index[-1].date())
            if hasattr(equity_curve.index[-1], "date")
            else str(equity_curve.index[-1])
        )
        final_capital = float(equity_curve.iloc[-1])

    total_net_pnl = final_capital - initial_capital
    total_return_pct = (total_net_pnl / initial_capital) * 100.0 if initial_capital > 0 else 0.0

    # Drawdown
    if not equity_curve.empty and len(equity_curve) > 1:
        peak = equity_curve.cummax()
        dd_series = equity_curve - peak
        dd_pct_series = (dd_series / peak) * 100.0
        max_dd_usd = abs(float(dd_series.min()))
        max_dd_pct = abs(float(dd_pct_series.min()))

        # Duración de drawdown en días
        is_underwater = dd_series < 0
        underwater_blocks = (~is_underwater).cumsum()[is_underwater]
        if not underwater_blocks.empty:
            max_dd_duration = int(underwater_blocks.value_counts().max())
        else:
            max_dd_duration = 0
    else:
        max_dd_usd = 0.0
        max_dd_pct = 0.0
        max_dd_duration = 0

    # Retornos diarios para Sharpe y DSR
    if len(equity_curve) > 1:
        daily_returns = equity_curve.pct_change().dropna()
        n_days = len(daily_returns)
        cagr_pct = (
            (((final_capital / initial_capital) ** (252.0 / max(1, n_days))) - 1.0) * 100.0
            if final_capital > 0
            else -100.0
        )

        std_dev = float(daily_returns.std(ddof=1))
        mean_ret = float(daily_returns.mean())
        if std_dev > 1e-12:
            sharpe_ratio = (mean_ret / std_dev) * math.sqrt(252.0)
            downside_std = float(daily_returns[daily_returns < 0].std(ddof=1))
            sortino_ratio = (
                (mean_ret / downside_std * math.sqrt(252.0)) if downside_std > 1e-12 else 0.0
            )
        else:
            sharpe_ratio = 0.0
            sortino_ratio = 0.0

        dsr = calculate_deflated_sharpe_ratio(daily_returns, num_trials=num_tested_trials)
    else:
        daily_returns = pd.Series(dtype=float)
        cagr_pct = 0.0
        sharpe_ratio = 0.0
        sortino_ratio = 0.0
        dsr = 0.0

    # Estadísticas de operaciones
    n_trades = len(trades)
    if n_trades > 0:
        winning = [t for t in trades if t.pnl > 0]
        losing = [t for t in trades if t.pnl <= 0]
        n_wins = len(winning)
        n_losses = len(losing)
        win_rate_pct = (n_wins / n_trades) * 100.0

        gross_profits = sum([float(t.pnl) for t in winning], 0.0)
        gross_losses = abs(sum([float(t.pnl) for t in losing], 0.0))
        profit_factor = (
            (gross_profits / gross_losses)
            if gross_losses > 0
            else (999.0 if gross_profits > 0 else 0.0)
        )

        avg_trade_pnl = float(sum([t.pnl for t in trades])) / n_trades
        avg_win = (gross_profits / n_wins) if n_wins > 0 else 0.0
        avg_loss = (gross_losses / n_losses) if n_losses > 0 else 0.0
        win_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

        expectancy_r = float(sum([t.pnl_r for t in trades])) / n_trades
        expectancy_usd = avg_trade_pnl

        total_fees = float(sum([t.fees for t in trades]))
        total_slippage = float(sum([t.slippage_cost for t in trades]))
    else:
        n_wins = 0
        n_losses = 0
        win_rate_pct = 0.0
        profit_factor = 0.0
        avg_trade_pnl = 0.0
        avg_win = 0.0
        avg_loss = 0.0
        win_loss_ratio = 0.0
        expectancy_r = 0.0
        expectancy_usd = 0.0
        total_fees = 0.0
        total_slippage = 0.0

    # Evaluación de la Puerta de Decisión (Gate) de Fase 2
    gate_failures: list[str] = []
    if n_trades == 0:
        gate_failures.append("Sin operaciones ejecutadas")
    if expectancy_r <= 0 or total_net_pnl <= 0:
        gate_failures.append(
            f"Expectativa neta no positiva (P&L=${total_net_pnl:.2f}, ExpR={expectancy_r:.2f})"
        )
    if dsr < min_dsr_threshold:
        gate_failures.append(f"DSR insuficiente: {dsr:.4f} < {min_dsr_threshold:.2f}")
    if max_dd_pct > max_dd_threshold_pct:
        gate_failures.append(
            f"Max Drawdown superado: {max_dd_pct:.2f}% > {max_dd_threshold_pct:.2f}%"
        )

    passed_gate = len(gate_failures) == 0

    return BacktestMetrics(
        strategy_id=strategy_id,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        final_capital=round(final_capital, 2),
        total_net_pnl=round(total_net_pnl, 2),
        total_return_pct=round(total_return_pct, 2),
        cagr_pct=round(cagr_pct, 2),
        total_trades=n_trades,
        winning_trades=n_wins,
        losing_trades=n_losses,
        win_rate_pct=round(win_rate_pct, 2),
        profit_factor=round(profit_factor, 2),
        avg_trade_pnl=round(avg_trade_pnl, 2),
        avg_win_usd=round(avg_win, 2),
        avg_loss_usd=round(avg_loss, 2),
        win_loss_ratio=round(win_loss_ratio, 2),
        expectancy_r=round(expectancy_r, 4),
        expectancy_usd=round(expectancy_usd, 2),
        max_drawdown_usd=round(max_dd_usd, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        max_drawdown_duration_days=max_dd_duration,
        sharpe_ratio=round(sharpe_ratio, 2),
        sortino_ratio=round(sortino_ratio, 2),
        deflated_sharpe_ratio=round(dsr, 4),
        num_tested_trials=num_tested_trials,
        total_fees_paid=round(total_fees, 2),
        total_slippage_cost=round(total_slippage, 2),
        passed_gate=passed_gate,
        gate_failures=gate_failures,
    )
