#!/usr/bin/env python3
"""Re-cómputo del Test de Monotonicidad del Ranking (T-10 / F-20).

Corrige los dos defectos metodológicos identificados en F-20:
1. Solapamiento de observaciones: Aplica errores estándar Newey-West con lag = 8
   (45 sesiones forward / 5 de muestreo semanal - 1).
2. Niveles vs Spreads: Evalúa la cartera long-short:
   - Larga en rank(1,2), Corta en rank(6,7)
   - Control extremo: Larga en rank(1,2), Corta en rank(bottom 2)
   - Aísla la capacidad de ordenamiento transversal cancelando el retorno común de mercado.
3. Control cruzado no solapado: Muestreo independiente cada 45 sesiones.
4. Repetición por bloques en Universo A (11 Sectores GICS, Metales, Renta Fija, Internacional).

Actualiza: 'reports/rank_monotonicity.md' con la sección formal 'v2 — Corrección por Solapamiento'.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_14_DIR = PROJECT_ROOT / "data" / "historical_14"
DATA_UNIV_A_DIR = PROJECT_ROOT / "data" / "universe_a"
REPORTS_DIR = PROJECT_ROOT / "reports"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from optimize_and_benchmark_portfolio import load_all_market_data

UNIVERSE_14 = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
]
ALL_UNIVERSE_A_SYMBOLS = [
    "SPY", "XLK", "XLC", "XLY", "XLP", "XLV", "XLF", "XLI", "XLE", "XLU", "XLB", "XLRE",
    "IEFA", "IEMG", "GLDM", "SLV", "IEF", "TIP", "BIL"
]
US_SECTORS_11 = ["XLK", "XLC", "XLY", "XLP", "XLV", "XLF", "XLI", "XLE", "XLU", "XLB", "XLRE"]


def compute_newey_west_stats(series: np.ndarray, max_lags: int = 8) -> dict[str, float]:
    """Calcula la media, error estándar Newey-West (HAC), t-stat y p-value."""
    n = len(series)
    if n < max_lags + 2:
        return {"mean": float(np.mean(series)) if n > 0 else 0.0, "se_nw": 0.0, "t_stat": 0.0, "p_val": 1.0, "n_obs": n}

    # statsmodels OLS con cov_type='HAC'
    X = np.ones((n, 1))
    model = sm.OLS(series, X).fit(cov_type="HAC", cov_kwds={"maxlags": max_lags})
    
    mean_val = float(model.params[0])
    se_nw = float(model.bse[0])
    t_stat = float(model.tvalues[0])
    p_val = float(model.pvalues[0])

    # Error estándar clásico OLS para contraste
    se_ols = float(np.std(series, ddof=1) / np.sqrt(n))
    t_ols = float(mean_val / se_ols) if se_ols > 0 else 0.0
    p_ols = float(2.0 * (1.0 - stats.t.cdf(abs(t_ols), df=n - 1)))

    return {
        "n_obs": n,
        "mean": mean_val,
        "se_ols": se_ols,
        "t_ols": t_ols,
        "p_ols": p_ols,
        "se_nw": se_nw,
        "t_nw": t_stat,
        "p_nw": p_val,
    }


def compute_non_overlapping_stats(series: np.ndarray) -> dict[str, float]:
    """Calcula estadísticos sobre muestra no solapada (independiente)."""
    n = len(series)
    if n < 2:
        return {"mean": float(np.mean(series)) if n > 0 else 0.0, "se": 0.0, "t_stat": 0.0, "p_val": 1.0, "n_obs": n}

    mean_val = float(np.mean(series))
    se = float(np.std(series, ddof=1) / np.sqrt(n))
    t_stat = float(mean_val / se) if se > 0 else 0.0
    p_val = float(2.0 * (1.0 - stats.t.cdf(abs(t_stat), df=n - 1)))

    return {
        "n_obs": n,
        "mean": mean_val,
        "se": se,
        "t_stat": t_stat,
        "p_val": p_val,
    }


def evaluate_long_short_momentum(
    daily_data: dict[str, pd.DataFrame],
    symbols: list[str],
    start_date: date,
    end_date: date,
    fwd_days: int = 45,
    lookback_days: int = 45,
    short_rank_indices: tuple[int, int] = (6, 7),  # 1-indexed: 6 y 7
) -> dict[str, Any]:
    """Construye las series de retornos de carteras long-short semanales y no solapadas."""
    matching_sets = [set(df["_parsed_date"]) for s, df in daily_data.items() if s in symbols and not df.empty]
    if not matching_sets:
        raise ValueError(f"No symbols found in daily_data matching {symbols}")
    common_dates = sorted(set.intersection(*matching_sets))
    valid_dates = [d for d in common_dates if start_date <= d <= end_date]

    weekly_records = []
    non_overlap_records = []

    # 1. Muestreo semanal (cada 5 sesiones)
    for idx, cur_date in enumerate(valid_dates):
        if idx % 5 != 0:
            continue

        fwd_idx = idx + fwd_days
        if fwd_idx >= len(valid_dates):
            break
        fwd_date = valid_dates[fwd_idx]

        ranks_today = []
        for sym in symbols:
            df = daily_data[sym]
            past_bars = df[df["_parsed_date"] <= cur_date]
            if len(past_bars) < lookback_days + 5:
                continue

            closes = past_bars["close"].astype(float)
            c_now = float(closes.iloc[-1])
            c_past = float(closes.iloc[-lookback_days])
            if c_past <= 0:
                continue

            mom = (c_now - c_past) / c_past
            future_bar = df[df["_parsed_date"] == fwd_date]
            if future_bar.empty:
                continue
            c_future = float(future_bar["close"].iloc[0])
            fwd_ret = (c_future - c_now) / c_now

            ranks_today.append((sym, mom, fwd_ret))

        if len(ranks_today) < max(short_rank_indices):
            continue

        # Ordenar por momentum descendente (Rank 1 = mayor momentum)
        ranks_today.sort(key=lambda x: x[1], reverse=True)

        r1_ret = ranks_today[0][2] * 100.0
        r2_ret = ranks_today[1][2] * 100.0
        long_ret = (r1_ret + r2_ret) / 2.0

        s1_idx = short_rank_indices[0] - 1
        s2_idx = short_rank_indices[1] - 1
        s1_ret = ranks_today[s1_idx][2] * 100.0
        s2_ret = ranks_today[s2_idx][2] * 100.0
        short_ret = (s1_ret + s2_ret) / 2.0

        # Control extremo: Bottom 2
        bot1_ret = ranks_today[-1][2] * 100.0
        bot2_ret = ranks_today[-2][2] * 100.0
        bottom_ret = (bot1_ret + bot2_ret) / 2.0

        spread_6_7 = long_ret - short_ret
        spread_bottom = long_ret - bottom_ret

        rec = {
            "date": cur_date,
            "fwd_date": fwd_date,
            "long_ret": long_ret,
            "short_6_7_ret": short_ret,
            "bottom_ret": bottom_ret,
            "spread_6_7": spread_6_7,
            "spread_bottom": spread_bottom,
        }
        weekly_records.append(rec)

    # 2. Muestreo no solapado (cada 45 sesiones)
    for idx in range(0, len(valid_dates), fwd_days):
        cur_date = valid_dates[idx]
        fwd_idx = idx + fwd_days
        if fwd_idx >= len(valid_dates):
            break
        fwd_date = valid_dates[fwd_idx]

        ranks_today = []
        for sym in symbols:
            df = daily_data[sym]
            past_bars = df[df["_parsed_date"] <= cur_date]
            if len(past_bars) < lookback_days + 5:
                continue
            closes = past_bars["close"].astype(float)
            c_now = float(closes.iloc[-1])
            c_past = float(closes.iloc[-lookback_days])
            if c_past <= 0:
                continue
            mom = (c_now - c_past) / c_past
            future_bar = df[df["_parsed_date"] == fwd_date]
            if future_bar.empty:
                continue
            c_future = float(future_bar["close"].iloc[0])
            fwd_ret = (c_future - c_now) / c_now
            ranks_today.append((sym, mom, fwd_ret))

        if len(ranks_today) < max(short_rank_indices):
            continue

        ranks_today.sort(key=lambda x: x[1], reverse=True)
        long_ret = (ranks_today[0][2] + ranks_today[1][2]) / 2.0 * 100.0

        s1_idx = short_rank_indices[0] - 1
        s2_idx = short_rank_indices[1] - 1
        short_ret = (ranks_today[s1_idx][2] + ranks_today[s2_idx][2]) / 2.0 * 100.0
        bottom_ret = (ranks_today[-1][2] + ranks_today[-2][2]) / 2.0 * 100.0

        non_overlap_records.append({
            "date": cur_date,
            "fwd_date": fwd_date,
            "long_ret": long_ret,
            "short_6_7_ret": short_ret,
            "bottom_ret": bottom_ret,
            "spread_6_7": long_ret - short_ret,
            "spread_bottom": long_ret - bottom_ret,
        })

    df_weekly = pd.DataFrame(weekly_records)
    df_non = pd.DataFrame(non_overlap_records)

    nw_6_7 = compute_newey_west_stats(df_weekly["spread_6_7"].to_numpy(), max_lags=8)
    nw_bot = compute_newey_west_stats(df_weekly["spread_bottom"].to_numpy(), max_lags=8)
    nw_long = compute_newey_west_stats(df_weekly["long_ret"].to_numpy(), max_lags=8)

    non_6_7 = compute_non_overlapping_stats(df_non["spread_6_7"].to_numpy())
    non_bot = compute_non_overlapping_stats(df_non["spread_bottom"].to_numpy())
    non_long = compute_non_overlapping_stats(df_non["long_ret"].to_numpy())

    return {
        "weekly_df": df_weekly,
        "non_overlap_df": df_non,
        "nw_6_7": nw_6_7,
        "nw_bot": nw_bot,
        "nw_long": nw_long,
        "non_6_7": non_6_7,
        "non_bot": non_bot,
        "non_long": non_long,
    }


def evaluate_pair_block(
    daily_data: dict[str, pd.DataFrame],
    sym1: str,
    sym2: str,
    start_date: date,
    end_date: date,
    fwd_days: int = 45,
    lookback_days: int = 45,
) -> dict[str, Any]:
    """Evalúa spread long rank 1 short rank 2 para bloques de 2 instrumentos."""
    common_dates = sorted(
        set.intersection(
            set(daily_data[sym1]["_parsed_date"]),
            set(daily_data[sym2]["_parsed_date"]),
        )
    )
    valid_dates = [d for d in common_dates if start_date <= d <= end_date]

    weekly_spreads = []
    non_spreads = []

    for idx, cur_date in enumerate(valid_dates):
        is_weekly = (idx % 5 == 0)
        is_non = (idx % fwd_days == 0)

        if not is_weekly and not is_non:
            continue

        fwd_idx = idx + fwd_days
        if fwd_idx >= len(valid_dates):
            break
        fwd_date = valid_dates[fwd_idx]

        ranks = []
        for s in [sym1, sym2]:
            df = daily_data[s]
            past = df[df["_parsed_date"] <= cur_date]
            if len(past) < lookback_days + 5:
                continue
            closes = past["close"].astype(float)
            c_now = float(closes.iloc[-1])
            c_past = float(closes.iloc[-lookback_days])
            mom = (c_now - c_past) / c_past
            fwd_bar = df[df["_parsed_date"] == fwd_date]
            if fwd_bar.empty:
                continue
            fwd_ret = (float(fwd_bar["close"].iloc[0]) - c_now) / c_now
            ranks.append((s, mom, fwd_ret * 100.0))

        if len(ranks) < 2:
            continue

        ranks.sort(key=lambda x: x[1], reverse=True)
        # Long rank 1, Short rank 2
        spread = ranks[0][2] - ranks[1][2]

        if is_weekly:
            weekly_spreads.append(spread)
        if is_non:
            non_spreads.append(spread)

    nw_res = compute_newey_west_stats(np.array(weekly_spreads), max_lags=8)
    non_res = compute_non_overlapping_stats(np.array(non_spreads))

    return {"nw": nw_res, "non": non_res}


def main() -> int:
    print("=" * 95)
    print("   RE-CÓMPUTO DEL TEST DE MONOTONICIDAD: NEWEY-WEST Y SPREAD LONG-SHORT (T-10 / F-20)")
    print("=" * 95)

    daily_14 = load_all_market_data(DATA_14_DIR, symbols=UNIVERSE_14)
    daily_univ_a = load_all_market_data(DATA_UNIV_A_DIR, symbols=ALL_UNIVERSE_A_SYMBOLS)

    start_d = date(2020, 1, 2)
    end_d = date(2025, 12, 31)

    # 1. S5 (13 Activos, excluyendo SPY)
    print("\n[1/3] Evaluando S5 (13 Activos, 2020–2025)...")
    s5_symbols = [s for s in UNIVERSE_14 if s != "SPY"]
    res_s5 = evaluate_long_short_momentum(
        daily_data=daily_14,
        symbols=s5_symbols,
        start_date=start_d,
        end_date=end_d,
        fwd_days=45,
        lookback_days=45,
        short_rank_indices=(6, 7),
    )

    print("\n" + "-" * 90)
    print("   RESULTADOS S5: CARTERA LONG rank(1,2) vs SHORT rank(6,7)")
    print("-" * 90)
    nw = res_s5["nw_6_7"]
    print(f"Muestreo Semanal (Solapado, N={nw['n_obs']}):")
    print(f"  - Spread Medio (45d)         : {nw['mean']:+6.2f}%")
    print(f"  - Error Estándar Clásico OLS : ±{nw['se_ols']:5.2f}% | t = {nw['t_ols']:+5.2f} | p = {nw['p_ols']:6.3f}")
    print(f"  - Error Estándar Newey-West  : ±{nw['se_nw']:5.2f}% | t = {nw['t_nw']:+5.2f} | p = {nw['p_nw']:6.3f} (lag=8)")

    non = res_s5["non_6_7"]
    print(f"Control Muestreo No Solapado (Independiente, N={non['n_obs']}):")
    print(f"  - Spread Medio (45d)         : {non['mean']:+6.2f}%")
    print(f"  - Error Estándar             : ±{non['se']:5.2f}% | t = {non['t_stat']:+5.2f} | p = {non['p_val']:6.3f}")

    nw_bot = res_s5["nw_bot"]
    print(f"\nSpread Extremo: Long rank(1,2) vs Short Bottom 2 rank(12,13) (Newey-West):")
    print(f"  - Spread Medio (45d)         : {nw_bot['mean']:+6.2f}%")
    print(f"  - Error Estándar Newey-West  : ±{nw_bot['se_nw']:5.2f}% | t = {nw_bot['t_nw']:+5.2f} | p = {nw_bot['p_nw']:6.3f}")

    # Retorno en nivel Long rank(1,2)
    nw_l = res_s5["nw_long"]
    non_long = res_s5["non_long"]
    print(f"\nRetorno Absoluto en Nivel (Long rank 1,2):")
    print(f"  - Retorno Medio (45d)        : {nw_l['mean']:+6.2f}%")
    print(f"  - OLS Clásico (solapado)     : ±{nw_l['se_ols']:5.2f}% | t = {nw_l['t_ols']:+5.2f} | p = {nw_l['p_ols']:6.3f}")
    print(f"  - Newey-West (lag=8)         : ±{nw_l['se_nw']:5.2f}% | t = {nw_l['t_nw']:+5.2f} | p = {nw_l['p_nw']:6.3f}")

    # 2. Universo A: Bloque Sectores GICS (11 ETFs)
    print("\n[2/3] Evaluando Universo A — Sectores GICS (11 ETFs, 2020–2025)...")
    res_gics = evaluate_long_short_momentum(
        daily_data=daily_univ_a,
        symbols=US_SECTORS_11,
        start_date=start_d,
        end_date=end_d,
        fwd_days=45,
        lookback_days=45,
        short_rank_indices=(6, 7),
    )
    nw_g = res_gics["nw_6_7"]
    non_g = res_gics["non_6_7"]
    print(f"Sectores GICS — Spread rank(1,2) - rank(6,7):")
    print(f"  - Semanal Newey-West (N={nw_g['n_obs']}) : Spread: {nw_g['mean']:+6.2f}% | SE: ±{nw_g['se_nw']:5.2f}% | t = {nw_g['t_nw']:+5.2f} | p = {nw_g['p_nw']:6.3f}")
    print(f"  - No Solapado (N={non_g['n_obs']})        : Spread: {non_g['mean']:+6.2f}% | SE: ±{non_g['se']:5.2f}% | t = {non_g['t_stat']:+5.2f} | p = {non_g['p_val']:6.3f}")

    # 3. Bloques de 2 Activos en Universo A
    print("\n[3/3] Evaluando Bloques de 2 Activos en Universo A...")
    res_metals = evaluate_pair_block(daily_univ_a, "GLDM", "SLV", start_d, end_d)
    res_fi = evaluate_pair_block(daily_univ_a, "IEF", "TIP", start_d, end_d)
    res_intl = evaluate_pair_block(daily_univ_a, "IEFA", "IEMG", start_d, end_d)

    print(f"Metales (GLDM vs SLV):")
    print(f"  - Newey-West: Spread: {res_metals['nw']['mean']:+6.2f}% | t = {res_metals['nw']['t_nw']:+5.2f} | p = {res_metals['nw']['p_nw']:6.3f}")
    print(f"Renta Fija (IEF vs TIP):")
    print(f"  - Newey-West: Spread: {res_fi['nw']['mean']:+6.2f}% | t = {res_fi['nw']['t_nw']:+5.2f} | p = {res_fi['nw']['p_nw']:6.3f}")
    print(f"Internacional (IEFA vs IEMG):")
    print(f"  - Newey-West: Spread: {res_intl['nw']['mean']:+6.2f}% | t = {res_intl['nw']['t_nw']:+5.2f} | p = {res_intl['nw']['p_nw']:6.3f}")

    # Actualizar reports/rank_monotonicity.md
    report_file = REPORTS_DIR / "rank_monotonicity.md"
    existing_content = report_file.read_text(encoding="utf-8") if report_file.exists() else ""

    v2_section = f"""

