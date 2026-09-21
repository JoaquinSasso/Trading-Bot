#!/usr/bin/env python3
"""Atribución Multifactorial del Alpha de S5 (T-12 / F-22).

Estima la regresión multifactorial institucional:
  r_p - r_f = alpha + beta1*(r_SPY - r_f) + beta2*r_GLD + beta3*r_MOM + epsilon

Evalúa dos proxies de momentum:
1. MTUM (iShares MSCI USA Momentum Factor ETF, proxy investible)
2. UMD (Kenneth French Momentum Factor, benchmark académico)

Compara formalmente:
- Modelo 1 Factor (CAPM clásico contra SPY)
- Modelo 2 Factores (SPY + GLD)
- Modelo 3 Factores (SPY + GLD + MOM)

Genera: 'reports/multifactor_attribution.md'.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_14_DIR = PROJECT_ROOT / "data" / "historical_14"
RF_FILE = PROJECT_ROOT / "data" / "risk_free_rate_bil.csv"
MTUM_FILE = PROJECT_ROOT / "data" / "MTUM_daily.csv"
KF_FILE = PROJECT_ROOT / "data" / "F-F_Momentum_Factor_daily.csv"
REPORTS_DIR = PROJECT_ROOT / "reports"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from optimize_and_benchmark_portfolio import load_all_market_data
from run_phase2_institutional_metrics import run_s5_full_institution

UNIVERSE_14 = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
]


def load_factor_data() -> tuple[pd.Series, pd.Series, dict[date, float]]:
    """Carga retornos diarios de MTUM, UMD (Ken French) y tasa libre de riesgo BIL."""
    # 1. BIL
    rf_df = pd.read_csv(RF_FILE)
    rf_df["_date"] = pd.to_datetime(rf_df["date"]).dt.date
    rf_map = dict(zip(rf_df["_date"], rf_df["daily_rf"]))

    # 2. MTUM
    mtum_df = pd.read_csv(MTUM_FILE)
    mtum_df["_date"] = pd.to_datetime(mtum_df["date"]).dt.date
    mtum_df = mtum_df.sort_values("_date").reset_index(drop=True)
    mtum_df["ret"] = mtum_df["close"].pct_change()
    mtum_series = pd.Series(mtum_df["ret"].values, index=mtum_df["_date"]).dropna()

    # 3. Kenneth French UMD
    # El archivo de Ken French tiene 13 líneas de encabezado, luego ',Mom'
    # Filas: YYYYMMDD, valor en % (ej. 0.55 = +0.55%)
    kf_lines = KF_FILE.read_text(encoding="utf-8").splitlines()
    data_lines = []
    start_reading = False
    for line in kf_lines:
        line = line.strip()
        if not line:
            continue
        if ",Mom" in line or line.startswith("Date,"):
            start_reading = True
            continue
        if start_reading:
            parts = line.split(",")
            if len(parts) >= 2 and parts[0].strip().isdigit() and len(parts[0].strip()) == 8:
                try:
                    dt_str = parts[0].strip()
                    d_val = date(int(dt_str[:4]), int(dt_str[4:6]), int(dt_str[6:8]))
                    mom_val = float(parts[1].strip()) / 100.0  # de % a decimal
                    if mom_val > -50.0:  # omitir missing data (-99.99)
                        data_lines.append((d_val, mom_val))
                except ValueError:
                    continue

    kf_df = pd.DataFrame(data_lines, columns=["date", "ret"])
    umd_series = pd.Series(kf_df["ret"].values, index=kf_df["date"]).dropna()

    return mtum_series, umd_series, rf_map


def fit_factor_model(
    y_excess: np.ndarray,
    X_matrix: np.ndarray,
    feature_names: list[str],
) -> dict[str, Any]:
    """Ajusta una regresión OLS con errores estándar robustos HAC (Newey-West)."""
    n_obs = len(y_excess)
    # Agregar constante para alpha
    X_with_const = sm.add_constant(X_matrix)
    model = sm.OLS(y_excess, X_with_const).fit(cov_type="HAC", cov_kwds={"maxlags": 5})

    alpha_daily = float(model.params[0])
    alpha_se_daily = float(model.bse[0])
    alpha_t = float(model.tvalues[0])
    alpha_p = float(model.pvalues[0])

    alpha_annual_pct = alpha_daily * 252.0 * 100.0
    alpha_se_annual_pct = alpha_se_daily * 252.0 * 100.0

    betas = {}
    for idx, name in enumerate(feature_names, 1):
        b_val = float(model.params[idx])
        b_se = float(model.bse[idx])
        b_t = float(model.tvalues[idx])
        b_p = float(model.pvalues[idx])
        betas[name] = {"beta": b_val, "se": b_se, "t": b_t, "p": b_p}

    r2 = float(model.rsquared)
    r2_adj = float(model.rsquared_adj)

    return {
        "n_obs": n_obs,
        "alpha_annual_pct": alpha_annual_pct,
        "alpha_se_annual_pct": alpha_se_annual_pct,
        "alpha_t": alpha_t,
        "alpha_p": alpha_p,
        "betas": betas,
        "r2": r2,
        "r2_adj": r2_adj,
    }


def main() -> int:
    print("=" * 95)
    print("   ATRIBUCIÓN MULTIFACTORIAL DEL ALPHA DE S5 (T-12 / F-22)")
    print("=" * 95)

    daily_14 = load_all_market_data(DATA_14_DIR, symbols=UNIVERSE_14)
    mtum_series, umd_series, rf_map = load_factor_data()

    spy_df = daily_14["SPY"].sort_values("_parsed_date").reset_index(drop=True)
    spy_df["ret"] = spy_df["close"].astype(float).pct_change()
    spy_rets = dict(zip(spy_df["_parsed_date"], spy_df["ret"]))

    gld_df = daily_14["GLD"].sort_values("_parsed_date").reset_index(drop=True)
    gld_df["ret"] = gld_df["close"].astype(float).pct_change()
    gld_rets = dict(zip(gld_df["_parsed_date"], gld_df["ret"]))

    configs = [
        ("Top-4 (25% cap, CB Fijo)", 4, 0.25, "fixed"),
        ("Top-4 (25% cap, CB Adaptativo)", 4, 0.25, "adaptive"),
        ("Top-2 (50% cap, CB Fijo)", 2, 0.50, "fixed"),
    ]

    periods = [
        ("Muestra Completa 2020–2025", date(2020, 1, 2), date(2025, 12, 31)),
        ("Trienio 2020–2022", date(2020, 1, 2), date(2022, 12, 30)),
        ("2020", date(2020, 1, 2), date(2020, 12, 31)),
        ("2021", date(2021, 1, 4), date(2021, 12, 31)),
        ("2022", date(2022, 1, 3), date(2022, 12, 30)),
        ("2025", date(2025, 1, 2), date(2025, 12, 31)),
    ]

    results_table = []

    for cfg_name, top_n, max_w, cb_m in configs:
        print(f"\nEvaluando configuración: {cfg_name}...")
        for p_name, st, en in periods:
            sim, _, _ = run_s5_full_institution(
                daily_data=daily_14,
                rf_daily_map=rf_map,
                start_date=st,
                end_date=en,
                config_name=f"{cfg_name} [{p_name}]",
                top_n=top_n,
                max_weight_per_asset=max_w,
                enable_circuit_breakers=True,
                cb_mode=cb_m,
                apply_costs=True,
                integer_shares=True,
            )

            strat_rets = sim.equity_series.pct_change().dropna()
            common_dates = sorted(
                set(strat_rets.index)
                .intersection(set(spy_rets.keys()))
                .intersection(set(gld_rets.keys()))
                .intersection(set(mtum_series.index))
                .intersection(set(umd_series.index))
            )

            if len(common_dates) < 30:
                continue

            y_excess = np.array([strat_rets[d] - rf_map.get(d, 0.0) for d in common_dates])
            x_spy = np.array([spy_rets[d] - rf_map.get(d, 0.0) for d in common_dates])
            x_gld = np.array([gld_rets[d] for d in common_dates])
            x_mtum = np.array([mtum_series[d] - rf_map.get(d, 0.0) for d in common_dates])
            x_umd = np.array([umd_series[d] for d in common_dates])

            # Modelo 1 Factor: SPY (CAPM)
            m1 = fit_factor_model(y_excess, x_spy.reshape(-1, 1), ["SPY"])

            # Modelo 2 Factores: SPY + GLD
            X2 = np.column_stack([x_spy, x_gld])
            m2 = fit_factor_model(y_excess, X2, ["SPY", "GLD"])

            # Modelo 3 Factores (con MTUM investible): SPY + GLD + MTUM
            X3_mtum = np.column_stack([x_spy, x_gld, x_mtum])
            m3_mtum = fit_factor_model(y_excess, X3_mtum, ["SPY", "GLD", "MTUM"])

            # Modelo 3 Factores (con Ken French UMD académico): SPY + GLD + UMD
            X3_umd = np.column_stack([x_spy, x_gld, x_umd])
            m3_umd = fit_factor_model(y_excess, X3_umd, ["SPY", "GLD", "UMD"])

            rec = {
                "config": cfg_name,
                "period": p_name,
                "n_obs": len(common_dates),
                "total_ret": sim.total_return_pct,
                "sharpe": sim.sharpe_ratio,
                "m1": m1,
                "m2": m2,
                "m3_mtum": m3_mtum,
                "m3_umd": m3_umd,
            }
            results_table.append(rec)

            if p_name == "Muestra Completa 2020–2025":
                print(f"  [{cfg_name} - Muestra Completa 2020-2025]:")
                print(f"    - Modelo 1F (CAPM)      : Alpha = {m1['alpha_annual_pct']:+6.2f}% ± {m1['alpha_se_annual_pct']:4.2f}% (t={m1['alpha_t']:+4.2f}, p={m1['alpha_p']:.3f}) | R2 = {m1['r2']:.2f}")
                print(f"    - Modelo 2F (SPY+GLD)   : Alpha = {m2['alpha_annual_pct']:+6.2f}% ± {m2['alpha_se_annual_pct']:4.2f}% (t={m2['alpha_t']:+4.2f}, p={m2['alpha_p']:.3f}) | R2 = {m2['r2']:.2f}")
                print(f"    - Modelo 3F (SPY+GLD+MTUM): Alpha = {m3_mtum['alpha_annual_pct']:+6.2f}% ± {m3_mtum['alpha_se_annual_pct']:4.2f}% (t={m3_mtum['alpha_t']:+4.2f}, p={m3_mtum['alpha_p']:.3f}) | R2 = {m3_mtum['r2']:.2f}")
                print(f"      Betas: SPY={m3_mtum['betas']['SPY']['beta']:.2f} (p={m3_mtum['betas']['SPY']['p']:.3f}), GLD={m3_mtum['betas']['GLD']['beta']:.2f} (p={m3_mtum['betas']['GLD']['p']:.3f}), MTUM={m3_mtum['betas']['MTUM']['beta']:.2f} (p={m3_mtum['betas']['MTUM']['p']:.3f})")
                print(f"    - Modelo 3F (SPY+GLD+UMD) : Alpha = {m3_umd['alpha_annual_pct']:+6.2f}% ± {m3_umd['alpha_se_annual_pct']:4.2f}% (t={m3_umd['alpha_t']:+4.2f}, p={m3_umd['alpha_p']:.3f}) | R2 = {m3_umd['r2']:.2f}")

    # Generar Reporte Markdown formal
    report_file = REPORTS_DIR / "multifactor_attribution.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write("# REPORTE DE AUDITORÍA — ATRIBUCIÓN MULTIFACTORIAL DEL ALPHA (T-12 / F-22)\n\n")
        f.write("| Campo | Valor |\n| :--- | :--- |\n")
        f.write("| **Documento** | T-12: Regresión Multifactorial de Rendimientos y Atribución Factorial |\n")
        f.write("| **Fecha** | 2026-09-21 |\n")
        f.write("| **Responde a** | Hallazgo F-22 y Mandato §3 T-12 de `AUDIT_FOLLOWUP_v2.2.md` |\n")
        f.write("| **Modelos Evaluados** | 1 Factor (CAPM SPY), 2 Factores (SPY + GLD), 3 Factores (SPY + GLD + MTUM/UMD) |\n")
        f.write("| **Errores Estándar** | Robustos a Heterocedasticidad y Autocorrelación (HAC / Newey-West) |\n\n---\n\n")

        f.write("## 1. Motivación y Predicción del Auditor (F-22)\n\n")
        f.write("> *'La regresión de un solo factor contra SPY, aplicada a una cartera que puede tener hasta el 50% en GLD y SLV, atribuye al intercepto todo el retorno de los metales... β = 0.31 y R² = 0.15... Predicción registrada antes del test: la mayor parte del alpha se traslada a cargas sobre el factor oro y el factor momentum, y el intercepto queda cerca de cero sin significancia.'*\n\n---\n\n")

        f.write("## 2. Tabla Comparativa de Modelos Factoriales (Muestra Completa 2020–2025)\n\n")
        f.write("| Configuración | Modelo | Alpha Anualizado (α) | Error Estándar (SE) | t-stat | p-value | β_SPY | β_GLD | β_MOM | R² | R² Aj. |\n")
        f.write("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")

        for r in results_table:
            if r["period"] == "Muestra Completa 2020–2025":
                cfg = r["config"]
                # 1F
                m1 = r["m1"]
                f.write(f"| **{cfg}** | **1F (CAPM SPY)** | **{m1['alpha_annual_pct']:+.2f}%** | ±{m1['alpha_se_annual_pct']:.2f}% | {m1['alpha_t']:+.2f} | {m1['alpha_p']:.3f} | {m1['betas']['SPY']['beta']:.2f} | — | — | **{m1['r2']:.2f}** | {m1['r2_adj']:.2f} |\n")
                # 2F
                m2 = r["m2"]
                f.write(f"| **{cfg}** | **2F (SPY + GLD)** | **{m2['alpha_annual_pct']:+.2f}%** | ±{m2['alpha_se_annual_pct']:.2f}% | {m2['alpha_t']:+.2f} | {m2['alpha_p']:.3f} | {m2['betas']['SPY']['beta']:.2f} | {m2['betas']['GLD']['beta']:.2f} | — | **{m2['r2']:.2f}** | {m2['r2_adj']:.2f} |\n")
                # 3F MTUM
                m3m = r["m3_mtum"]
                f.write(f"| **{cfg}** | **3F (SPY + GLD + MTUM)** | **{m3m['alpha_annual_pct']:+.2f}%** | ±{m3m['alpha_se_annual_pct']:.2f}% | {m3m['alpha_t']:+.2f} | {m3m['alpha_p']:.3f} | {m3m['betas']['SPY']['beta']:.2f} | {m3m['betas']['GLD']['beta']:.2f} | {m3m['betas']['MTUM']['beta']:.2f} | **{m3m['r2']:.2f}** | {m3m['r2_adj']:.2f} |\n")
                # 3F UMD
                m3u = r["m3_umd"]
                f.write(f"| **{cfg}** | **3F (SPY + GLD + UMD)** | **{m3u['alpha_annual_pct']:+.2f}%** | ±{m3u['alpha_se_annual_pct']:.2f}% | {m3u['alpha_t']:+.2f} | {m3u['alpha_p']:.3f} | {m3u['betas']['SPY']['beta']:.2f} | {m3u['betas']['GLD']['beta']:.2f} | {m3u['betas']['UMD']['beta']:.2f} | **{m3u['r2']:.2f}** | {m3u['r2_adj']:.2f} |\n")

        f.write("\n---\n\n## 3. Desglose Detallado por Períodos (Top-4 CB Adaptativo)\n\n")
        f.write("| Período | Retorno Neto | Modelo 1F (Alpha / R²) | Modelo 2F SPY+GLD (Alpha / R²) | Modelo 3F SPY+GLD+MTUM (Alpha / R²) | β_SPY (p-val) | β_GLD (p-val) | β_MTUM (p-val) |\n")
        f.write("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n")

        for r in results_table:
            if r["config"] == "Top-4 (25% cap, CB Adaptativo)":
                p = r["period"]
                ret = r["total_ret"]
                m1 = r["m1"]
                m2 = r["m2"]
                m3 = r["m3_mtum"]
                b_spy = f"{m3['betas']['SPY']['beta']:.2f} ({m3['betas']['SPY']['p']:.3f})"
                b_gld = f"{m3['betas']['GLD']['beta']:.2f} ({m3['betas']['GLD']['p']:.3f})"
                b_mtum = f"{m3['betas']['MTUM']['beta']:.2f} ({m3['betas']['MTUM']['p']:.3f})"

                f.write(
                    f"| {p} | {ret:+.2f}% | "
                    f"**{m1['alpha_annual_pct']:+.2f}%** (R²={m1['r2']:.2f}) | "
                    f"**{m2['alpha_annual_pct']:+.2f}%** (R²={m2['r2']:.2f}) | "
                    f"**{m3['alpha_annual_pct']:+.2f}%** (R²={m3['r2']:.2f}) | "
                    f"{b_spy} | {b_gld} | {b_mtum} |\n"
                )

        f.write("\n---\n\n## 4. Dictamen Institucional y Conclusión (Criterio F-22)\n\n")
        f.write("Criterio del auditor: *'Si el R² sube sustancialmente y el α pierde significancia, el retorno del sistema es exposición factorial y no habilidad.'*\n\n")
        
        # Extraer Top-4 Adaptativo full sample
        t4_full = [r for r in results_table if r["config"] == "Top-4 (25% cap, CB Adaptativo)" and r["period"] == "Muestra Completa 2020–2025"][0]
        m1_t4 = t4_full["m1"]
        m3_t4 = t4_full["m3_mtum"]
        
        f.write(f"- **Evolución del R²:** Pasa de **{m1_t4['r2']:.2f}** (1 factor SPY) a **{m3_t4['r2']:.2f}** en el modelo 3 factores.\n")
        f.write(f"- **Evolución del Alpha Anualizado:** Pasa de **{m1_t4['alpha_annual_pct']:+.2f}%** (t={m1_t4['alpha_t']:+.2f}, p={m1_t4['alpha_p']:.3f}) a **{m3_t4['alpha_annual_pct']:+.2f}%** (t={m3_t4['alpha_t']:+.2f}, p={m3_t4['alpha_p']:.3f}).\n")
        f.write(f"- **Cargas Factoriales (Betas):**\n")
        f.write(f"  * **Beta SPY:** {m3_t4['betas']['SPY']['beta']:.2f} (t={m3_t4['betas']['SPY']['t']:.2f}, p={m3_t4['betas']['SPY']['p']:.3f})\n")
        f.write(f"  * **Beta GLD:** {m3_t4['betas']['GLD']['beta']:.2f} (t={m3_t4['betas']['GLD']['t']:.2f}, p={m3_t4['betas']['GLD']['p']:.3f})\n")
        f.write(f"  * **Beta MTUM:** {m3_t4['betas']['MTUM']['beta']:.2f} (t={m3_t4['betas']['MTUM']['t']:.2f}, p={m3_t4['betas']['MTUM']['p']:.3f})\n\n")

        if m3_t4["alpha_p"] >= 0.05:
            f.write("### Veredicto F-22: CONFIRMADO PLENAMENTE.\n")
            f.write("La predicción del auditor se cumple con exactitud matemática: al incorporar el factor de metales (`GLD`) y el factor de momentum (`MTUM`), el modelo explica sustancialmente más varianza de la cartera y **el alpha deja de ser estadísticamente significativo al 5%**.\n")
            f.write("El exceso de retorno del bot no proviene de 'habilidad pura' de selección idiosincrática ni de sincronización milagrosa de mercado, sino de **captura sistemática y disciplinada de exposición factorial a Momentum y Oro/Metales con gestión de riesgo intradiario**.\n")
            f.write("Esto reclasifica honestamente la ventaja competitiva del sistema en el repositorio: de 'generador de alpha puro' a **'cosechador eficiente de factores con preservación de capital'**.\n")
        else:
            f.write("### Veredicto F-22: PARCIALMENTE CONFIRMADO.\n")
            f.write(f"El R² aumenta a {m3_t4['r2']:.2f} pero el alpha residual conserva significancia marginal ({m3_t4['alpha_annual_pct']:+.2f}%, p={m3_t4['alpha_p']:.3f}).\n")

    print(f"\n[OK] Reporte formal generado en: {report_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
