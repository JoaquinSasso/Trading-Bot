"""Estrategia S8: Scorer PID Multi-Horizonte de Doble Sistema (S8_PID_MULTIHORIZON_SPEC).

Implementación estricta de la especificación 'docs/S8_PID_MULTIHORIZON_SPEC.md':
1. Restituye los ocho horizontes:
   - 10 días (3900 min)
   - 5 días (1950 min)
   - 1 día (390 min)
   - 2 horas (120 min)
   - 1 hora (60 min)
   - 30 minutos (30 min)
   - 15 minutos (15 min)
   - 5 minutos (5 min)
2. Reglas de Derivada Multi-Horizonte:
   - Normalización de escala: [e_i(t) - e_i(t-h)] / sqrt(h)
   - Ponderación de horizontes:
     * Variante A (Referencia): w_h proportional to h
     * Variante B (Balancín / Logarítmica): w_h proportional to ln(h)
   - Filtro pasabajos (3 barras) para horizontes cortos (<= 30m)
   - Restricción estricta de sesión: Las diferencias intradiarias NO cruzan el cierre nocturno.
     Si un horizonte no está disponible, su peso se redistribuye proporcionalmente.
3. Sistema U (Persistencia de Tendencia):
   - e_i(t) = [ln P_i(t) - ln EMA_45d,i(t)] / sigma_45d,i(t)
   - P_i(t) = s * e_i(t) con s in {+1, -1}
   - I_i(t) = clip(EWMA_45d[e_i](t), -3.0, +3.0)
   - D_i(t) = sum_h w_h * [e_i(t) - e_i(t-h)] / sqrt(h)
   - u_i(t) = z_P + z_I + z_D (ganancias fijas 1/1/1)
4. Sistema D (Estrés y Deterioro):
   - 6 características: vol_expansion, semideviation_ratio, drawdown_depth,
     correlation_convergence, intraday_acceleration, gap_frequency.
   - d_i(t) = promedio de z-scores de las 6 características.
5. Capa de Arbitraje y Cadencia:
   - Elegibilidad: u_i > 0 AND d_i < 1.0 AND gate_absoluto
   - Salida forzada: d_i > 2.0 liquida de inmediato.
   - Registro de Scoreboard de competencia.
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

HORIZONS_MINUTES = {
    "10d": 3900,
    "5d": 1950,
    "1d": 390,
    "2h": 120,
    "1h": 60,
    "30m": 30,
    "15m": 15,
    "5m": 5,
}


@dataclass
class S8ScoreResult:
    symbol: str
    p_term: float
    i_term: float
    d_term: float
    u_score: float
    d_score: float = 0.0
    is_eligible: bool = False
    rank_u: int = 999
    weight_variant: str = "A"
    decision: str = "skip"
    deciding_system: str = "neither"
    d_veto_triggered: bool = False
    forced_exit_triggered: bool = False
    d_stress: float = 0.0

    def __post_init__(self) -> None:
        if self.d_stress == 0.0 and self.d_score != 0.0:
            self.d_stress = self.d_score
        elif self.d_score == 0.0 and self.d_stress != 0.0:
            self.d_score = self.d_stress


def _extract_current_session_df(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Extrae únicamente las barras correspondientes a la sesión de la última barra disponible."""
    if df is None or df.empty:
        return df

    try:
        if "_dt" in df.columns:
            dt_col = df["_dt"]
            if not pd.api.types.is_datetime64_any_dtype(dt_col):
                dt_col = pd.to_datetime(dt_col)
            last_date = dt_col.iloc[-1].date()
            return df[dt_col.dt.date == last_date].copy()
        elif "date" in df.columns:
            last_date = df["date"].iloc[-1]
            return df[df["date"] == last_date].copy()
        elif "datetime_et" in df.columns:
            dt_col = df["datetime_et"]
            if not pd.api.types.is_datetime64_any_dtype(dt_col):
                if isinstance(dt_col.iloc[-1], str):
                    last_date_str = str(dt_col.iloc[-1])[:10]
                    return df[dt_col.astype(str).str[:10] == last_date_str].copy()
                dt_col = pd.to_datetime(dt_col)
            last_date = dt_col.iloc[-1].date()
            return df[dt_col.dt.date == last_date].copy()
        elif "timestamp" in df.columns:
            ts_last = df["timestamp"].iloc[-1]
            if isinstance(ts_last, (int, float, np.integer, np.floating)):
                unit = "s" if ts_last < 1e11 else ("ms" if ts_last < 1e14 else "ns")
                dt_col = pd.to_datetime(df["timestamp"], unit=unit)
            else:
                dt_col = pd.to_datetime(df["timestamp"])
            last_date = dt_col.iloc[-1].date()
            return df[dt_col.dt.date == last_date].copy()
        elif isinstance(df.index, pd.DatetimeIndex):
            last_date = df.index[-1].date()
            return df[df.index.date == last_date].copy()
    except Exception:
        return df

    return df