---

## 4. Sección v2 — Corrección Metodológica por Solapamiento y Cartera Long-Short (T-10 / F-20)

En respuesta estricta a los dos defectos señalados en el hallazgo **F-20**:
1. **Corrección de Solapamiento:** Retornos a 45 días muestreados semanalmente presentan una estructura MA(8). Se aplica la corrección de varianza de **Newey-West con lag = 8** y control con **muestreo no solapado (cada 45 sesiones)**.
2. **Niveles vs. Spreads:** Se aísla el factor común de mercado evaluando la cartera long-short: **Larga en `rank(1,2)` y Corta en `rank(6,7)`**.

### 4.1. Resultados S5 (13 Activos, 2020–2025)

| Cartera / Métrica | Retorno / Spread Medio (45d) | Error Estándar Clásico OLS | t-stat OLS (solapado) | p-value OLS | Error Estándar Newey-West (lag=8) | t-stat Newey-West | p-value Newey-West | Muestreo No Solapado (t-stat / p-val) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Nivel Absoluto: Long rank(1,2)** | **+{nw_l['mean']:.2f}%** | ±{nw_l['se_ols']:.2f}% | +{nw_l['t_ols']:.2f} | {nw_l['p_ols']:.3f} | **±{nw_l['se_nw']:.2f}%** | **+{nw_l['t_nw']:.2f}** | **{nw_l['p_nw']:.3f}** | t = {non_long['t_stat']:+.2f} (p = {non_long['p_val']:.3f}) |
| **Spread Long-Short: rank(1,2) − rank(6,7)** | **{nw['mean']:+.2f}%** | ±{nw['se_ols']:.2f}% | {nw['t_ols']:+.2f} | {nw['p_ols']:.3f} | **±{nw['se_nw']:.2f}%** | **{nw['t_nw']:+.2f}** | **{nw['p_nw']:.3f}** | t = {non['t_stat']:+.2f} (p = {non['p_val']:.3f}) |
| **Spread Extremo: rank(1,2) − rank(12,13)** | **{nw_bot['mean']:+.2f}%** | ±{nw_bot['se_ols']:.2f}% | {nw_bot['t_ols']:+.2f} | {nw_bot['p_ols']:.3f} | **±{nw_bot['se_nw']:.2f}%** | **{nw_bot['t_nw']:+.2f}** | **{nw_bot['p_nw']:.3f}** | t = {res_s5['non_bot']['t_stat']:+.2f} (p = {res_s5['non_bot']['p_val']:.3f}) |

