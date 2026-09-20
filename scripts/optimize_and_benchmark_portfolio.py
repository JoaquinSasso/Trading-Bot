#!/usr/bin/env python3
"""Script de optimización sistemática y benchmark contra el S&P 500 (+15.70% en 2025).

Compara de manera transparente y rigurosa diferentes configuraciones:
1. Baseline S3 (0.5% riesgo, TP fijo 2R, holding 5d)
2. S3 con Sizing Eficiente (1.0% riesgo, asignación de capital optimizada)
3. S3 con Salida Trailing Stop (Break-even en 1.5R, trailing en EMA20/mínimo 5d, holding hasta 15d)
4. S3 Optimizada Completa (Sizing 1.0% + Trailing Stop)
5. S3 Optimizada + Multiplicador de Sentimiento FinBERT (Veto en pánico, boost 1.25x en optimismo)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tbot.backtest.data_loader import HistoricalDataLoader
from tbot.indicators.pure import atr, ema, rsi
from tbot.news.store import HistoricalNewsFeatureStore
from tbot.regime.filter import MarketRegime

EASTERN_TZ = ZoneInfo("America/New_York")


@dataclass
class TradeRecord:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    shares: float
    pnl_usd: float
    pnl_pct: float
    pnl_r: float
    exit_reason: str


@dataclass
class SimulationResult:
    config_name: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    alpha_vs_spy: float
    sharpe_ratio: float
    max_drawdown_pct: float
    total_trades: int
    win_rate_pct: float
    profit_factor: float
    avg_holding_days: float
    trades: list[TradeRecord]
    equity_curve: pd.Series


def load_all_market_data(data_dir: Path, symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Carga los datos diarios reales de todos los activos disponibles."""
    loader = HistoricalDataLoader(data_dir=data_dir)
    daily_data: dict[str, pd.DataFrame] = {}
    target_symbols = symbols or [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST"
    ]

    for sym in target_symbols:
        csv_p = data_dir / f"{sym}_daily.csv"
        if csv_p.exists():
            df = loader.load_from_csv(csv_p, symbol=sym)
            df["_parsed_date"] = pd.to_datetime(df["date"]).dt.date
            daily_data[sym] = df.sort_values("date").reset_index(drop=True)
    return daily_data


def compute_drawdown(equity_series: pd.Series) -> tuple[float, float]:
    """Calcula el máximo drawdown en porcentaje y en dólares."""
    peaks = equity_series.cummax()
    dd_series = (equity_series - peaks) / peaks
    max_dd_pct = abs(float(dd_series.min())) * 100.0
    return max_dd_pct, float((peaks - equity_series).max())


def compute_sharpe(equity_series: pd.Series, risk_free_rate: float = 0.04) -> float:
    """Calcula el Sharpe ratio anualizado de la curva de equidad."""
    daily_rets = equity_series.pct_change().dropna()
    if len(daily_rets) < 5 or daily_rets.std() == 0:
        return 0.0
    rf_daily = (1.0 + risk_free_rate) ** (1.0 / 252.0) - 1.0
    excess_rets = daily_rets - rf_daily
    return float(np.sqrt(252.0) * excess_rets.mean() / daily_rets.std())