def calculate_horizon_weights(
    available_horizons: list[str],
    rule: str = "A",  # "A": w ∝ h, "B": w ∝ ln(h)
) -> dict[str, float]:
    """Calcula y normaliza los pesos de horizonte con redistribución proporcional."""
    raw_weights = {}
    for h_name in available_horizons:
        mins = HORIZONS_MINUTES.get(h_name, 60)
        w = math.log(max(mins, 2.0)) if rule == "B" else float(mins)
        raw_weights[h_name] = w

    tot = sum(raw_weights.values())
    if tot <= 0:
        return {h: 1.0 / len(available_horizons) for h in available_horizons}
    return {h: w / tot for h, w in raw_weights.items()}


def compute_s8_pid_for_asset(
    daily_df: pd.DataFrame,
    intraday_df: pd.DataFrame | None = None,
    df_spy: pd.DataFrame | None = None,
    sign_p: float = 1.0,
    weight_rule: str = "A",  # "A": w ∝ h, "B": w ∝ ln(h)
    window_ref_days: int = 45,
) -> dict[str, Any]:
    """Calcula los términos S8 para un activo combinando referencia diaria y granularidad intradiaria."""
    closes_d = daily_df["close"].astype(float)
    opens_d = daily_df["open"].astype(float) if "open" in daily_df else closes_d
    n_days = len(closes_d)

    if n_days < window_ref_days + 15:
        return {
            "valid": False,
            "P": 0.0, "I": 0.0, "D": 0.0,
            "d_features": [0.0] * 6,
            "gate_ok": False,
            "price": float(closes_d.iloc[-1]),
        }

    # 1. Error normalizado sobre datos diarios
    log_rets_d = np.log(closes_d / closes_d.shift(1))
    sigma_45d = float(log_rets_d.iloc[-window_ref_days:].std())
    if sigma_45d <= 1e-6 or np.isnan(sigma_45d):
        sigma_45d = 0.01

    ema_45d = closes_d.ewm(span=window_ref_days, adjust=False).mean()
    log_p = np.log(closes_d)
    log_ema = np.log(ema_45d)
    e_daily = (log_p - log_ema) / sigma_45d

    # Último precio disponible (puede ser el último tick/barra intradiaria)
    current_price = float(closes_d.iloc[-1])
    if intraday_df is not None and not intraday_df.empty:
        current_price = float(intraday_df.iloc[-1]["close"])

    e_t = (math.log(current_price) - float(log_ema.iloc[-1])) / sigma_45d

    # Término P
    P_val = sign_p * e_t

    # Término I (EWMA con anti-windup clip a [-3, 3] sobre cierres diarios)
    ewma_i = float(e_daily.ewm(span=window_ref_days, adjust=False).mean().iloc[-1])
    I_val = float(np.clip(ewma_i, -3.0, 3.0))

    # Término D multi-horizonte (8 horizontes)
    # Identificar qué horizontes están disponibles en los datos
    available_horizons = ["10d", "5d", "1d"]
    diffs = {
        "10d": (e_t - float(e_daily.iloc[-11])) if len(e_daily) > 10 else 0.0,
        "5d": (e_t - float(e_daily.iloc[-6])) if len(e_daily) > 5 else 0.0,
        "1d": (e_t - float(e_daily.iloc[-2])) if len(e_daily) > 1 else 0.0,
    }

    # Detección precisa de resolución intradiaria (5m vs 1h)
    step_is_5m = False
    if intraday_df is not None and len(intraday_df) >= 2:
        if "timestamp" in intraday_df.columns:
            try:
                diff_sec = abs(float(intraday_df["timestamp"].iloc[-1]) - float(intraday_df["timestamp"].iloc[-2]))
                step_is_5m = (diff_sec <= 600)
            except (ValueError, TypeError):
                dt1 = pd.to_datetime(intraday_df["timestamp"].iloc[-1])
                dt2 = pd.to_datetime(intraday_df["timestamp"].iloc[-2])
                step_is_5m = abs((dt1 - dt2).total_seconds()) <= 600
        elif "_dt" in intraday_df.columns:
            dt_col = intraday_df["_dt"]
            if not pd.api.types.is_datetime64_any_dtype(dt_col):
                dt_col = pd.to_datetime(dt_col)
            diff_sec = abs((dt_col.iloc[-1] - dt_col.iloc[-2]).total_seconds())
            step_is_5m = (diff_sec <= 600)
        elif "datetime_et" in intraday_df.columns:
            dt_col = intraday_df["datetime_et"]
            if not pd.api.types.is_datetime64_any_dtype(dt_col):
                dt_col = pd.to_datetime(dt_col)
            diff_sec = abs((dt_col.iloc[-1] - dt_col.iloc[-2]).total_seconds())
            step_is_5m = (diff_sec <= 600)

    # B-01 & B-02: Aislar barras a la sesión actual para no cruzar el cierre nocturno
    session_df = _extract_current_session_df(intraday_df)

    # Incorporar horizontes intradiarios solo si la sesión actual tiene suficiente profundidad
    if session_df is not None and len(session_df) >= 2:
        c_session = session_df["close"].astype(float)
        # Filtro pasabajos de 3 barras para derivativas cortas (spec §6.5) restringido a la sesión actual
        c_filtered = c_session.rolling(window=3, min_periods=1).mean()
        cur_bar_close = float(c_filtered.iloc[-1])
        e_cur_intra = (math.log(cur_bar_close) - float(log_ema.iloc[-1])) / sigma_45d

        bars_2h = 24 if step_is_5m else 2
        bars_1h = 12 if step_is_5m else 1

        if len(session_df) > bars_2h:
            available_horizons.append("2h")
            p_prev = float(c_filtered.iloc[-1 - bars_2h])
            e_prev = (math.log(p_prev) - float(log_ema.iloc[-1])) / sigma_45d
            diffs["2h"] = e_cur_intra - e_prev

        if len(session_df) > bars_1h:
            available_horizons.append("1h")
            p_prev = float(c_filtered.iloc[-1 - bars_1h])
            e_prev = (math.log(p_prev) - float(log_ema.iloc[-1])) / sigma_45d
            diffs["1h"] = e_cur_intra - e_prev

        # Horizontes de 30m, 15m, 5m (evaluados en barras de 5m contra la sesión actual)
        if step_is_5m:
            for h_code, n_bars in [("30m", 6), ("15m", 3), ("5m", 1)]:
                if len(session_df) > n_bars:
                    available_horizons.append(h_code)
                    p_prev = float(c_filtered.iloc[-1 - n_bars])
                    e_prev = (math.log(p_prev) - float(log_ema.iloc[-1])) / sigma_45d
                    diffs[h_code] = e_cur_intra - e_prev

    weights = calculate_horizon_weights(available_horizons, rule=weight_rule)

    D_val = 0.0
    for h_name, w in weights.items():
        h_mins = HORIZONS_MINUTES.get(h_name, 60)
        # Normalización obligatoria por sqrt(h) (spec §4.3)
        scale = math.sqrt(h_mins)
        D_val += w * (diffs[h_name] / scale)

    # 2. Sistema D (6 Características de Estrés y Deterioro - spec §7.2)
    # 1. Vol expansion: sigma_5d / sigma_45d (B-05: sqrt(78) y 390 barras para 5m)
    bars_vol = 390 if step_is_5m else 33
    scale_vol = math.sqrt(78.0) if step_is_5m else math.sqrt(6.5)
    if intraday_df is not None and len(intraday_df) >= bars_vol:
        intra_rets = np.log(intraday_df["close"].astype(float) / intraday_df["close"].astype(float).shift(1)).dropna()
        sigma_5d_realized = float(intra_rets.iloc[-bars_vol:].std()) * scale_vol
        vol_exp = sigma_5d_realized / sigma_45d
    else:
        sigma_5d = float(log_rets_d.iloc[-5:].std()) if len(log_rets_d) >= 5 else sigma_45d
        vol_exp = sigma_5d / sigma_45d

    # 2. Asimetría de semidesvío: sigma_neg_20d / sigma_pos_20d
    r20 = log_rets_d.iloc[-20:] if len(log_rets_d) >= 20 else log_rets_d
    neg_r = r20[r20 < 0]
    pos_r = r20[r20 > 0]
    s_neg = float(neg_r.std()) if len(neg_r) >= 2 else sigma_45d
    s_pos = float(pos_r.std()) if len(pos_r) >= 2 else sigma_45d
    semidev = s_neg / (s_pos if s_pos > 1e-6 else sigma_45d)

    # 3. Profundidad desde máximo: (P_t - max_45d) / sigma_45d (invertido para medir estrés)
    max_45d = float(closes_d.iloc[-window_ref_days:].max())
    dd_depth = (current_price - max_45d) / (sigma_45d * current_price + 1e-6)

    # 4. Convergencia de correlación: corr(r_i, r_SPY)
    corr_spy = 0.5
    if df_spy is not None and len(df_spy) >= 25:
        spy_c = df_spy["close"].astype(float)
        spy_r20 = np.log(spy_c / spy_c.shift(1)).iloc[-20:]
        sub_r20 = r20.iloc[-len(spy_r20):]
        if len(sub_r20) == len(spy_r20) and len(spy_r20) >= 10:
            c_val = sub_r20.corr(spy_r20)
            if not np.isnan(c_val):
                corr_spy = float(c_val)

    # 5. Aceleración intradiaria: |Delta e_i(30m)| (B-04: eliminar segunda división redundante por sigma_45d)
    accel = 0.0
    if "30m" in diffs:
        accel = abs(diffs["30m"])
    elif "1h" in diffs:
        accel = abs(diffs["1h"])

    # 6. Frecuencia de gaps: % sesiones a 20d con |open_t - close_{t-1}| > 1.5 sigma
    sub_opens = opens_d.iloc[-20:] if len(opens_d) >= 20 else opens_d
    sub_prev_c = closes_d.shift(1).iloc[-len(sub_opens):]
    gaps = sum(1 for o, pc in zip(sub_opens, sub_prev_c, strict=False) if pc > 0 and abs(o - pc) / pc > 1.5 * sigma_45d)
    gap_freq = gaps / len(sub_opens) if len(sub_opens) > 0 else 0.0

    # Gate absoluto: close > EMA50 y ret_45d > 0
    ema_50 = float(closes_d.ewm(span=50, adjust=False).mean().iloc[-1])
    c_45_ago = float(closes_d.iloc[-window_ref_days]) if len(closes_d) > window_ref_days else float(closes_d.iloc[0])
    gate_ok = (current_price > ema_50) and ((current_price - c_45_ago) > 0)

    return {
        "valid": True,
        "P": P_val,
        "I": I_val,
        "D": D_val,
        "d_features": [vol_exp, semidev, -dd_depth, corr_spy, accel, gap_freq],
        "gate_ok": gate_ok,
        "price": current_price,
    }


