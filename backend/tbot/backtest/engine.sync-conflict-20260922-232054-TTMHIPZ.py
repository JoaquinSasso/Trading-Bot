"""Motor de Replay determinista para backtesting offline.

Itera cronológicamente sobre las barras históricas sin sesgo de anticipación (no lookahead),
inyecta el SimulatedClock, evalúa estrategias, aplica reglas de sizing y gestiona la ejecución
a través del SimulatedBroker.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

import pandas as pd
import pytz

from tbot.backtest.metrics import BacktestMetrics, compute_backtest_metrics
from tbot.backtest.simulated_broker import SimulatedBroker, SimulatedTrade
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import Strategy, StrategyContext


class ReplayEngine:
    """Motor de simulación y replay barra a barra."""

    def __init__(
        self,
        strategy: Strategy,
        historical_daily: dict[str, pd.DataFrame],
        historical_intraday: dict[str, pd.DataFrame] | None = None,
        initial_capital: Decimal = Decimal("2000.00"),
        risk_per_trade_pct: float = 0.5,
        max_open_positions: int = 4,
        num_tested_trials: int = 1,
    ) -> None:
        self.strategy = strategy
        self.daily_data = {k: v.copy() for k, v in historical_daily.items()}
        self.intraday_data = (
            {k: v.copy() for k, v in historical_intraday.items()} if historical_intraday else {}
        )
        self.initial_capital = initial_capital
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_open_positions = max_open_positions
        self.num_tested_trials = num_tested_trials

        self.broker = SimulatedBroker(initial_capital=initial_capital)
        self.ny_tz = pytz.timezone("America/New_York")

        # Pre-parsear columnas temporales una sola vez para máxima velocidad
        for df in self.daily_data.values():
            d_col = "date" if "date" in df.columns else "timestamp"
            df["_parsed_date"] = pd.to_datetime(df[d_col]).dt.date

        for df in self.intraday_data.values():
            i_col = "timestamp" if "timestamp" in df.columns else "date"
            df["_parsed_ts"] = pd.to_datetime(df[i_col])
            df["_parsed_date"] = df["_parsed_ts"].dt.date

    def _determine_regime(self, spy_df: pd.DataFrame, current_date: date) -> MarketRegime:
        """Determina el régimen de mercado a partir de barras previas de SPY."""
        if spy_df.empty:
            return MarketRegime.BULL_CALM
        # Filtrar barras cerradas estrictamente anteriores al día actual
        if "_parsed_date" in spy_df.columns:
            history = spy_df[spy_df["_parsed_date"] < current_date]
        else:
            date_col = "date" if "date" in spy_df.columns else "timestamp"
            history = spy_df[pd.to_datetime(spy_df[date_col]).dt.date < current_date]
        if len(history) < 200:
            return MarketRegime.BULL_CALM  # Para backtests con historial acotado

        closes = history["close"].astype(float)
        sma200 = float(closes.rolling(200).mean().iloc[-1])
        cur_c = float(closes.iloc[-1])

        if cur_c <= sma200:
            return MarketRegime.BEAR

        # Volatilidad realizada 20d
        rets = closes.pct_change().dropna()
        if len(rets) >= 20:
            vol_20d = float(rets.iloc[-20:].std() * (252.0**0.5))
            # Aproximación del percentil 70
            if vol_20d > 0.22:
                return MarketRegime.BULL_VOLATILE
        return MarketRegime.BULL_CALM

    def run(
        self, start_date: date, end_date: date
    ) -> tuple[BacktestMetrics, list[SimulatedTrade], pd.Series]:
        """Ejecuta el replay cronológico desde start_date hasta end_date."""
        # Obtener lista de símbolos y fechas únicas
        all_symbols = list(self.daily_data.keys())
        spy_df = self.daily_data.get("SPY", pd.DataFrame())

        # Encontrar todos los días hábiles presentes en los datos
        all_dates_set: set[date] = set()
        for df in self.daily_data.values():
            d_col = "date" if "date" in df.columns else "timestamp"
            all_dates_set.update(pd.to_datetime(df[d_col]).dt.date)

        trading_days = sorted([d for d in all_dates_set if start_date <= d <= end_date])
        equity_records: dict[date, float] = {}

        # Determinar horario de evaluación según la estrategia
        # Default a 15:30 para S1 o 15:45 para S2/S3
        eval_time = time(15, 30) if self.strategy.id == "intraday_momentum" else time(15, 45)

        for current_day in trading_days:
            eval_dt = datetime.combine(current_day, eval_time)
            regime = self._determine_regime(spy_df, current_day)

            # 1. Preparar datos sin mirar al futuro (No Lookahead)
            sliced_daily: dict[str, pd.DataFrame] = {}
            sliced_intraday: dict[str, pd.DataFrame] = {}
            current_prices: dict[str, Decimal] = {}

            for sym in all_symbols:
                d_df = self.daily_data[sym]
                # Solo barras cerradas antes o hasta el día actual
                d_past = d_df[d_df["_parsed_date"] <= current_day]
                if not d_past.empty:
                    sliced_daily[sym] = d_past
                    cur_p = Decimal(str(round(float(d_past["close"].iloc[-1]), 4)))
                    current_prices[sym] = cur_p

                if sym in self.intraday_data:
                    i_df = self.intraday_data[sym]
                    # Barras intradía estrictamente <= eval_dt
                    i_past = i_df[i_df["_parsed_ts"] <= eval_dt]
                    if not i_past.empty:
                        sliced_intraday[sym] = i_past
                        cur_p = Decimal(str(round(float(i_past["close"].iloc[-1]), 4)))
                        current_prices[sym] = cur_p

            # 2. Evaluar posiciones abiertas antes de nuevas señales (stops intradiarios)
            for sym, _pos in list(self.broker.positions.items()):
                if sym in self.intraday_data:
                    day_intra = self.intraday_data[sym]
                    t_col = "timestamp" if "timestamp" in day_intra.columns else "date"
                    bars = day_intra[
                        (day_intra["_parsed_date"] == current_day)
                        & (day_intra["_parsed_ts"] <= eval_dt)
                    ]
                    for _, b in bars.iterrows():
                        self.broker.evaluate_bar(
                            symbol=sym,
                            bar_open=Decimal(str(b["open"])),
                            bar_high=Decimal(str(b["high"])),
                            bar_low=Decimal(str(b["low"])),
                            bar_close=Decimal(str(b["close"])),
                            timestamp=b[t_col],
                            is_market_close=False,
                        )
                        if sym not in self.broker.positions:
                            break

            # 3. Crear StrategyContext
            ctx = StrategyContext(
                now=eval_dt,
                regime=regime,
                daily_bars=sliced_daily,
                intraday_bars=sliced_intraday,
                current_prices=current_prices,
                portfolio_positions=set(self.broker.positions.keys()),
            )

            # 4. Generar señales
            signals = self.strategy.generate(ctx)

            # 5. Ejecutar señales aprobadas según RiskGate
            for sig in signals:
                if len(self.broker.positions) >= self.max_open_positions:
                    break
                if sig.symbol in self.broker.positions:
                    continue

                equity = self.broker.get_equity(current_prices)
                risk_usd = Decimal(str(self.risk_per_trade_pct / 100.0)) * equity
                risk_per_unit = sig.entry_price_ref - sig.stop_price

                if risk_per_unit <= Decimal("0.0"):
                    continue

                qty = Decimal(int(risk_usd / risk_per_unit))
                if qty < Decimal("1"):
                    qty = Decimal("1")  # Mínimo 1 acción entera

                # Buscar precio de apertura de la siguiente barra o usar entry_price_ref
                entry_fill = sig.entry_price_ref
                self.broker.submit_buy(sig, qty, entry_fill, eval_dt)

            # 6. Evaluar barras restantes hasta el cierre de la sesión (16:00 ET)
            close_dt = datetime.combine(current_day, time(16, 0))
            for sym, pos in list(self.broker.positions.items()):
                if sym in self.intraday_data:
                    day_intra = self.intraday_data[sym]
                    t_col = "timestamp" if "timestamp" in day_intra.columns else "date"
                    late_bars = day_intra[
                        (day_intra["_parsed_date"] == current_day)
                        & (day_intra["_parsed_ts"] > eval_dt)
                        & (day_intra["_parsed_ts"] <= close_dt)
                    ]
                    for idx, (_, b) in enumerate(late_bars.iterrows()):
                        is_close = (idx == len(late_bars) - 1) or (
                            pos.exit_at_close and idx >= len(late_bars) - 2
                        )
                        self.broker.evaluate_bar(
                            symbol=sym,
                            bar_open=Decimal(str(b["open"])),
                            bar_high=Decimal(str(b["high"])),
                            bar_low=Decimal(str(b["low"])),
                            bar_close=Decimal(str(b["close"])),
                            timestamp=b[t_col],
                            is_market_close=is_close,
                        )
                        if sym not in self.broker.positions:
                            break
                else:
                    # En datos diarios puros, evaluar contra barra diaria del día
                    d_row = self.daily_data[sym][
                        self.daily_data[sym]["_parsed_date"] == current_day
                    ]
                    if not d_row.empty:
                        r = d_row.iloc[0]
                        self.broker.evaluate_bar(
                            symbol=sym,
                            bar_open=Decimal(str(r["open"])),
                            bar_high=Decimal(str(r["high"])),
                            bar_low=Decimal(str(r["low"])),
                            bar_close=Decimal(str(r["close"])),
                            timestamp=close_dt,
                            is_market_close=pos.exit_at_close,
                        )

            # 7. Registrar equidad al cierre del día
            day_equity = float(self.broker.get_equity(current_prices))
            equity_records[current_day] = day_equity

        equity_series = pd.Series(equity_records)
        equity_series.index = pd.to_datetime(equity_series.index)

        metrics = compute_backtest_metrics(
            strategy_id=self.strategy.id,
            trades=self.broker.closed_trades,
            equity_curve=equity_series,
            initial_capital=float(self.initial_capital),
            num_tested_trials=self.num_tested_trials,
        )

        return metrics, self.broker.closed_trades, equity_series
