"""Estrategia S5: Dual Momentum Leader (rotación de líderes con filtro de régimen y trailing EMA25).

Estrategia cuantitativa de momentum transversal y preservación de capital:
1. Filtro macro / régimen: solo abre posiciones en BULL_CALM o BULL_VOLATILE (régimen del motor:
   SPY vs SMA200 con las barras previas). Con `exit_on_bear=True` liquida las posiciones cuando el
   régimen pasa a BEAR y queda en efectivo remunerado (o en `refuge_asset` si se configura).
2. Momentum transversal: retorno a `momentum_lookback_days` sesiones; califican los activos con
   momentum > 0 que cotizan sobre su EMA(25). Se mantienen los `top_n_leaders` mejores.
3. Rotación por ranking: una posición se vende cuando cae por debajo del puesto
   `top_n_leaders + rotation_buffer` (o deja de calificar) tras `rotation_min_hold_sessions`,
   y el lugar lo ocupa el nuevo líder.
4. Trailing stop anclado a la EMA(25) con buffer del 3.5% y salida si el cierre pierde la EMA
   tras 3 sesiones.
5. Límite de `max_holding_sessions`: al cumplirse, si el activo sigue en el top N se renueva
   (equivale a vender y recomprar sin costos); si no, se vende.
6. Tamaño: peso fijo 1/top_n (`target_weight`), luego control de volatilidad del motor.

Decisión D-01: el veto FinBERT fue retirado del camino crítico; la decisión es 100% determinista.

Rendimiento: `prepare()` precalcula la EMA de cada activo una sola vez. Como la EMA (adjust=False)
es causal, el valor precalculado en la fila i es idéntico al de recalcular sobre el prefijo [0..i].
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from tbot.indicators.pure import ema
from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import (
    PositionAction,
    Signal,
    StrategyContext,
    StrategyDataRequirements,
    compute_signal_id,
)

# Diferencia máxima entre el precio actual y el cierre de la última barra para considerarlos iguales
# (el motor redondea los precios a 4 decimales).
_PRICE_TOL = 5e-5


class DualMomentumLeaderStrategy:
    """Implementación canónica de S5 - Dual Momentum Leader."""

    id: str = "dual_momentum_leader"
    version: str = "1.3.0"
    schedule: list[str] = ["15:45 America/New_York"]
    allowed_regimes: set[MarketRegime] = {
        MarketRegime.BULL_CALM,
        MarketRegime.BULL_VOLATILE,
    }
    allows_open_window: bool = False

    # Universo Multi-Sectorial y Metales Preciosos Oficial (Tech, Finanzas, Salud, Energía, Consumo y Metales)
    DEFAULT_UNIVERSE: list[str] = [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "JPM", "LLY", "XOM", "COST", "GLD", "SLV"
    ]

    data_requirements: StrategyDataRequirements = StrategyDataRequirements(
        needs_daily_bars=True,
        daily_lookback_days=75,  # 45 sesiones de momentum + 25 sesiones de EMA con margen
        needs_intraday_bars=False,
        requires_sip_delayed=True,
    )

    def __init__(
        self,
        momentum_lookback_days: int = 45,
        top_n_leaders: int = 2,
        trailing_ema_period: int = 25,
        stop_buffer_pct: float = 0.035,  # Buffer unificado al 3.5%
        max_holding_sessions: int = 30,  # 30 sesiones bursátiles (se renueva si sigue siendo líder)
        universe: list[str] | None = None,
        max_holding_days: int | None = None,  # Compatibilidad hacia atrás
        refuge_asset: str | None = None,
        rotation_buffer: int = 2,
        rotation_min_hold_sessions: int = 3,
        exit_on_bear: bool = True,
        equal_weight: bool = True,
        renew_on_max_hold: bool = True,
    ) -> None:
        self.refuge_asset = refuge_asset
        self.universe = list(universe) if universe is not None else list(self.DEFAULT_UNIVERSE)
        self.momentum_lookback_days = momentum_lookback_days
        self.top_n_leaders = top_n_leaders
        self.trailing_ema_period = trailing_ema_period
        self.stop_buffer_pct = stop_buffer_pct
        self.max_holding_sessions = (
            max_holding_sessions if max_holding_days is None else max_holding_days
        )
        self.max_holding_days = self.max_holding_sessions
        self.rotation_buffer = rotation_buffer
        self.rotation_min_hold_sessions = rotation_min_hold_sessions
        self.exit_on_bear = exit_on_bear
        self.equal_weight = equal_weight
        self.renew_on_max_hold = renew_on_max_hold

        # Precálculo opcional (backtest): symbol -> (close, ema)
        self._pre: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._rank_cache: tuple[Any, Any, Any, list[tuple[str, float, float, float]]] | None = None

    # ------------------------------------------------------------------ precálculo
    def prepare(self, daily_data: dict[str, pd.DataFrame]) -> None:
        """Precalcula cierres y EMA de cada activo del universo (y del refugio)."""
        self._pre = {}
        self._rank_cache = None
        wanted = set(self.universe) | ({self.refuge_asset} if self.refuge_asset else set())
        for sym in wanted:
            df = daily_data.get(sym)
            if df is None or df.empty or "close" not in df.columns:
                continue
            closes = df["close"].to_numpy(dtype=float)
            ema_arr = ema(pd.Series(closes), self.trailing_ema_period).to_numpy(dtype=float)
            self._pre[sym] = (closes, ema_arr)

    # ------------------------------------------------------------------ cálculo base
    def _close_and_ema(self, ctx: StrategyContext, symbol: str) -> tuple[pd.Series | None, float | None, int]:
        """Devuelve (cierres, EMA actual, cantidad de barras) para el símbolo a la fecha del contexto.

        Camino rápido: usa el precálculo si la vista del motor expone la fila y el precio actual
        coincide con el cierre de esa fila. Camino general: recalcula sobre las barras del contexto.
        El primer elemento solo se devuelve en el camino general (None en el rápido).
        """
        current_price = ctx.current_prices.get(symbol)
        if current_price is None:
            return None, None, 0
        cur = float(current_price)
        bars = ctx.daily_bars
        pre = self._pre.get(symbol)
        row_index = getattr(bars, "row_index", None)
        if pre is not None and row_index is not None:
            k = row_index(symbol)
            closes, ema_arr = pre
            if 0 <= k < len(closes) and abs(closes[k] - cur) <= _PRICE_TOL:
                return None, float(ema_arr[k]), k + 1

        daily_df = bars.get(symbol) if hasattr(bars, "get") else None
        if daily_df is None or len(daily_df) == 0:
            return None, None, 0
        closes_s = daily_df["close"].astype(float).copy()
        # Si el último cierre no coincide con el precio actual, el precio actual reemplaza la última barra.
        if abs(float(closes_s.iloc[-1]) - cur) > _PRICE_TOL:
            closes_s.iloc[-1] = cur
        ema_series = ema(closes_s, self.trailing_ema_period).dropna()
        ema_v = float(ema_series.iloc[-1]) if not ema_series.empty else None
        return closes_s, ema_v, len(daily_df)

    def _past_close(self, ctx: StrategyContext, symbol: str, closes_s: pd.Series | None, n_bars: int, lag: int) -> float:
        if closes_s is not None:
            return float(closes_s.iloc[-(lag + 1)])
        closes, _ = self._pre[symbol]
        return float(closes[n_bars - 1 - lag])

    def _rank(self, ctx: StrategyContext) -> list[tuple[str, float, float, float]]:
        """Candidatos calificados (momentum > 0 y precio >= EMA) ordenados por momentum descendente.

        Cada elemento: (símbolo, momentum, precio, EMA).
        """
        # Caché por evaluación: mismo instante y mismos objetos de precios/barras (manage_positions y
        # generate comparten el contexto de la sesión). Se guardan las referencias para comparar con `is`.
        rc = self._rank_cache
        if rc is not None and rc[0] == ctx.now and rc[1] is ctx.current_prices and rc[2] is ctx.daily_bars:
            return rc[3]

        lb = self.momentum_lookback_days
        ranked: list[tuple[str, float, float, float]] = []
        for symbol in self.universe:
            if symbol == self.refuge_asset:
                continue
            closes_s, ema_v, n_bars = self._close_and_ema(ctx, symbol)
            if ema_v is None or n_bars < lb + 2:
                continue
            cur = float(ctx.current_prices[symbol])
            p_past = self._past_close(ctx, symbol, closes_s, n_bars, lb)
            if p_past <= 0:
                continue
            mom = (cur - p_past) / p_past
            if cur < ema_v or mom <= 0.0:
                continue
            ranked.append((symbol, mom, cur, ema_v))
        ranked.sort(key=lambda x: x[1], reverse=True)
        self._rank_cache = (ctx.now, ctx.current_prices, ctx.daily_bars, ranked)
        return ranked

    def _vol60(self, ctx: StrategyContext, symbol: str) -> float:
        """Volatilidad diaria (log) de 60 sesiones; solo se usa para el score informativo."""
        closes_s, _, n_bars = self._close_and_ema(ctx, symbol)
        if closes_s is None:
            closes, _ = self._pre[symbol]
            closes_s = pd.Series(closes[max(0, n_bars - 61) : n_bars])
        log_rets = np.log(closes_s / closes_s.shift(1))
        vol = float(log_rets.iloc[-60:].std()) if len(log_rets) >= 60 else float(log_rets.std())
        if np.isnan(vol) or vol <= 1e-6:
            vol = 0.01
        return vol

    # ------------------------------------------------------------------ salidas propias
    def manage_positions(self, ctx: StrategyContext) -> list[PositionAction]:
        """Salidas de S5: régimen bajista, trailing EMA, rotación por ranking y máximo de sesiones."""
        actions: list[PositionAction] = []
        if not ctx.positions:
            return actions
        bull = ctx.regime in self.allowed_regimes
        ranked = self._rank(ctx) if bull else []
        rank_of = {sym: i + 1 for i, (sym, *_rest) in enumerate(ranked)}
        keep_limit = self.top_n_leaders + max(0, self.rotation_buffer)

        for sym, pos in ctx.positions.items():
            if pos.strategy_id != self.id:
                continue
            if sym == self.refuge_asset:
                if bull:
                    actions.append(PositionAction(sym, "exit", reason="refuge_exit_bull_regime"))
                continue
            if not bull and self.exit_on_bear:
                actions.append(PositionAction(sym, "exit", reason="regime_bear_exit"))
                continue

            price = ctx.current_prices.get(sym)
            if price is None:
                continue
            cur = float(price)
            _, ema_v, _ = self._close_and_ema(ctx, sym)

            # Trailing EMA25 (misma regla que el overlay histórico)
            if cur < float(pos.current_stop):
                actions.append(PositionAction(sym, "exit", reason="stop_loss"))
                continue
            if ema_v is not None and pos.bars_held >= 3 and cur < ema_v:
                actions.append(PositionAction(sym, "exit", reason="trailing_ema25"))
                continue

            rank = rank_of.get(sym)
            if bull and pos.bars_held >= self.rotation_min_hold_sessions and (rank is None or rank > keep_limit):
                actions.append(PositionAction(sym, "exit", reason="rotation_rank"))
                continue

            if self.max_holding_sessions > 0 and pos.bars_held >= self.max_holding_sessions:
                if self.renew_on_max_hold and rank is not None and rank <= self.top_n_leaders:
                    actions.append(PositionAction(sym, "reset_holding", reason="renewed_leader"))
                else:
                    actions.append(PositionAction(sym, "exit", reason="max_holding"))
                    continue

            if ema_v is not None:
                new_stop = Decimal(str(round(ema_v * (1.0 - self.stop_buffer_pct), 4)))
                if new_stop > pos.current_stop:
                    actions.append(PositionAction(sym, "update_stop", new_stop=new_stop))
        return actions

    # ------------------------------------------------------------------ entradas
    def generate(self, ctx: StrategyContext) -> list[Signal]:
        """Emite señales de compra para los líderes del top N que no están en cartera."""
        # 1. Filtro absoluto de régimen: preservación de capital en mercados bajistas
        if ctx.regime not in self.allowed_regimes:
            if (
                self.refuge_asset
                and self.refuge_asset in ctx.current_prices
                and self.refuge_asset not in ctx.portfolio_positions
            ):
                ref_px = Decimal(str(ctx.current_prices[self.refuge_asset]))
                return [
                    Signal.create(
                        strategy_id=self.id,
                        version=self.version,
                        symbol=self.refuge_asset,
                        bar_ts=ctx.now,
                        side="buy",
                        entry_type="market",
                        entry_price_ref=ref_px,
                        stop_price=Decimal(str(round(float(ref_px) * 0.95, 4))),
                        max_holding=0,
                        score=1.0,
                        target_weight=1.0 if self.equal_weight else None,
                    )
                ]
            return []

        ranked = self._rank(ctx)
        # Con detalle de posiciones (motor de backtest) se compran solo los huecos del top N.
        # Sin detalle (runner en vivo, que mezcla estrategias) se mantiene el comportamiento previo.
        if ctx.positions:
            own_held = [
                s for s, p in ctx.positions.items() if p.strategy_id == self.id and s != self.refuge_asset
            ]
            slots = self.top_n_leaders - len(own_held)
        else:
            slots = self.top_n_leaders
        signals: list[Signal] = []

        for rank, (symbol, mom_score, cur_price_f, val_ema) in enumerate(ranked[: self.top_n_leaders], start=1):
            if symbol in ctx.portfolio_positions:
                continue
            if slots <= 0:
                break
            slots -= 1

            # Stop dinámico inicial: ligeramente bajo la EMA25 (o bajo el precio si la EMA está por encima)
            stop_price_f = min(cur_price_f * (1.0 - self.stop_buffer_pct), val_ema * (1.0 - self.stop_buffer_pct))
            if stop_price_f >= cur_price_f:
                stop_price_f = cur_price_f * (1.0 - self.stop_buffer_pct)

            vol_60 = self._vol60(ctx, symbol)
            inv_vol = 1.0 / (vol_60 * 100.0)
            target_score = float(min(1.0, max(0.1, inv_vol)))

            signals.append(
                Signal(
                    signal_id=compute_signal_id(self.id, self.version, symbol, ctx.now),
                    strategy_id=self.id,
                    symbol=symbol,
                    side="buy",
                    entry_type="limit",
                    entry_price_ref=Decimal(str(round(cur_price_f, 4))),
                    stop_price=Decimal(str(round(stop_price_f, 4))),
                    created_at=ctx.now,
                    limit_price=Decimal(str(round(cur_price_f, 4))),
                    take_profit_price=None,  # Salidas gestionadas en manage_positions
                    max_holding=0,  # El límite de sesiones lo gestiona manage_positions (con renovación)
                    exit_at_close=False,
                    score=round(target_score, 4),
                    target_weight=(1.0 / self.top_n_leaders) if self.equal_weight else None,
                    features={
                        "momentum": round(mom_score, 4),
                        "momentum_lookback_days": self.momentum_lookback_days,
                        f"momentum_{self.momentum_lookback_days}d": round(mom_score, 4),
                        "momentum_45d": round(mom_score, 4),
                        "rank": rank,
                        "trailing_ema": round(val_ema, 4),
                        "trailing_ema_period": self.trailing_ema_period,
                        "ema25": round(val_ema, 4),
                        "ema20": round(val_ema, 4),
                    },
                )
            )

        return signals