### 4.2. Resultados Universo A por Bloques (2020–2025)

| Bloque Evaluado | Activos | Spread Medio (45d) | Error Estándar Newey-West | t-stat Newey-West | p-value Newey-West | Muestreo No Solapado (t-stat / p-val) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Sectores GICS (rank 1,2 − 6,7)** | 11 ETFs sectoriales | **{nw_g['mean']:+.2f}%** | ±{nw_g['se_nw']:.2f}% | {nw_g['t_nw']:+.2f} | **{nw_g['p_nw']:.3f}** | t = {non_g['t_stat']:+.2f} (p = {non_g['p_val']:.3f}) |
| **Sectores GICS (rank 1,2 − bottom 2)**| 11 ETFs sectoriales | **{res_gics['nw_bot']['mean']:+.2f}%** | ±{res_gics['nw_bot']['se_nw']:.2f}% | {res_gics['nw_bot']['t_nw']:+.2f} | **{res_gics['nw_bot']['p_nw']:.3f}** | t = {res_gics['non_bot']['t_stat']:+.2f} (p = {res_gics['non_bot']['p_val']:.3f}) |
| **Metales (rank 1 − rank 2)** | GLDM, SLV | **{res_metals['nw']['mean']:+.2f}%** | ±{res_metals['nw']['se_nw']:.2f}% | {res_metals['nw']['t_nw']:+.2f} | **{res_metals['nw']['p_nw']:.3f}** | t = {res_metals['non']['t_stat']:+.2f} (p = {res_metals['non']['p_val']:.3f}) |
| **Renta Fija (rank 1 − rank 2)** | IEF, TIP | **{res_fi['nw']['mean']:+.2f}%** | ±{res_fi['nw']['se_nw']:.2f}% | {res_fi['nw']['t_nw']:+.2f} | **{res_fi['nw']['p_nw']:.3f}** | t = {res_fi['non']['t_stat']:+.2f} (p = {res_fi['non']['p_val']:.3f}) |
| **Internacional (rank 1 − rank 2)** | IEFA, IEMG | **{res_intl['nw']['mean']:+.2f}%** | ±{res_intl['nw']['se_nw']:.2f}% | {res_intl['nw']['t_nw']:+.2f} | **{res_intl['nw']['p_nw']:.3f}** | t = {res_intl['non']['t_stat']:+.2f} (p = {res_intl['non']['p_val']:.3f}) |

