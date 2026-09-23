from __future__ import annotations
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from tbot.regime.filter import MarketRegime
from tbot.strategies.interfaces import Signal, Strategy, StrategyContext, StrategyDataRequirements

from tbot.strategies.s5_dual_momentum_leader import DualMomentumLeaderStrategy
from tbot.strategies.s11_volatility_squeeze import VolatilitySqueezeStrategy
from tbot.strategies.s9_turn_of_month_momentum import TurnOfMonthMomentumStrategy

class CoreSatelliteOverlayStrategy:
    """
    S12: Meta-estrategia que combina una estrategia Core (S5 Momentum) 
    con una Satellite de corto plazo (S11 Squeeze o S9).
    
    Lógica de Overriding:
    - La estrategia Core opera con normalidad.
    - Cuando la estrategia Satellite genera una señal fuerte, 'toma prestado' 
      el capital de una de las posiciones Core emitiendo una orden explícita de venta
      sobre la posición Core con menor fuerza relativa.
    - Una vez que la Satellite termina su operación, la estrategia Core 
      automáticamente reocupará el espacio libre al día siguiente.
    """
    id = "S12"
    version = "1.0.0"
    schedule = ["15:45 America/New_York"]
    allowed_regimes = {MarketRegime.BULL_CALM, MarketRegime.BULL_VOLATILE, MarketRegime.BEAR}
    allows_open_window = False
    universe = None
    
    def __init__(self, core_strategy=None, satellite_strategy=None, max_positions=4):
        self.core = core_strategy if core_strategy else DualMomentumLeaderStrategy()
        self.satellite = satellite_strategy if satellite_strategy else VolatilitySqueezeStrategy()
        self.max_positions = max_positions
        
        self.data_requirements = StrategyDataRequirements(
            needs_daily_bars=True, 
            daily_lookback_days=250, 
            needs_intraday_bars=False
        )

    def generate(self, ctx: StrategyContext) -> list[Signal]:
        # Generar señales base
        core_signals = self.core.generate(ctx)
        sat_signals = self.satellite.generate(ctx)
        
        final_signals = []
        sat_symbols = []
        
        # 1. Asignar prioridad máxima a señales del Satellite
        for s in sat_signals:
            if s.side == "buy":
                # Aumentamos el score para asegurar que ganen la prioridad
                s_dict = s.__dict__.copy()
                s_dict["score"] = 2.0 
                final_signals.append(Signal(**s_dict))
                sat_symbols.append(s.symbol)
        
        # 2. Filtrar y ordenar señales del Core (de mayor a menor score)
        core_buys = [s for s in core_signals if s.side == "buy" and s.symbol not in sat_symbols]
        core_buys.sort(key=lambda x: x.score, reverse=True)
        
        # Agregar al pool
        final_signals.extend(core_buys)
        
        # 3. Lógica de "Desalojo" (Eviction):
        # Si la cartera está llena y el Satellite quiere entrar, debemos vender algo del Core.
        current_holdings = list(ctx.portfolio_positions)
        
        if len(current_holdings) >= self.max_positions and len(sat_symbols) > 0:
            # Calcular cuánto espacio extra necesitamos
            needed_slots = len(sat_symbols) - (self.max_positions - len(current_holdings))
            
            if needed_slots > 0:
                # Buscamos cuáles activos sostenidos NO son del Satellite, para desalojarlos.
                core_buy_symbols = {s.symbol: s.score for s in core_buys}
                
                # Asignar score actual a las posiciones
                held_scores = []
                for sym in current_holdings:
                    if sym not in sat_symbols:
                        score = core_buy_symbols.get(sym, -1.0)
                        held_scores.append((sym, score))
                
                # Ordenar de menor a mayor score
                held_scores.sort(key=lambda x: x[1])
                
                # Emitir señal de venta explícita para liberar los slots necesarios
                for i in range(min(needed_slots, len(held_scores))):
                    sym_to_sell = held_scores[i][0]
                    sell_sig = Signal.create(
                        strategy_id=self.id,
                        version=self.version,
                        symbol=sym_to_sell,
                        bar_ts=ctx.now,
                        side="sell",
                        entry_type="market",
                        entry_price_ref=Decimal("0"),
                        stop_price=Decimal("0")
                    )
                    final_signals.insert(0, sell_sig)

        return final_signals
