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
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm
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
    cagr_pct: float | None

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

    sharpe_ratio: float | None
    sortino_ratio: float | None
    deflated_sharpe_ratio: float
    num_tested_trials: int

    total_fees_paid: float
    total_slippage_cost: float

    # Puerta de decisión (Gate)
    passed_gate: bool
    gate_failures: list[str]

    # B-10 & B-08: Régimen de cuenta y error estándar de Sharpe
    account_type: str = "cash"
    sharpe_se: float | None = None

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
        regime_desc = (
            "Reg T Cash (T+1 Settlement)"
            if self.account_type.lower() == "cash"
            else "Margin Account"
        )
        cagr_str = f"{self.cagr_pct:.2f}%" if self.cagr_pct is not None else "N/A (T < 252d)"
        sharpe_str = f"{self.sharpe_ratio:.2f}" if self.sharpe_ratio is not None else "N/A (T < 252d)"
        se_str = f"{self.sharpe_se:.4f}" if self.sharpe_se is not None else "N/A"

        return f"""### Resultados del Backtest: {self.strategy_id}
**Periodo:** {self.start_date} al {self.end_date} | **Regimen:** {regime_desc} | **Puerta de Decision:** {gate_status}

| Metrica | Valor | Criterio Puerta Fase 2 |
|---|---|---|
| **Regimen de Cuenta** | {regime_desc} | Declarado formalmente |
| **Capital Inicial / Final** | ${self.initial_capital:,.2f} -> ${self.final_capital:,.2f} | -- |
| **Retorno Neto Total** | {self.total_return_pct:+.2f}% (${self.total_net_pnl:+,.2f}) | Expectativa Neta > 0 |
| **CAGR (%)** | {cagr_str} | Anualizado (solo T >= 252) |
| **Operaciones Totales** | {self.total_trades} (Win: {self.winning_trades}, Loss: {self.losing_trades}) | -- |
| **Win Rate (%)** | {self.win_rate_pct:.2f}% | -- |
| **Profit Factor** | {self.profit_factor:.2f} | -- |
| **Expectativa por Trade** | {self.expectancy_r:+.2f} R (${self.expectancy_usd:+,.2f}) | Expectativa > 0 |
| **Max Drawdown** | {self.max_drawdown_pct:.2f}% (${self.max_drawdown_usd:,.2f}) | <= 15.00% |
| **Duracion Max. Drawdown** | {self.max_drawdown_duration_days} dias | -- |
| **Sharpe Ratio Anualizado** | {sharpe_str} | Con tasa BIL descontada |
| **Error Estandar Sharpe (SE)** | {se_str} | SE(S) asymptotic Lo (2002) |
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


def load_risk_free_rate_bil(csv_path: Path | str | None = None) -> pd.Series:
    """Carga la serie histórica de tasa libre de riesgo diaria del ETF BIL.

    Returns:
        pd.Series indexada por datetime.date con los valores flotantes de daily_rf.
    """
    if csv_path is None:
        curr = Path(__file__).resolve()
        candidate_paths = [
            curr.parent.parent.parent.parent / "data" / "risk_free_rate_bil_2010_2026.csv",
            Path("data/risk_free_rate_bil_2010_2026.csv"),
        ]
        resolved = None
        for cp in candidate_paths:
            if cp.exists():
                resolved = cp
                break
        if resolved is None:
            return pd.Series(dtype=float)
        csv_path = resolved
    else:
        csv_path = Path(csv_path)
        if not csv_path.exists():
            return pd.Series(dtype=float)

    df = pd.read_csv(csv_path)
    df["_date"] = pd.to_datetime(df["date"]).dt.date
    rf_series = df.set_index("_date")["daily_rf"].astype(float)
    return rf_series


@dataclass
class AlphaRegressionResult:
    """Resultado estructurado de regresión OLS respecto al benchmark sobre retornos excedentes."""

    alpha_annualized: float
    alpha_se: float
    alpha_tstat: float
    alpha_pvalue: float
    beta: float
    beta_se: float
    r_squared: float
    n_observations: int
    alpha_daily: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_ols_alpha_beta(
    strategy_returns: pd.Series,
    benchmark_returns: pd.Series,
    risk_free_returns: pd.Series | float | None = None,
    annualization_factor: float = 252.0,
) -> AlphaRegressionResult:
    """Calcula Alpha de Jensen y Beta mediante regresión OLS statsmodels sobre retornos excedentes.

    Especificación:
        R_{p, t} - R_{f, t} = alpha + beta * (R_{m, t} - R_{f, t}) + epsilon_t
    """
    strat_s = strategy_returns.dropna().copy()
    bench_s = benchmark_returns.dropna().copy()

    strat_s.index = pd.to_datetime(strat_s.index).date
    bench_s.index = pd.to_datetime(bench_s.index).date

    common_idx = strat_s.index.intersection(bench_s.index)
    if len(common_idx) < 5:
        raise ValueError(f"Observaciones comunes insuficientes ({len(common_idx)}) para regresión OLS.")

    s_ret = strat_s.loc[common_idx].astype(float)
    b_ret = bench_s.loc[common_idx].astype(float)

    if isinstance(risk_free_returns, pd.Series):
        rf_s = risk_free_returns
        rf_s.index = pd.to_datetime(rf_s.index).date
        rf_vals = pd.Series([rf_s.get(d, 0.0) for d in common_idx], index=common_idx, dtype=float)
    elif isinstance(risk_free_returns, (int, float)):
        rf_vals = pd.Series(float(risk_free_returns), index=common_idx, dtype=float)
    else:
        loaded_rf = load_risk_free_rate_bil()
        if not loaded_rf.empty:
            rf_vals = pd.Series([loaded_rf.get(d, 0.0) for d in common_idx], index=common_idx, dtype=float)
        else:
            rf_vals = pd.Series(0.0, index=common_idx, dtype=float)

    y = s_ret - rf_vals
    x = b_ret - rf_vals

    if float(np.var(x.values)) < 1e-12:
        return AlphaRegressionResult(
            alpha_annualized=0.0,
            alpha_se=0.0,
            alpha_tstat=0.0,
            alpha_pvalue=1.0,
            beta=1.0,
            beta_se=0.0,
            r_squared=0.0,
            n_observations=len(common_idx),
            alpha_daily=0.0,
        )

    X = sm.add_constant(x.values)
    model = sm.OLS(y.values, X)
    res = model.fit()

    alpha_daily = float(res.params[0])
    beta = float(res.params[1])
    alpha_se_daily = float(res.bse[0])
    beta_se = float(res.bse[1])
    alpha_tstat = float(res.tvalues[0])
    alpha_pvalue = float(res.pvalues[0])
    r_squared = float(res.rsquared)

    alpha_annualized = alpha_daily * annualization_factor
    alpha_se_annualized = alpha_se_daily * annualization_factor

    return AlphaRegressionResult(
        alpha_annualized=round(alpha_annualized, 6),
        alpha_se=round(alpha_se_annualized, 6),
        alpha_tstat=round(alpha_tstat, 4),
        alpha_pvalue=round(alpha_pvalue, 6),
        beta=round(beta, 4),
        beta_se=round(beta_se, 4),
        r_squared=round(r_squared, 4),
        n_observations=len(common_idx),
        alpha_daily=round(alpha_daily, 8),
    )


def calculate_shrunk_covariance(
    returns: np.ndarray,
    shrinkage_lambda: float = 0.3,
) -> np.ndarray:
    """Calcula matriz de covarianza con contracción lineal (shrinkage):
    Sigma_shrunk = (1 - lambda) * Sigma_sample + lambda * diag(Sigma_sample).
    """
    if returns.ndim == 1:
        returns = returns.reshape(-1, 1)
    t, m = returns.shape
    if m == 1:
        var = float(np.var(returns, ddof=1)) if t > 1 else float(np.var(returns))
        return np.array([[var]], dtype=float)

    sample_cov = np.cov(returns, rowvar=False, ddof=1)
    diag_target = np.diag(np.diag(sample_cov))
    shrunk_cov = (1.0 - shrinkage_lambda) * sample_cov + shrinkage_lambda * diag_target
    return shrunk_cov


def compute_portfolio_volatility_and_scale(
    weights: dict[str, float] | np.ndarray,
    returns_matrix: pd.DataFrame | np.ndarray,
    target_vol: float = 0.12,
    shrinkage_lambda: float = 0.3,
    annualization_factor: float = 252.0,
) -> tuple[float, float, np.ndarray]:
    """Calcula volatilidad anualizada de cartera con covarianza encogida y factor de escala k.

    Returns:
        tuple (sigma_port, k_scale, shrunk_cov_matrix)
    """
    if isinstance(returns_matrix, pd.DataFrame):
        if isinstance(weights, dict):
            symbols = [s for s in returns_matrix.columns if s in weights]
            w = np.array([weights[s] for s in symbols], dtype=float)
            r = returns_matrix[symbols].dropna().values
        else:
            w = np.asarray(weights, dtype=float)
            r = returns_matrix.dropna().values
    else:
        w = np.asarray(weights, dtype=float)
        r = np.asarray(returns_matrix, dtype=float)

    if len(w) == 0 or len(r) < 2:
        return 0.0, 1.0, np.zeros((len(w), len(w)))

    shrunk_cov = calculate_shrunk_covariance(r, shrinkage_lambda=shrinkage_lambda)
    ann_cov = shrunk_cov * annualization_factor

    port_var = float(w.T @ ann_cov @ w)
    sigma_port = math.sqrt(max(0.0, port_var))
    k_scale = min(1.0, target_vol / sigma_port) if sigma_port > 1e-8 else 1.0
    return sigma_port, k_scale, shrunk_cov


def compute_backtest_metrics(
    strategy_id: str,
    trades: list[SimulatedTrade],
    equity_curve: pd.Series,
    initial_capital: float = 2000.0,
    num_tested_trials: int = 1,
    max_dd_threshold_pct: float = 15.0,
    min_dsr_threshold: float = 0.90,
    account_type: str = "cash",
    rf_series: pd.Series | None = None,
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

        # B-06: Obtener e integrar tasa libre de riesgo BIL
        if rf_series is None:
            rf_series = load_risk_free_rate_bil()

        if rf_series is not None and not rf_series.empty:
            dates_list = [
                d.date() if hasattr(d, "date") else (pd.to_datetime(d).date() if not isinstance(d, date) else d)
                for d in daily_returns.index
            ]
            rf_aligned = pd.Series([rf_series.get(d, 0.0) for d in dates_list], index=daily_returns.index, dtype=float)
            excess_returns = daily_returns - rf_aligned
        else:
            excess_returns = daily_returns.copy()

        std_excess = float(excess_returns.std(ddof=1)) if len(excess_returns) > 1 else 0.0
        mean_excess = float(excess_returns.mean()) if len(excess_returns) > 0 else 0.0

        if std_excess > 1e-12 and n_days > 1:
            periodic_sharpe = mean_excess / std_excess
            sharpe_se = math.sqrt((1.0 + 0.5 * (periodic_sharpe ** 2)) / n_days)
        else:
            periodic_sharpe = 0.0
            sharpe_se = 0.0

        # B-08: Anualización estrictamente prohibida para T < 252 sesiones
        if n_days >= 252:
            cagr_pct = (
                (((final_capital / initial_capital) ** (252.0 / max(1, n_days))) - 1.0) * 100.0
                if final_capital > 0
                else -100.0
            )
            sharpe_ratio = periodic_sharpe * math.sqrt(252.0)
            downside_excess = excess_returns[excess_returns < 0]
            sortino_ratio = (
                (mean_excess / float(downside_excess.std(ddof=1)) * math.sqrt(252.0))
                if len(downside_excess) > 1 and float(downside_excess.std(ddof=1)) > 1e-12
                else 0.0
            )
        else:
            cagr_pct = None
            sharpe_ratio = None
            sortino_ratio = None

        dsr = calculate_deflated_sharpe_ratio(excess_returns, num_trials=num_tested_trials)
    else:
        daily_returns = pd.Series(dtype=float)
        excess_returns = pd.Series(dtype=float)
        n_days = 0
        cagr_pct = None
        sharpe_ratio = None
        sortino_ratio = None
        sharpe_se = None
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
        cagr_pct=round(cagr_pct, 2) if cagr_pct is not None else None,
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
        sharpe_ratio=round(sharpe_ratio, 2) if sharpe_ratio is not None else None,
        sortino_ratio=round(sortino_ratio, 2) if sortino_ratio is not None else None,
        deflated_sharpe_ratio=round(dsr, 4),
        num_tested_trials=num_tested_trials,
        total_fees_paid=round(total_fees, 2),
        total_slippage_cost=round(total_slippage, 2),
        passed_gate=passed_gate,
        gate_failures=gate_failures,
        account_type=account_type,
        sharpe_se=round(sharpe_se, 6) if sharpe_se is not None else None,
    )