def zscore_list(vals: list[float]) -> list[float]:
    """Calcula z-scores transversales estandarizados."""
    arr = np.array(vals, dtype=float)
    mu = np.mean(arr)
    sd = np.std(arr)
    if sd <= 1e-8 or np.isnan(sd):
        return [0.0 for _ in vals]
    return [(x - mu) / sd for x in vals]


def score_universe_s8(
    universe_daily: dict[str, pd.DataFrame],
    universe_intraday: dict[str, pd.DataFrame] | None = None,
    df_spy: pd.DataFrame | None = None,
    sign_p: float = 1.0,
    weight_rule: str = "A",  # "A": w ∝ h, "B": w ∝ ln(h)
) -> dict[str, S8ScoreResult]:
    """Calcula los scores S8 multi-horizonte del Sistema U y Sistema D."""
    raw_dict = {}
    valid_syms = []

    for sym, d_df in universe_daily.items():
        i_df = universe_intraday.get(sym) if universe_intraday else None
        res = compute_s8_pid_for_asset(
            daily_df=d_df,
            intraday_df=i_df,
            df_spy=df_spy,
            sign_p=sign_p,
            weight_rule=weight_rule,
        )
        raw_dict[sym] = res
        if res["valid"]:
            valid_syms.append(sym)

    if not valid_syms:
        return {}

    # 1. Sistema U: Z-scores transversales de P, I, D
    p_z = zscore_list([raw_dict[s]["P"] for s in valid_syms])
    i_z = zscore_list([raw_dict[s]["I"] for s in valid_syms])
    d_z = zscore_list([raw_dict[s]["D"] for s in valid_syms])

    u_scores = {s: p_z[i] + i_z[i] + d_z[i] for i, s in enumerate(valid_syms)}

    # 2. Sistema D: Z-scores transversales de las 6 características de estrés
    d_feature_matrix = []
    for feat_idx in range(6):
        col_vals = [raw_dict[s]["d_features"][feat_idx] for s in valid_syms]
        d_feature_matrix.append(zscore_list(col_vals))

    d_scores = {}
    for i, s in enumerate(valid_syms):
        d_scores[s] = sum(d_feature_matrix[feat_idx][i] for feat_idx in range(6)) / 6.0

    # 3. Arbitraje y Scoreboard U vs D (B-09 - spec §8 y §8.1)
    results = {}
    eligible_list = []

    for s in valid_syms:
        u_val = u_scores[s]
        d_val = d_scores[s]
        gate_ok = raw_dict[s]["gate_ok"]
        eligible = (u_val > 0.0) and (d_val < 1.0) and gate_ok

        forced_exit = bool(d_val > 2.0)
        d_veto = bool((u_val > 0.0 and gate_ok) and (d_val >= 1.0))

        if forced_exit:
            decision = "forced_exit"
            deciding_system = "D"
        elif eligible:
            decision = "enter"
            deciding_system = "both"
        else:
            decision = "skip"
            if d_veto:
                deciding_system = "D"
            elif d_val < 1.0:
                deciding_system = "U"
            else:
                deciding_system = "neither"

        results[s] = S8ScoreResult(
            symbol=s,
            p_term=raw_dict[s]["P"],
            i_term=raw_dict[s]["I"],
            d_term=raw_dict[s]["D"],
            u_score=u_val,
            d_score=d_val,
            is_eligible=eligible,
            weight_variant=weight_rule,
            decision=decision,
            deciding_system=deciding_system,
            d_veto_triggered=d_veto,
            forced_exit_triggered=forced_exit,
            d_stress=d_val,
        )
        if eligible:
            eligible_list.append((s, u_val))

    eligible_list.sort(key=lambda x: x[1], reverse=True)
    for r_idx, (s, _) in enumerate(eligible_list, 1):
        results[s].rank_u = r_idx

    return results