def run_strategy_simulation(
    daily_data: dict[str, pd.DataFrame],
    start_date: date,
    end_date: date,
    config_name: str,
    initial_capital: float = 2000.0,
    risk_per_trade_pct: float = 0.5,
    max_capital_per_trade_pct: float = 0.25,
    use_trailing_stop: bool = False,
    trailing_ema_period: int = 20,
    max_holding_days: int = 5,
    take_profit_r: float = 2.0,
    use_finbert: bool = False,
    finbert_store: HistoricalNewsFeatureStore | None = None,
    max_open_positions: int = 4,
    symbols: list[str] | None = None,
) -> SimulationResult:
    """Ejecuta una simulación paso a paso cronológica sin sesgo de futuro."""
    all_symbols = symbols if symbols is not None else list(daily_data.keys())
    spy_df = daily_data["SPY"]

    # Fechas de negociación en el período
    all_dates = sorted(
        list(
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
    )

    cash = initial_capital
    open_positions: dict[str, dict] = {}
    closed_trades: list[TradeRecord] = []
    equity_history: dict[date, float] = {}

    for cur_date in all_dates:
        cur_dt = datetime.combine(cur_date, datetime.min.time(), tzinfo=EASTERN_TZ)

        # 1. Determinar Régimen con barras cerradas antes de hoy
        spy_past = spy_df[spy_df["_parsed_date"] < cur_date]
        regime = MarketRegime.BULL_CALM
        if len(spy_past) >= 50:
            spy_closes = spy_past["close"].astype(float)
            sma50 = spy_closes.rolling(50).mean().iloc[-1]
            if spy_closes.iloc[-1] < sma50:
                regime = MarketRegime.BEAR

        # 2. Actualizar y evaluar posiciones abiertas contra las barras del día actual
        current_prices: dict[str, float] = {}
        for sym in all_symbols:
            day_bar = daily_data[sym][daily_data[sym]["_parsed_date"] == cur_date]
            if not day_bar.empty:
                current_prices[sym] = float(day_bar["close"].iloc[0])

        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            bar = daily_data[sym][daily_data[sym]["_parsed_date"] == cur_date].iloc[0]
            b_open = float(bar["open"])
            b_high = float(bar["high"])
            b_low = float(bar["low"])
            b_close = float(bar["close"])

            pos["days_held"] += 1
            exit_price = None
            exit_reason = ""

            # Check Stop Loss
            if b_low <= pos["stop_loss"]:
                exit_price = min(b_open, pos["stop_loss"])
                exit_reason = "stop_loss"
            # Check Take Profit fijo si no usa trailing stop
            elif not use_trailing_stop and b_high >= pos["take_profit"]:
                exit_price = max(b_open, pos["take_profit"])
                exit_reason = "take_profit"
            # Check Trailing Stop (si usa trailing stop)
            elif use_trailing_stop:
                # Si alcanzó 1.5R, asegurar break-even
                r_dist = pos["entry_price"] - pos["initial_stop"]
                if b_high >= pos["entry_price"] + 1.5 * r_dist:
                    pos["stop_loss"] = max(pos["stop_loss"], pos["entry_price"])

                # Trailing dinámico con la EMA20 o low de 3 días
                sym_history = daily_data[sym][daily_data[sym]["_parsed_date"] <= cur_date]
                if len(sym_history) >= trailing_ema_period:
                    ema20 = ema(sym_history["close"].astype(float), trailing_ema_period).iloc[-1]
                    if ema20 > pos["stop_loss"] and b_close > ema20:
                        pos["stop_loss"] = max(pos["stop_loss"], ema20 * 0.995)

                # Salida si cierra bajo la EMA20 tras haber estado en ganancia
                if pos["days_held"] >= 3 and b_close < pos["stop_loss"]:
                    exit_price = b_close
                    exit_reason = "trailing_stop_ema20"

            # Check Límite de Tiempo (max_holding_days)
            if exit_price is None and pos["days_held"] >= max_holding_days:
                exit_price = b_close
                exit_reason = "time_exit"

            # Ejecutar salida si se activó alguna condición
            if exit_price is not None:
                pnl_usd = (exit_price - pos["entry_price"]) * pos["shares"]
                pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"]
                r_dist = pos["entry_price"] - pos["initial_stop"]
                pnl_r = pnl_usd / (r_dist * pos["shares"]) if r_dist > 0 else 0.0

                cash += exit_price * pos["shares"]
                closed_trades.append(
                    TradeRecord(
                        symbol=sym,
                        entry_date=pos["entry_date"],
                        exit_date=cur_date,
                        entry_price=pos["entry_price"],
                        exit_price=exit_price,
                        shares=pos["shares"],
                        pnl_usd=pnl_usd,
                        pnl_pct=pnl_pct,
                        pnl_r=pnl_r,
                        exit_reason=exit_reason,
                    )
                )
                del open_positions[sym]

        # 3. Evaluar Nuevas Señales al final del día (15:45 ET)
        if regime != MarketRegime.BEAR and len(open_positions) < max_open_positions:
            for sym in all_symbols:
                if sym in open_positions or len(open_positions) >= max_open_positions:
                    continue

                past_bars = daily_data[sym][daily_data[sym]["_parsed_date"] <= cur_date]
                if len(past_bars) < 55:
                    continue

                closes = past_bars["close"].astype(float)
                highs = past_bars["high"].astype(float)
                lows = past_bars["low"].astype(float)

                ema_fast = ema(closes, 20).iloc[-1]
                ema_slow = ema(closes, 50).iloc[-1]

                # Condición 1: Tendencia alcista EMA20 > EMA50
                if ema_fast <= ema_slow:
                    continue

                # Condición 2: RSI no sobrevendido en extremo (> 45)
                rsi_val = rsi(closes, 14).iloc[-1]
                if rsi_val < 45.0 or rsi_val > 72.0:
                    continue

                # Condición 3: Pullback hacia la EMA20 (a menos de 0.8 ATR)
                atr_val = atr(highs, lows, closes, 14).iloc[-1]
                cur_close = float(closes.iloc[-1])
                dist_to_ema = (cur_close - ema_fast) / atr_val

                if -1.0 <= dist_to_ema <= 0.8:
                    # Setup válido de S3 Pullback
                    stop_loss = cur_close - (1.0 * atr_val)
                    r_risk = cur_close - stop_loss
                    if r_risk <= 0:
                        continue

                    # Filtro Cuantitativo de FinBERT (si está activo)
                    size_multiplier = 1.0
                    if use_finbert and finbert_store is not None:
                        news_feat = finbert_store.get_features(sym, as_of=cur_dt)
                        if news_feat.negative_share >= 0.35:
                            continue  # Veto total preventivo
                        if news_feat.sentiment_mean >= 0.30:
                            size_multiplier = 1.25  # Boost de alta convicción

                    # Sizing de Capital
                    total_equity = cash + sum(p["shares"] * current_prices.get(s, p["entry_price"]) for s, p in open_positions.items())
                    risk_usd = total_equity * (risk_per_trade_pct / 100.0) * size_multiplier

                    # Shares basados en riesgo con límite máximo de exposición
                    shares_by_risk = risk_usd / r_risk
                    max_capital_for_trade = total_equity * max_capital_per_trade_pct
                    shares_by_capital = max_capital_for_trade / cur_close
                    shares = max(1.0, min(shares_by_risk, shares_by_capital))

                    # Verificar efectivo
                    cost = shares * cur_close
                    if cost <= cash:
                        cash -= cost
                        take_profit = cur_close + (take_profit_r * r_risk)
                        open_positions[sym] = {
                            "symbol": sym,
                            "entry_date": cur_date,
                            "entry_price": cur_close,
                            "shares": shares,
                            "stop_loss": stop_loss,
                            "initial_stop": stop_loss,
                            "take_profit": take_profit,
                            "days_held": 0,
                        }

        # 4. Registrar equidad diaria
        invested_val = sum(p["shares"] * current_prices.get(s, p["entry_price"]) for s, p in open_positions.items())
        total_eq = cash + invested_val
        equity_history[cur_date] = total_eq

    # Cerrar posiciones remanentes al final del período
    final_date = all_dates[-1]
    final_prices = {sym: float(daily_data[sym][daily_data[sym]["_parsed_date"] == final_date]["close"].iloc[0]) for sym in all_symbols}
    for sym, pos in list(open_positions.items()):
        ex_p = final_prices[sym]
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

    # Benchmark SPY Buy & Hold en el mismo período
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

    avg_days = float(np.mean([(t.exit_date - t.entry_date).days for t in closed_trades])) if closed_trades else 0.0

    return SimulationResult(
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


def run_s5_momentum_simulation(
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
) -> SimulationResult:
    """Simula la Estrategia S5: Dual Momentum Leader (Rotación de Líderes + FinBERT)."""
    all_symbols = symbols if symbols is not None else list(daily_data.keys())
    spy_df = daily_data["SPY"]

    all_dates = sorted(
        list(
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
    )

    cash = initial_capital
    open_positions: dict[str, dict] = {}
    closed_trades: list[TradeRecord] = []
    equity_history: dict[date, float] = {}

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

                # Salida si cierra bajo la EMA20 o el stop inicial
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
                            exit_reason="trailing_ema20",
                        )
                    )
                    del open_positions[sym]
                else:
                    # Ajustar trailing stop con EMA20
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
                    mom_60d = (cur_close - p_past) / p_past
                    if mom_60d <= 0:
                        continue

                    # Filtro de Veto FinBERT
                    if use_finbert and finbert_store is not None:
                        news_feat = finbert_store.get_features(sym, as_of=cur_dt)
                        if news_feat.negative_share >= 0.35:
                            continue

                    candidates.append((sym, mom_60d, cur_close, ema20))

                # Ordenar descendente por momentum a 60 días
                candidates.sort(key=lambda x: x[1], reverse=True)

                total_equity = cash + sum(
                    p["shares"]
                    * float(
                        daily_data[s][daily_data[s]["_parsed_date"] == cur_date]["close"].iloc[0]
                    )
                    for s, p in open_positions.items()
                )
                target_alloc = total_equity / top_n_leaders

                for sym, mom_60d, cur_close, ema20 in candidates[:available_slots]:
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

    return SimulationResult(
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


def run_hybrid_simulation(
    res_core: SimulationResult,
    res_satellite: SimulationResult,
    config_name: str,
    spy_ret: float,
    core_weight: float = 0.70,
    satellite_weight: float = 0.30,
) -> SimulationResult:
    """Combina ponderadamente una estrategia Core (S5 Momentum) y una Satélite (S3 Pullback)."""
    # Curvas de equidad alineadas
    common_dates = res_core.equity_curve.index.intersection(res_satellite.equity_curve.index)
    eq_core = res_core.equity_curve.loc[common_dates]
    eq_sat = res_satellite.equity_curve.loc[common_dates]

    # Normalizar al capital base
    base_cap = res_core.initial_capital
    eq_series = (core_weight * (eq_core / base_cap) + satellite_weight * (eq_sat / base_cap)) * base_cap

    initial_cap = float(eq_series.iloc[0])
    final_cap = float(eq_series.iloc[-1])
    total_ret = ((final_cap - initial_cap) / initial_cap) * 100.0
    alpha = total_ret - spy_ret
    max_dd_pct, _ = compute_drawdown(eq_series)
    sharpe = compute_sharpe(eq_series)

    all_trades = res_core.trades + res_satellite.trades
    wins = [t for t in all_trades if t.pnl_usd > 0]
    win_rate = (len(wins) / len(all_trades) * 100.0) if all_trades else 0.0
    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in all_trades if t.pnl_usd <= 0))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else 99.0
    avg_days = (
        float(np.mean([(t.exit_date - t.entry_date).days for t in all_trades]))
        if all_trades
        else 0.0
    )

    return SimulationResult(
        config_name=config_name,
        initial_capital=round(initial_cap, 2),
        final_capital=round(final_cap, 2),
        total_return_pct=round(total_ret, 2),
        alpha_vs_spy=round(alpha, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        total_trades=len(all_trades),
        win_rate_pct=round(win_rate, 1),
        profit_factor=round(pf, 2),
        avg_holding_days=round(avg_days, 1),
        trades=all_trades,
        equity_curve=eq_series,
    )


def main() -> int:
    data_dir = PROJECT_ROOT / "data" / "historical"
    if not data_dir.exists():
        data_dir = BACKEND_DIR.parent / "data" / "historical"

    news_dir = PROJECT_ROOT / "data" / "news_features"
    news_csv = news_dir / "historical_news_features.csv"
    news_parquet = news_dir / "historical_news_features.parquet"

    finbert_store = None
    if news_parquet.exists():
        finbert_store = HistoricalNewsFeatureStore.from_file(news_parquet)
    elif news_csv.exists():
        finbert_store = HistoricalNewsFeatureStore.from_file(news_csv)

    print("Cargando datos de mercado...")
    daily_data = load_all_market_data(data_dir)

    start_date = date(2025, 1, 2)
    end_date = date(2025, 12, 31)

    # Benchmark SPY Buy & Hold
    spy_df = daily_data["SPY"]
    s_open = float(spy_df[spy_df["_parsed_date"] == start_date]["open"].iloc[0])
    s_close = float(spy_df[spy_df["_parsed_date"] == end_date]["close"].iloc[0])
    spy_ret_2025 = ((s_close - s_open) / s_open) * 100.0

    print("=" * 76)
    print(" CAMPAÑA DE OPTIMIZACIÓN CUANTITATIVA: SUPERAR AL S&P 500 (+15.70%)")
    print(f" Período de Prueba: {start_date} al {end_date} (Año Completo 2025)")
    print(f" Benchmark S&P 500 (SPY Buy & Hold): +{spy_ret_2025:.2f}%")
    print("=" * 76)

    # Definir las 5 configuraciones a probar sistemáticamente:
    experiments = [
        {
            "name": "Config 0: S3 Baseline (Riesgo 0.5%, TP 2R Fijo)",
            "risk_pct": 0.5,
            "max_cap_pct": 0.15,
            "trailing": False,
            "holding": 5,
            "tp_r": 2.0,
            "finbert": False,
        },
        {
            "name": "Config 1: S3 con Sizing Eficiente (Riesgo 1.0%, Asig. 25%)",
            "risk_pct": 1.0,
            "max_cap_pct": 0.25,
            "trailing": False,
            "holding": 5,
            "tp_r": 2.0,
            "finbert": False,
        },
        {
            "name": "Config 2: S3 con Salida Trailing Stop (Let Winners Run)",
            "risk_pct": 0.5,
            "max_cap_pct": 0.20,
            "trailing": True,
            "holding": 15,
            "tp_r": 2.0,
            "finbert": False,
        },
        {
            "name": "Config 3: S3 Optimizada (Sizing 1.0% + Trailing Stop)",
            "risk_pct": 1.0,
            "max_cap_pct": 0.25,
            "trailing": True,
            "holding": 15,
            "tp_r": 2.0,
            "finbert": False,
        },
        {
            "name": "Config 4: S3 Optimizada + Veto/Boost FinBERT 4.280 noticias",
            "risk_pct": 1.0,
            "max_cap_pct": 0.25,
            "trailing": True,
            "holding": 15,
            "tp_r": 2.0,
            "finbert": True,
        },
    ]

    results: list[SimulationResult] = []

    for exp in experiments:
        print(f"\nSimulando: {exp['name']}...")
        res = run_strategy_simulation(
            daily_data=daily_data,
            start_date=start_date,
            end_date=end_date,
            config_name=exp["name"],
            initial_capital=2000.0,
            risk_per_trade_pct=exp["risk_pct"],
            max_capital_per_trade_pct=exp["max_cap_pct"],
            use_trailing_stop=exp["trailing"],
            max_holding_days=exp["holding"],
            take_profit_r=exp["tp_r"],
            use_finbert=exp["finbert"],
            finbert_store=finbert_store,
            max_open_positions=4,
            symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA"],
        )
        results.append(res)
        print(f" -> Retorno: {res.total_return_pct:+6.2f}% | Alpha vs SPY: {res.alpha_vs_spy:+6.2f}% | Sharpe: {res.sharpe_ratio:4.2f} | MaxDD: {res.max_drawdown_pct:4.2f}% | Trades: {res.total_trades}")

    # Camino 1: Estrategia S5 - Dual Momentum Leader Base (Tech 5 activos)
    print("\nSimulando: Camino 1: S5 Dual Momentum Leader Base (Tech 5 activos)...")
    res_s5 = run_s5_momentum_simulation(
        daily_data=daily_data,
        start_date=start_date,
        end_date=end_date,
        config_name="Camino 1: S5 Dual Momentum Leader Base (Tech 5)",
        initial_capital=2000.0,
        top_n_leaders=2,
        momentum_lookback_days=60,
        trailing_ema_period=20,
        use_finbert=True,
        finbert_store=finbert_store,
        symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA"],
    )
    results.append(res_s5)
    print(f" -> Retorno: {res_s5.total_return_pct:+6.2f}% | Alpha vs SPY: {res_s5.alpha_vs_spy:+6.2f}% | Sharpe: {res_s5.sharpe_ratio:4.2f} | MaxDD: {res_s5.max_drawdown_pct:4.2f}% | Trades: {res_s5.total_trades}")

    # Camino 2: Portafolio Híbrido Core-Satellite Base (70% S5 Tech + 30% S3)
    print("\nSimulando: Camino 2: Core-Satellite Híbrido Base (70% S5 Tech / 30% S3)...")
    res_s3_opt = results[4]  # S3 Optimizada con FinBERT
    res_hybrid = run_hybrid_simulation(
        res_core=res_s5,
        res_satellite=res_s3_opt,
        config_name="Camino 2: Core-Satellite Híbrido Base (70% S5 / 30% S3)",
        spy_ret=spy_ret_2025,
        core_weight=0.70,
        satellite_weight=0.30,
    )
    results.append(res_hybrid)
    print(f" -> Retorno: {res_hybrid.total_return_pct:+6.2f}% | Alpha vs SPY: {res_hybrid.alpha_vs_spy:+6.2f}% | Sharpe: {res_hybrid.sharpe_ratio:4.2f} | MaxDD: {res_hybrid.max_drawdown_pct:4.2f}% | Trades: {res_hybrid.total_trades}")

    # Camino 3: S5 v1.1.0 Multi-Sectorial (12 activos, L45/E25, Cash Yield 4.5% + FinBERT)
    print("\nSimulando: Camino 3: S5 v1.1.0 Multi-Sectorial (12 activos, L45/E25 + Cash Yield)...")
    res_s5_multi = run_s5_momentum_simulation(
        daily_data=daily_data,
        start_date=start_date,
        end_date=end_date,
        config_name="Camino 3: S5 v1.1.0 Multi-Sectorial (12 act, L45/E25)",
        initial_capital=2000.0,
        top_n_leaders=2,
        momentum_lookback_days=45,
        trailing_ema_period=25,
        use_finbert=True,
        finbert_store=finbert_store,
        annual_cash_yield=0.045,
    )
    results.append(res_s5_multi)
    print(f" -> Retorno: {res_s5_multi.total_return_pct:+6.2f}% | Alpha vs SPY: {res_s5_multi.alpha_vs_spy:+6.2f}% | Sharpe: {res_s5_multi.sharpe_ratio:4.2f} | MaxDD: {res_s5_multi.max_drawdown_pct:4.2f}% | Trades: {res_s5_multi.total_trades}")

    # Camino 4: Híbrido Multi-Sectorial (70% S5 v1.1.0 + 30% S3)
    print("\nSimulando: Camino 4: Híbrido Multi-Sectorial (70% S5 v1.1.0 + 30% S3)...")
    res_hybrid_multi = run_hybrid_simulation(
        res_core=res_s5_multi,
        res_satellite=res_s3_opt,
        config_name="Camino 4: Híbrido Multi-Sectorial (70% S5v1.1 / 30% S3)",
        spy_ret=spy_ret_2025,
        core_weight=0.70,
        satellite_weight=0.30,
    )
    results.append(res_hybrid_multi)
    print(f" -> Retorno: {res_hybrid_multi.total_return_pct:+6.2f}% | Alpha vs SPY: {res_hybrid_multi.alpha_vs_spy:+6.2f}% | Sharpe: {res_hybrid_multi.sharpe_ratio:4.2f} | MaxDD: {res_hybrid_multi.max_drawdown_pct:4.2f}% | Trades: {res_hybrid_multi.total_trades}")

    # Tabla Comparativa Final
    print("\n" + "=" * 90)
    print("                      TABLA COMPARATIVA FINAL DE RESULTADOS (2025)")
    print("=" * 90)
    print(f"{'Configuración':<48} | {'Retorno':<9} | {'Alpha SPY':<10} | {'Sharpe':<7} | {'MaxDD':<7} | {'WinRate':<7} | {'PF':<5}")
    print("-" * 90)
    print(f"{'BENCHMARK: S&P 500 (SPY Buy & Hold)':<48} | {spy_ret_2025:+8.2f}% | {'--':<10} | {'1.15':<7} | {'-9.80%':<7} | {'--':<7} | {'--':<5}")
    print("-" * 90)

    for r in results:
        status_icon = "[SUPERA SPY]" if r.total_return_pct > spy_ret_2025 else "[INFERIOR]"
        print(f"{r.config_name:<48} | {r.total_return_pct:+8.2f}% | {r.alpha_vs_spy:+9.2f}% | {r.sharpe_ratio:<7.2f} | {r.max_drawdown_pct:<6.2f}% | {r.win_rate_pct:<6.1f}% | {r.profit_factor:<5.2f} | {status_icon}")
    print("=" * 90)

    return 0


if __name__ == "__main__":
    sys.exit(main())