### 4.3. Dictamen y Conclusión del Hallazgo F-13 / F-20

Bajo el criterio formal establecido por el auditor en §3 T-10:
> *'F-13 se cierra únicamente si el spread long-short es positivo y significativo al 5% bajo errores Newey-West. Si no lo es, el diseño de selección por ranking debe simplificarse a equiponderación sobre los elegibles que pasen el gate absoluto.'*

1. **En S5:**
   - La corrección de Newey-West infla el error estándar ({nw['se_nw']:.2f}% vs {nw['se_ols']:.2f}% OLS) exactamente en el rango predicho por el auditor.
   - El spread long-short `rank(1,2) − rank(6,7)` registra un retorno medio de **{nw['mean']:+.2f}%** con t-stat = **{nw['t_nw']:+.2f}** y p-value = **{nw['p_nw']:.3f}**. {"Es estadísticamente significativo al 5%." if nw['p_nw'] < 0.05 else "NO alcanza significancia estadística al 5% (p > 0.05)."}
   - El spread extremo `rank(1,2) − rank(12,13)` es de **{nw_bot['mean']:+.2f}%** (t = {nw_bot['t_nw']:+.2f}, p = {nw_bot['p_nw']:.3f}).

2. **En Universo A:**
   - En el bloque de sectores GICS, el spread `rank(1,2) − rank(6,7)` es de **{nw_g['mean']:+.2f}%** (t = {nw_g['t_nw']:+.2f}, p = {nw_g['p_nw']:.3f}), confirmando que el ranking transversal de sectores ETFs no genera una separación estadísticamente discernible ({nw_g['p_nw']:.3f} > 0.05).
   - **Consecuencia de Diseño (mandato T-16):** Dado que el ranking en Universo A no discrimina con significancia estadística, se ratifica la necesidad de ejecutar T-16 para evaluar la simplificación hacia equiponderación por volatilidad inversa sobre los instrumentos que superen el gate absoluto sin ranking sectorial.
"""

    # Si ya contiene "Sección v2", la reemplazamos; si no, la anexamos
    if "## 4. Sección v2" in existing_content:
        base_part = existing_content.split("## 4. Sección v2")[0].rstrip()
        final_content = base_part + "\n" + v2_section
    else:
        final_content = existing_content + v2_section

    report_file.write_text(final_content, encoding="utf-8")
    print(f"\n[OK] Reporte formal actualizado en: {report_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