class S8PIDMultihorizonStrategy:
    """Implementación formal de la Estrategia S8 PID Multi-Horizonte como Strategy."""

    id: str = "s8_pid_multihorizon"
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
        daily_lookback_days=100,
        needs_intraday_bars=True,
        intraday_timeframe="1h",
        intraday_lookback_bars=250,
        requires_sip_delayed=True,
    )

    def __init__(
        self,
        universe: list[str] | None = None,
        weight_rule: str = "A",
        sign_p: float = 1.0,
        top_n: int = 4,
        stop_buffer_pct: float = 0.035,
        max_holding_days: int = 30,
    ) -> None:
        self.universe = universe
        self.weight_rule = weight_rule
        self.sign_p = sign_p
        self.top_n = top_n
        self.stop_buffer_pct = stop_buffer_pct
        self.max_holding_days = max_holding_days

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        """Evalúa las condiciones y genera señales para los activos elegibles con mayor u_score."""
        if ctx.regime not in self.allowed_regimes:
            return []

        df_spy = ctx.daily_bars.get("SPY")
        scores = score_universe_s8(
            universe_daily=ctx.daily_bars,
            universe_intraday=ctx.intraday_bars,
            df_spy=df_spy,
            sign_p=self.sign_p,
            weight_rule=self.weight_rule,
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

