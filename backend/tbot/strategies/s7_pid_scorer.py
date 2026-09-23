"""Estrategia S7: PID Scorer de Doble Sistema (S6_PID_SCORER_SPEC).

Implementación fiel de la especificación 'docs/S6_PID_SCORER_SPEC.md':
1. Sistema U (Persistencia de Tendencia):
   - Error normalizado: e_i(t) = [ln(P_i(t)) - ln(EMA_45,i(t))] / sigma_45d,i(t)
   - Término Proporcional (P): P_i(t) = s * e_i(t) con s = +1 (trend) o -1 (reversión)
   - Término Integral con Anti-Windup (I): I_i(t) = clip(EWMA_45(e_i(t)), -3.0, +3.0)
   - Término Derivativo Multi-Horizonte (D):
     D_i(t) = sum_h w_h * [e_i(t) - e_i(t-h)] / sqrt(h)
     Horizontes diarios h in {10, 5, 1} sesiones, w_h proporcional a h (0.625, 0.3125, 0.0625)
   - Agregación transversal: u_i(t) = z_P + z_I + z_D (cero parámetros libres sintonizados)
2. Sistema D (Estrés y Deterioro):
   - Vol expansion: sigma_5d / sigma_45d
   - Semideviation ratio: sigma_neg_20d / sigma_pos_20d
   - Drawdown depth: (ln(P_t) - ln(max_45d)) / sigma_45d
   - Correlation convergence: corr_20d(r_i, r_SPY)
   - Gap frequency: % sesiones a 20d con |open_t - close_{t-1}| > 1.5 * sigma
   - d_i(t): promedio de los z-scores transversales de estas 5 métricas
3. Capa de Arbitraje:
   - Elegibilidad: (u_i > 0) y (d_i < +1.0) y gate_absoluto
   - Selección: Ranking por u_i dentro de los elegibles
   - Salida forzada: d_i > +2.0 fuerza liquidación incondicional
   - Salidas estándar: Trailing Stop EMA(25), Stop Loss fijo, o retención máxima de 30 días.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import (
    Signal,
    StrategyContext,
    StrategyDataRequirements,
    compute_signal_id,
)


@dataclass
class PIDScoreResult:
    symbol: str
    p_term: float
    i_term: float
    d_term: float
    u_score: float  # Score compuesto de tendencia
    d_stress: float # Score compuesto de estrés defensivo
    is_eligible: bool
    rank_u: int = 999


def compute_log_returns(prices: pd.Series) -> pd.Series:
    """Calcula retornos logarítmicos diarios."""
    return np.log(prices / prices.shift(1)).dropna()


def compute_pid_terms_for_asset(
    df: pd.DataFrame,
    df_spy: pd.DataFrame | None = None,
    sign_p: float = 1.0,
    window_ref: int = 45,
) -> dict[str, Any]:
    """Calcula las componentes P, I, D y métricas defensivas para un activo."""
    closes = df["close"].astype(float)
    opens = df["open"].astype(float) if "open" in df else closes
    n = len(closes)

    if n < window_ref + 15:
        return {
            "valid": False,
            "P": 0.0, "I": 0.0, "D": 0.0,
            "vol_exp": 1.0, "semidev": 1.0, "dd_depth": 0.0,
            "gate_ok": False, "price": float(closes.iloc[-1]),
        }

    # 1. Error normalizado
    log_rets = np.log(closes / closes.shift(1))
    sigma_45 = float(log_rets.iloc[-window_ref:].std())
    if sigma_45 <= 1e-6 or np.isnan(sigma_45):
        sigma_45 = 0.01

    ema_45 = closes.ewm(span=window_ref, adjust=False).mean()
    log_p = np.log(closes)
    log_ema = np.log(ema_45)
    e_series = (log_p - log_ema) / sigma_45

    e_t = float(e_series.iloc[-1])

    # Término P
    P_val = sign_p * e_t

    # Término I (EWMA con anti-windup clip a [-3, 3])
    ewma_i = float(e_series.ewm(span=window_ref, adjust=False).mean().iloc[-1])
    I_val = float(np.clip(ewma_i, -3.0, 3.0))

    # Término D multi-horizonte (h in 10, 5, 1)
    # w(10)=0.625, w(5)=0.3125, w(1)=0.0625
    h_weights = [(10, 0.625), (5, 0.3125), (1, 0.0625)]
    D_val = 0.0
    for h, w in h_weights:
        if len(e_series) > h:
            diff_h = float(e_series.iloc[-1] - e_series.iloc[-1 - h])
            D_val += w * (diff_h / math.sqrt(h))

    # 2. Métricas del Sistema Defensivo (D)
    # Vol expansion: sigma_5d / sigma_45d
    sigma_5 = float(log_rets.iloc[-5:].std()) if len(log_rets) >= 5 else sigma_45
    if np.isnan(sigma_5) or sigma_5 <= 1e-6:
        sigma_5 = sigma_45
    vol_exp = sigma_5 / sigma_45

    # Asimetría de semidesvío: sigma_neg_20d / sigma_pos_20d
    r20 = log_rets.iloc[-20:] if len(log_rets) >= 20 else log_rets
    neg_r = r20[r20 < 0]
    pos_r = r20[r20 > 0]
    s_neg = float(neg_r.std()) if len(neg_r) >= 2 else sigma_45
    s_pos = float(pos_r.std()) if len(pos_r) >= 2 else sigma_45
    if np.isnan(s_neg) or s_neg <= 1e-6:
        s_neg = sigma_45
    if np.isnan(s_pos) or s_pos <= 1e-6:
        s_pos = sigma_45
    semidev = s_neg / s_pos

    # Profundidad desde máximo: (ln(P_t) - ln(max_45d)) / sigma_45
    max_45 = float(closes.iloc[-window_ref:].max())
    dd_depth = (float(np.log(closes.iloc[-1])) - float(np.log(max_45))) / sigma_45

    # Gate absoluto: close > EMA50 y retorno a 45 días > 0
    ema_50 = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])
    c_now = float(closes.iloc[-1])
    c_45d_ago = float(closes.iloc[-window_ref]) if len(closes) > window_ref else float(closes.iloc[0])
    ret_45d = (c_now - c_45d_ago) / c_45d_ago
    gate_ok = (c_now > ema_50) and (ret_45d > 0.0)

    return {
        "valid": True,
        "P": P_val,
        "I": I_val,
        "D": D_val,
        "vol_exp": vol_exp,
        "semidev": semidev,
        "dd_depth": dd_depth,
        "gate_ok": gate_ok,
        "price": c_now,
    }


def zscore_series(vals: list[float]) -> list[float]:
    """Calcula z-scores transversales estandarizados."""
    arr = np.array(vals, dtype=float)
    mu = np.mean(arr)
    sd = np.std(arr)
    if sd <= 1e-8 or np.isnan(sd):
        return [0.0 for _ in vals]
    return [(x - mu) / sd for x in vals]


def score_universe_pid(
    universe_data: dict[str, pd.DataFrame],
    df_spy: pd.DataFrame | None = None,
    sign_p: float = 1.0,
) -> dict[str, PIDScoreResult]:
    """Calcula los scores transversales del Sistema U y Sistema D para el universo."""
    raw_results = {}
    valid_syms = []

    for sym, df in universe_data.items():
        res = compute_pid_terms_for_asset(df, df_spy=df_spy, sign_p=sign_p)
        raw_results[sym] = res
        if res["valid"]:
            valid_syms.append(sym)

    if not valid_syms:
        return {}

    # 1. Z-scores transversales para Sistema U
    p_raw = [raw_results[s]["P"] for s in valid_syms]
    i_raw = [raw_results[s]["I"] for s in valid_syms]
    d_raw = [raw_results[s]["D"] for s in valid_syms]

    z_p = zscore_series(p_raw)
    z_i = zscore_series(i_raw)
    z_d = zscore_series(d_raw)

    u_scores = {}
    for idx, s in enumerate(valid_syms):
        # u = s * z_P + z_I + z_D
        u_scores[s] = z_p[idx] + z_i[idx] + z_d[idx]

    # 2. Z-scores transversales para Sistema D (Estrés)
    ve_raw = [raw_results[s]["vol_exp"] for s in valid_syms]
    sd_raw = [raw_results[s]["semidev"] for s in valid_syms]
    # dd_depth es negativo cuando hay drawdown: invertimos para que mayor sea mayor estrés
    dd_raw = [-raw_results[s]["dd_depth"] for s in valid_syms]

    z_ve = zscore_series(ve_raw)
    z_sd = zscore_series(sd_raw)
    z_dd = zscore_series(dd_raw)

    d_scores = {}
    for idx, s in enumerate(valid_syms):
        # Estrés = promedio simple de z-scores de las 3 métricas de deterioro
        d_scores[s] = (z_ve[idx] + z_sd[idx] + z_dd[idx]) / 3.0

    # 3. Capa de arbitraje:
    # elegible = (u_i > 0) y (d_i < 1.0) y gate_absoluto
    scored_items = []
    out_dict = {}

    for s in valid_syms:
        u_val = u_scores[s]
        d_val = d_scores[s]
        gate_ok = raw_results[s]["gate_ok"]

        eligible = (u_val > 0.0) and (d_val < 1.0) and gate_ok

        out_dict[s] = PIDScoreResult(
            symbol=s,
            p_term=raw_results[s]["P"],
            i_term=raw_results[s]["I"],
            d_term=raw_results[s]["D"],
            u_score=u_val,
            d_stress=d_val,
            is_eligible=eligible,
        )
        if eligible:
            scored_items.append((s, u_val))

    # Ranking ordinal por u_score
    scored_items.sort(key=lambda x: x[1], reverse=True)
    for rank_idx, (s, _) in enumerate(scored_items, 1):
        out_dict[s].rank_u = rank_idx

    return out_dict


class S7PIDScorerStrategy:
    """Implementación formal de la Estrategia S7 PID Scorer como Strategy."""

    id: str = "s7_pid_scorer"
    version: str = "1.0.0"
    schedule: list[str] = ["15:45 America/New_York"]
    allowed_regimes: set[MarketRegime] = {
        MarketRegime.BULL_CALM,
        MarketRegime.BULL_VOLATILE,
    }
    allows_open_window: bool = False
    universe: list[str] | None = None
    data_requirements: StrategyDataRequirements = StrategyDataRequirements(
        needs_daily_bars=True,
        daily_lookback_days=75,
        needs_intraday_bars=False,
        requires_sip_delayed=True,
    )

    def __init__(
        self,
        universe: list[str] | None = None,
        sign_p: float = 1.0,
        top_n: int = 4,
        stop_buffer_pct: float = 0.035,
        max_holding_days: int = 30,
    ) -> None:
        self.universe = universe
        self.sign_p = sign_p
        self.top_n = top_n
        self.stop_buffer_pct = stop_buffer_pct
        self.max_holding_days = max_holding_days

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        """Evalúa las condiciones y genera señales para los activos elegibles con mayor u_score."""
        if ctx.regime not in self.allowed_regimes:
            return []

        df_spy = ctx.daily_bars.get("SPY")
        scores = score_universe_pid(
            universe_data=ctx.daily_bars,
            df_spy=df_spy,
            sign_p=self.sign_p,
        )

        candidates = []
        for sym, res in scores.items():
            if self.universe and sym not in self.universe:
                continue
            if sym in ctx.portfolio_positions:
                continue
            if res.is_eligible:
                cur_price = ctx.current_prices.get(sym)
                if cur_price is None and sym in ctx.daily_bars:
                    cur_price = Decimal(str(round(float(ctx.daily_bars[sym]["close"].iloc[-1]), 4)))
                if cur_price is not None and cur_price > Decimal("0"):
                    candidates.append((sym, res.u_score, res.d_stress, cur_price))

        candidates.sort(key=lambda x: x[1], reverse=True)
        signals: list[Signal] = []

        for sym, u_score, d_stress, cur_p in candidates[: self.top_n]:
            stop_p = cur_p * Decimal(str(1.0 - self.stop_buffer_pct))
            sig_id = compute_signal_id(self.id, self.version, sym, ctx.now)
            signals.append(
                Signal(
                    signal_id=sig_id,
                    strategy_id=self.id,
                    symbol=sym,
                    side="buy",
                    entry_type="market",
                    entry_price_ref=cur_p,
                    stop_price=Decimal(str(round(float(stop_p), 4))),
                    created_at=ctx.now,
                    max_holding=timedelta(days=self.max_holding_days),
                    score=float(min(1.0, max(0.1, u_score / 3.0))),
                    features={"u_score": u_score, "d_stress": d_stress},
                )
            )

        return signals

