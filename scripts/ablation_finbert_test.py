#!/usr/bin/env python3
"""Ablation Test: S5 Dual Momentum Leader v1.2.0 con FinBERT vs sin FinBERT.

Responde al Hallazgo P0 (F-05/F-06) de la Auditoría Externa Cuantitativa:
- Evalúa el impacto real del filtro de sentimiento FinBERT en:
  1. 2025 (Año Completo)
  2. 2020 (Crash COVID + Rebote)
  3. 2021 (Mercado Alcista)
  4. 2022 (Mercado Bajista)
  5. 2020-2022 (3 Años Fuera de Muestra Compuesto)
- Registra cada evento de veto: ticker, fecha, negative_share, sentiment_mean.
- Compara métricas clave: Retorno, Alpha vs SPY, Sharpe, MaxDD, WinRate, PF, Trades.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from optimize_and_benchmark_portfolio import (
    EASTERN_TZ,
    SimulationResult,
    TradeRecord,
    compute_drawdown,
    compute_sharpe,
    ema,
    load_all_market_data,
)
from tbot.news.store import HistoricalNewsFeatureStore
from tbot.regime.filter import MarketRegime

UNIVERSE_14 = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
]


@dataclass
class VetoEvent:
    symbol: str
    date: date
    momentum_pct: float
    negative_share: float
    sentiment_mean: float
    top_topics: list[str]


def run_s5_ablation_simulation(
    daily_data: dict[str, pd.DataFrame],
    start_date: date,
    end_date: date,
    config_name: str,
    initial_capital: float = 2000.0,
    top_n_leaders: int = 2,
    momentum_lookback_days: int = 45,
    trailing_ema_period: int = 25,
    use_finbert: bool = True,
    finbert_store: HistoricalNewsFeatureStore | None = None,
    symbols: list[str] | None = None,
    annual_cash_yield: float = 0.0,
) -> tuple[SimulationResult, list[VetoEvent]]:
    """Simulación S5 con instrumentación para registrar cada veto de FinBERT."""
    all_symbols = symbols if symbols is not None else list(daily_data.keys())
    spy_df = daily_data["SPY"]

    all_dates = sorted(
        set.intersection(
            *[
                set(
                    df[
                        (df["_parsed_date"] >= start_date)
                        & (df["_parsed_date"] <= end_date)
                    ]["_parsed_date"]
                )
                for s, df in daily_data.items()
                if s in all_symbols or s == "SPY"
            ]
        )
    )

    cash = initial_capital
    open_positions: dict[str, dict] = {}
    closed_trades: list[TradeRecord] = []
    equity_history: dict[date, float] = {}
    veto_events: list[VetoEvent] = []

    daily_yield_rate = (
        (1.0 + annual_cash_yield) ** (1.0 / 252.0) - 1.0
        if annual_cash_yield > 0
        else 0.0
    )

    for cur_date in all_dates:
        cur_dt = datetime.combine(cur_date, datetime.min.time(), tzinfo=EASTERN_TZ)

        # 1. Filtro Macro de Régimen: SPY sobre EMA50
        spy_past = spy_df[spy_df["_parsed_date"] < cur_date]
        regime = MarketRegime.BULL_CALM
        if len(spy_past) >= 50:
            spy_closes = spy_past["close"].astype(float)
            ema50 = ema(spy_closes, 50).iloc[-1]
            if spy_closes.iloc[-1] < ema50:
                regime = MarketRegime.BEAR

        # 2. Si régimen bajista: rotación preventiva 100% a efectivo
        if regime == MarketRegime.BEAR:
            for sym in list(open_positions.keys()):
                pos = open_positions[sym]
                bar = daily_data[sym][daily_data[sym]["_parsed_date"] == cur_date].iloc[0]
                ex_p = float(bar["close"])
                pnl = (ex_p - pos["entry_price"]) * pos["shares"]
                pnl_pct = (ex_p - pos["entry_price"]) / pos["entry_price"]
                r_dist = pos["entry_price"] - pos["initial_stop"]
                pnl_r = pnl / (r_dist * pos["shares"]) if r_dist > 0 else 0.0
                cash += ex_p * pos["shares"]
                closed_trades.append(
                    TradeRecord(
                        symbol=sym,
                        entry_date=pos["entry_date"],
                        exit_date=cur_date,
                        entry_price=pos["entry_price"],
                        exit_price=ex_p,
                        shares=pos["shares"],
                        pnl_usd=pnl,
                        pnl_pct=pnl_pct,
                        pnl_r=pnl_r,
                        exit_reason="regime_bear_exit",
                    )
                )
                del open_positions[sym]

            if daily_yield_rate > 0:
                cash *= 1.0 + daily_yield_rate

        # 3. Si régimen alcista: gestionar trailing stop y líderes
        else:
            for sym in list(open_positions.keys()):
                pos = open_positions[sym]
                pos["days_held"] += 1
                bar = daily_data[sym][daily_data[sym]["_parsed_date"] == cur_date].iloc[0]
                cur_close = float(bar["close"])

                past_bars = daily_data[sym][daily_data[sym]["_parsed_date"] <= cur_date]
                ema20 = ema(past_bars["close"].astype(float), trailing_ema_period).iloc[-1]

                # Salida si cierra bajo stop o bajo EMA25 tras 3 días
                if cur_close < pos["stop_loss"] or (pos["days_held"] >= 3 and cur_close < ema20):
                    ex_p = cur_close
                    pnl = (ex_p - pos["entry_price"]) * pos["shares"]
                    pnl_pct = (ex_p - pos["entry_price"]) / pos["entry_price"]
                    r_dist = pos["entry_price"] - pos["initial_stop"]
                    pnl_r = pnl / (r_dist * pos["shares"]) if r_dist > 0 else 0.0
                    cash += ex_p * pos["shares"]
                    closed_trades.append(
                        TradeRecord(
                            symbol=sym,
                            entry_date=pos["entry_date"],
                            exit_date=cur_date,
                            entry_price=pos["entry_price"],
                            exit_price=ex_p,
                            shares=pos["shares"],
                            pnl_usd=pnl,
                            pnl_pct=pnl_pct,
                            pnl_r=pnl_r,
                            exit_reason="trailing_ema25",
                        )
                    )
                    del open_positions[sym]
                else:
                    pos["stop_loss"] = max(pos["stop_loss"], ema20 * 0.985)

            # 4. Asignación de capital en líderes si hay cupo
            available_slots = top_n_leaders - len(open_positions)
            if available_slots > 0:
                candidates = []
                for sym in all_symbols:
                    if sym in open_positions:
                        continue
                    past_bars = daily_data[sym][daily_data[sym]["_parsed_date"] <= cur_date]
                    if len(past_bars) < momentum_lookback_days + 5:
                        continue
                    closes = past_bars["close"].astype(float)
                    cur_close = float(closes.iloc[-1])
                    ema20 = ema(closes, trailing_ema_period).iloc[-1]
                    if cur_close < ema20:
                        continue
                    p_past = float(closes.iloc[-momentum_lookback_days])
                    if p_past <= 0:
                        continue
                    mom_pct = (cur_close - p_past) / p_past
                    if mom_pct <= 0:
                        continue

                    # Filtro de Veto FinBERT
                    if use_finbert and finbert_store is not None:
                        news_feat = finbert_store.get_features(sym, as_of=cur_dt)
                        if news_feat.negative_share >= 0.35:
                            veto_events.append(
                                VetoEvent(
                                    symbol=sym,
                                    date=cur_date,
                                    momentum_pct=mom_pct * 100.0,
                                    negative_share=news_feat.negative_share,
                                    sentiment_mean=news_feat.sentiment_mean,
                                    top_topics=news_feat.top_topics,
                                )
                            )
                            continue

                    candidates.append((sym, mom_pct, cur_close, ema20))

                # Ordenar descendente por momentum
                candidates.sort(key=lambda x: x[1], reverse=True)

                total_equity = cash + sum(
                    p["shares"]
                    * float(
                        daily_data[s][daily_data[s]["_parsed_date"] == cur_date]["close"].iloc[0]
                    )
                    for s, p in open_positions.items()
                )
                target_alloc = total_equity / top_n_leaders

                for sym, mom_pct, cur_close, ema20 in candidates[:available_slots]:
                    alloc = min(cash, target_alloc)
                    if alloc >= 50.0:
                        shares = alloc / cur_close
                        cash -= shares * cur_close
                        stop_loss = min(cur_close * 0.95, ema20 * 0.985)
                        open_positions[sym] = {
                            "symbol": sym,
                            "entry_date": cur_date,
                            "entry_price": cur_close,
                            "shares": shares,
                            "stop_loss": stop_loss,
                            "initial_stop": stop_loss,
                            "take_profit": 999999.0,
                            "days_held": 0,
                        }

        # 5. Registrar equidad diaria
        invested_val = sum(
            p["shares"]
            * float(
                daily_data[s][daily_data[s]["_parsed_date"] == cur_date]["close"].iloc[0]
            )
            for s, p in open_positions.items()
        )
        total_eq = cash + invested_val
        equity_history[cur_date] = total_eq

    # Cerrar posiciones al final
    final_date = all_dates[-1]
    for sym, pos in list(open_positions.items()):
        ex_p = float(
            daily_data[sym][daily_data[sym]["_parsed_date"] == final_date]["close"].iloc[0]
        )
        pnl = (ex_p - pos["entry_price"]) * pos["shares"]
        pnl_pct = (ex_p - pos["entry_price"]) / pos["entry_price"]
        r_dist = pos["entry_price"] - pos["initial_stop"]
        pnl_r = pnl / (r_dist * pos["shares"]) if r_dist > 0 else 0.0
        cash += ex_p * pos["shares"]
        closed_trades.append(
            TradeRecord(
                symbol=sym,
                entry_date=pos["entry_date"],
                exit_date=final_date,
                entry_price=pos["entry_price"],
                exit_price=ex_p,
                shares=pos["shares"],
                pnl_usd=pnl,
                pnl_pct=pnl_pct,
                pnl_r=pnl_r,
                exit_reason="end_of_period",
            )
        )
    equity_history[final_date] = cash

    eq_series = pd.Series(equity_history)
    final_cap = float(eq_series.iloc[-1])
    total_ret = ((final_cap - initial_capital) / initial_capital) * 100.0

    spy_start = float(spy_df[spy_df["_parsed_date"] == all_dates[0]]["open"].iloc[0])
    spy_end = float(spy_df[spy_df["_parsed_date"] == all_dates[-1]]["close"].iloc[0])
    spy_ret = ((spy_end - spy_start) / spy_start) * 100.0
    alpha = total_ret - spy_ret

    max_dd_pct, _ = compute_drawdown(eq_series)
    sharpe = compute_sharpe(eq_series)

    wins = [t for t in closed_trades if t.pnl_usd > 0]
    losses = [t for t in closed_trades if t.pnl_usd <= 0]
    win_rate = (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0

    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in losses))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.0
    avg_days = (
        float(np.mean([(t.exit_date - t.entry_date).days for t in closed_trades]))
        if closed_trades
        else 0.0
    )

    sim_res = SimulationResult(
        config_name=config_name,
        initial_capital=initial_capital,
        final_capital=round(final_cap, 2),
        total_return_pct=round(total_ret, 2),
        alpha_vs_spy=round(alpha, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        total_trades=len(closed_trades),
        win_rate_pct=round(win_rate, 1),
        profit_factor=round(pf, 2),
        avg_holding_days=round(avg_days, 1),
        trades=closed_trades,
        equity_curve=eq_series,
    )

    return sim_res, veto_events


def main() -> int:
    print("=" * 95)
    print("           TEST DE ABLACIÓN INSTITUCIONAL: S5 CON FINBERT vs SIN FINBERT")
    print("        Verificación Cuantitativa de Contribución de Señales NLP (Auditoría v1.2.0)")
    print("=" * 95)

    # 1. Rutas de datos
    data_2025_dir = PROJECT_ROOT / "data" / "historical"
    news_2025_file = PROJECT_ROOT / "data" / "news_features" / "historical_news_features.csv"

    data_hist_dir = PROJECT_ROOT / "data" / "historical_2020_2022"
    news_hist_file = PROJECT_ROOT / "data" / "news_features" / "historical_news_features_2020_2022.csv"

    print("\n[1/3] Cargando datasets de mercado y noticias...")
    data_2025 = load_all_market_data(data_2025_dir, symbols=UNIVERSE_14)
    store_2025 = HistoricalNewsFeatureStore.from_file(news_2025_file)
    print(f" -> 2025: {len(data_2025)} activos, {len(store_2025._df)} noticias FinBERT.")

    data_hist = load_all_market_data(data_hist_dir, symbols=UNIVERSE_14)
    store_hist = HistoricalNewsFeatureStore.from_file(news_hist_file)
    print(f" -> 2020-2022: {len(data_hist)} activos, {len(store_hist._df)} noticias FinBERT.")

    # Definición de períodos de prueba
    test_periods = [
        {
            "id": "2025",
            "name": "Año 2025 Completo",
            "data": data_2025,
            "store": store_2025,
            "start": date(2025, 1, 2),
            "end": date(2025, 12, 31),
            "cash_yield": 0.045,
        },
        {
            "id": "2020",
            "name": "Año 2020 (Crash COVID + Rebote)",
            "data": data_hist,
            "store": store_hist,
            "start": date(2020, 1, 2),
            "end": date(2020, 12, 31),
            "cash_yield": 0.005,
        },
        {
            "id": "2021",
            "name": "Año 2021 (Mercado Alcista)",
            "data": data_hist,
            "store": store_hist,
            "start": date(2021, 1, 4),
            "end": date(2021, 12, 31),
            "cash_yield": 0.005,
        },
        {
            "id": "2022",
            "name": "Año 2022 (Mercado Bajista Severo)",
            "data": data_hist,
            "store": store_hist,
            "start": date(2022, 1, 3),
            "end": date(2022, 12, 30),
            "cash_yield": 0.025,
        },
        {
            "id": "2020-2022",
            "name": "Período 2020-2022 (3 Años Compuesto)",
            "data": data_hist,
            "store": store_hist,
            "start": date(2020, 1, 2),
            "end": date(2022, 12, 30),
            "cash_yield": 0.015,
        },
    ]

    ablation_results = []

    print("\n[2/3] Ejecutando comparativa emparejada (Con FinBERT vs Sin FinBERT)...")
    for p in test_periods:
        print(f"\n--- Evaluando {p['name']} ---")
        # Simulación CON FinBERT
        res_with, vetos = run_s5_ablation_simulation(
            daily_data=p["data"],
            start_date=p["start"],
            end_date=p["end"],
            config_name=f"{p['name']} [CON FinBERT]",
            initial_capital=2000.0,
            top_n_leaders=2,
            momentum_lookback_days=45,
            trailing_ema_period=25,
            use_finbert=True,
            finbert_store=p["store"],
            symbols=UNIVERSE_14,
            annual_cash_yield=p["cash_yield"],
        )

        # Simulación SIN FinBERT
        res_without, _ = run_s5_ablation_simulation(
            daily_data=p["data"],
            start_date=p["start"],
            end_date=p["end"],
            config_name=f"{p['name']} [SIN FinBERT]",
            initial_capital=2000.0,
            top_n_leaders=2,
            momentum_lookback_days=45,
            trailing_ema_period=25,
            use_finbert=False,
            finbert_store=None,
            symbols=UNIVERSE_14,
            annual_cash_yield=p["cash_yield"],
        )

        delta_ret = res_with.total_return_pct - res_without.total_return_pct
        delta_sharpe = res_with.sharpe_ratio - res_without.sharpe_ratio
        delta_maxdd = res_with.max_drawdown_pct - res_without.max_drawdown_pct

        ablation_results.append({
            "period": p["name"],
            "res_with": res_with,
            "res_without": res_without,
            "vetos": vetos,
            "delta_ret": delta_ret,
            "delta_sharpe": delta_sharpe,
            "delta_maxdd": delta_maxdd,
        })

        print(f"  [CON FinBERT] Retorno: {res_with.total_return_pct:+7.2f}% | Sharpe: {res_with.sharpe_ratio:4.2f} | MaxDD: {res_with.max_drawdown_pct:5.2f}% | Trades: {res_with.total_trades} | Vetos: {len(vetos)}")
        print(f"  [SIN FinBERT] Retorno: {res_without.total_return_pct:+7.2f}% | Sharpe: {res_without.sharpe_ratio:4.2f} | MaxDD: {res_without.max_drawdown_pct:5.2f}% | Trades: {res_without.total_trades}")
        print(f"  --> Delta FinBERT: Retorno: {delta_ret:+6.2f}% | Sharpe: {delta_sharpe:+4.2f} | MaxDD: {delta_maxdd:+5.2f}%")

        if vetos:
            print(f"  -> Detalle de Vetos ({len(vetos)} eventos):")
            for v in vetos[:5]:
                print(f"     * {v.date} | {v.symbol:<5} | Mom: {v.momentum_pct:+6.1f}% | NegShare: {v.negative_share:.2f} | Topics: {v.top_topics[:2]}")
            if len(vetos) > 5:
                print(f"     ... y {len(vetos) - 5} vetos más.")

    # 3. Resumen y Tabla Comparativa Institucional
    print("\n" + "=" * 105)
    print("                         TABLA CONSOLIDADA DEL TEST DE ABLACIÓN (S5)")
    print("=" * 105)
    print(f"{'Período':<32} | {'Métrica':<10} | {'CON FinBERT':<12} | {'SIN FinBERT':<12} | {'Impacto (Delta)':<15} | {'Veredicto':<12}")
    print("-" * 105)

    for item in ablation_results:
        p_name = item["period"]
        rw = item["res_with"]
        rwo = item["res_without"]
        v_count = len(item["vetos"])

        d_ret = item["delta_ret"]
        d_sh = item["delta_sharpe"]
        d_dd = item["delta_maxdd"]

        verdict = "MEJORA" if d_ret > 0.5 or (abs(d_ret) <= 0.5 and d_dd > 0.5) else ("NEUTRO" if abs(d_ret) <= 0.5 else "EMPEORA")

        print(f"{p_name:<32} | Retorno    | {rw.total_return_pct:+10.2f}% | {rwo.total_return_pct:+10.2f}% | {d_ret:+13.2f}% | {verdict:<12}")
        print(f"{'':<32} | Sharpe     | {rw.sharpe_ratio:10.2f}  | {rwo.sharpe_ratio:10.2f}  | {d_sh:+13.2f}  | Vetos: {v_count}")
        print(f"{'':<32} | MaxDD      | {rw.max_drawdown_pct:10.2f}% | {rwo.max_drawdown_pct:10.2f}% | {d_dd:+13.2f}% | WinRate: {rw.win_rate_pct:.1f}% vs {rwo.win_rate_pct:.1f}%")
        print(f"{'':<32} | Trades     | {rw.total_trades:10d}  | {rwo.total_trades:10d}  | {rw.total_trades - rwo.total_trades:+13d}  | PF: {rw.profit_factor:.2f} vs {rwo.profit_factor:.2f}")
        print("-" * 105)

    print("=" * 105)
    return 0


if __name__ == "__main__":
    sys.exit(main())
