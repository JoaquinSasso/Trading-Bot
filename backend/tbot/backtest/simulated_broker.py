"""Broker simulado para backtesting y replay offline.

Modela la ejecución de órdenes con las fricciones de un broker retail (Alpaca):
- Fills con slippage y medio spread diferenciados (ETFs vs acciones).
- Stops ejecutados al peor precio ante gaps (min(open, stop)).
- Orden de evaluación dentro de una barra: stop -> take profit -> salidas al cierre.
- Tarifas regulatorias en ventas (SEC fee + FINRA TAF + CAT).
- Liquidación T+N con calendario de sesiones (omite feriados si se provee el calendario).
- Cuenta cash con modelo Reg T: se puede comprar con fondos no liquidados y se cuentan
  las Good Faith Violations (GFV) si se vende antes de que esos fondos liquiden.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from tbot.strategies.interfaces import Signal

# Conjunto histórico (motor v2.x) conservado para reproducir resultados previos (modo legacy).
LEGACY_ETF_SYMBOLS: frozenset[str] = frozenset(
    {"SPY", "QQQ", "IWM", "GLD", "SPYM", "QQQM", "GLDM", "TLT", "XLE", "XLV"}
)

# ETFs líquidos usados en los universos del proyecto (spread/slippage de ETF).
DEFAULT_ETF_SYMBOLS: frozenset[str] = frozenset(
    {
        "SPY", "SPYM", "VOO", "IVV", "QQQ", "QQQM", "IWM", "DIA", "MTUM",
        "GLD", "GLDM", "IAU", "SLV",
        "TLT", "IEF", "SHY", "BIL", "SGOV", "TIP", "AGG", "BND", "LQD", "HYG",
        "IEFA", "IEMG", "EFA", "EEM", "VEA", "VWO",
        "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    }
)


@dataclass
class UnsettledCredit:
    """Crédito de efectivo no liquidado pendiente de settlement T+1/T+2."""

    amount: Decimal
    settlement_date: date


@dataclass
class SimulatedPosition:
    """Posición abierta en el broker simulado."""

    symbol: str
    qty: Decimal
    entry_price: Decimal
    entry_time: datetime
    initial_stop: Decimal
    current_stop: Decimal
    take_profit: Decimal | None
    strategy_id: str
    max_holding_bars: int = 0
    bars_held: int = 0
    exit_at_close: bool = False
    fees_paid: Decimal = Decimal("0.0")
    # Cuenta cash: fecha hasta la cual la compra quedó financiada con fondos no liquidados.
    # Venderla antes de esa fecha constituye una Good Faith Violation (GFV).
    unsettled_funding_until: date | None = None


@dataclass
class SimulatedTrade:
    """Registro de un trade cerrado y liquidado."""

    trade_id: str
    symbol: str
    strategy_id: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    pnl: Decimal
    pnl_pct: float
    pnl_r: float
    fees: Decimal
    slippage_cost: Decimal
    exit_reason: str
    initial_risk_usd: Decimal
    account_type: str = "cash"
    gfv: bool = False


def _to_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


class SimulatedBroker:
    """Simulador de broker para ejecución de órdenes en replay."""

    # Conjunto de ETFs por defecto (spread y slippage de ETF). Ver DEFAULT_ETF_SYMBOLS.
    ETF_SYMBOLS = DEFAULT_ETF_SYMBOLS

    def __init__(
        self,
        initial_capital: Decimal = Decimal("2000.00"),
        etf_slippage_bps: float = 2.0,  # 2 bps para ETFs
        stock_slippage_bps: float = 5.0,  # 5 bps para acciones
        sec_fee_rate: Decimal = Decimal("0.0000278"),  # $27.80 por millón de USD nocional vendido
        finra_taf_per_share: Decimal = Decimal("0.000166"),  # $0.000166 por acción vendida
        max_finra_taf_per_order: Decimal = Decimal("8.30"),
        account_type: str = "cash",
        settlement_days: int = 1,
        etf_half_spread_bps: float = 0.0,
        stock_half_spread_bps: float = 0.0,
        cat_fee_per_share: Decimal = Decimal("0.00003"),
        integer_shares: bool = False,
        etf_symbols: set[str] | frozenset[str] | None = None,
        trading_days: list[date] | None = None,
        cash_account_model: str = "settled_only",
        legacy: bool = False,
    ) -> None:
        self.initial_capital = initial_capital
        self.account_type = account_type.lower()
        self.settlement_days = settlement_days
        self.settled_cash = initial_capital
        self.unsettled_cash = Decimal("0.0")
        self._unsettled_credits: list[UnsettledCredit] = []

        self.etf_slippage = Decimal(str(etf_slippage_bps / 10000.0))
        self.stock_slippage = Decimal(str(stock_slippage_bps / 10000.0))
        self.etf_half_spread = Decimal(str(etf_half_spread_bps / 10000.0))
        self.stock_half_spread = Decimal(str(stock_half_spread_bps / 10000.0))
        self.sec_fee_rate = sec_fee_rate
        self.finra_taf_per_share = finra_taf_per_share
        self.max_finra_taf = max_finra_taf_per_order
        self.cat_fee_per_share = cat_fee_per_share
        self.integer_shares = integer_shares
        self.etf_symbols: frozenset[str] = (
            frozenset(s.upper() for s in etf_symbols) if etf_symbols is not None else self.ETF_SYMBOLS
        )

        # Calendario de sesiones para liquidación (omite feriados). Sin calendario: solo fines de semana.
        self._trading_days: list[date] = sorted(set(trading_days)) if trading_days else []
        # "gfv_aware": se puede comprar con fondos no liquidados (Reg T) y se cuentan las GFV.
        # "settled_only": solo fondos liquidados (modelo conservador del motor v2.x).
        if cash_account_model not in ("gfv_aware", "settled_only"):
            raise ValueError(f"cash_account_model inválido: {cash_account_model!r}")
        self.cash_account_model = cash_account_model
        # Reproduce el comportamiento del motor v2.x (para validar equivalencia).
        self.legacy = legacy
        self.gfv_count = 0

        self.positions: dict[str, SimulatedPosition] = {}
        self.closed_trades: list[SimulatedTrade] = []
        self.trade_counter = 0

    # ------------------------------------------------------------------ efectivo
    @property
    def cash(self) -> Decimal:
        """Efectivo total (liquidado + no liquidado)."""
        return self.settled_cash + self.unsettled_cash

    @cash.setter
    def cash(self, value: Decimal) -> None:
        """Permite asignar efectivo manteniendo compatibilidad hacia atrás."""
        diff = Decimal(str(value)) - (self.settled_cash + self.unsettled_cash)
        self.settled_cash += diff

    @property
    def buying_power(self) -> Decimal:
        """Poder de compra disponible según el tipo de cuenta.

        Cash + "settled_only": solo fondos liquidados.
        Cash + "gfv_aware": fondos liquidados + no liquidados (Reg T); las ventas prematuras
        de lo comprado con fondos no liquidados se registran como GFV.
        Margin (sin apalancamiento): efectivo total.
        """
        if self.account_type == "cash" and self.cash_account_model == "settled_only":
            return max(Decimal("0.0"), self.settled_cash)
        return max(Decimal("0.0"), self.cash)

    def set_trading_days(self, trading_days: list[date]) -> None:
        """Define el calendario de sesiones usado para calcular la fecha de liquidación."""
        self._trading_days = sorted(set(trading_days))

    def calculate_settlement_date(self, trade_date: date | datetime) -> date:
        """Fecha de liquidación: `settlement_days` sesiones hábiles después de la operación."""
        d = _to_date(trade_date)
        cal = self._trading_days
        if cal and cal[0] <= d <= cal[-1]:
            idx = bisect.bisect_right(cal, d) + self.settlement_days - 1
            if idx < len(cal):
                return cal[idx]
            # Más allá del calendario conocido: continuar con días de semana.
            extra = idx - (len(cal) - 1)
            d = cal[-1]
            while extra > 0:
                d += timedelta(days=1)
                if d.weekday() < 5:
                    extra -= 1
            return d
        added_days = 0
        while added_days < self.settlement_days:
            d += timedelta(days=1)
            if d.weekday() < 5:  # 0=Lunes, 4=Viernes
                added_days += 1
        return d

    def process_settlement(self, current_date: date | datetime) -> None:
        """Libera créditos no liquidados cuya fecha de liquidación haya llegado."""
        if not self._unsettled_credits:
            return
        cur_d = _to_date(current_date)
        remaining: list[UnsettledCredit] = []
        for credit in self._unsettled_credits:
            if cur_d >= credit.settlement_date:
                self.settled_cash += credit.amount
                self.unsettled_cash = max(Decimal("0.0"), self.unsettled_cash - credit.amount)
            else:
                remaining.append(credit)
        self._unsettled_credits = remaining

    def _consume_unsettled(self, amount: Decimal) -> date | None:
        """Descuenta `amount` de los créditos no liquidados (primero los que liquidan antes).

        Devuelve la fecha de liquidación más tardía consumida (límite para evitar una GFV).
        """
        remaining = amount
        latest: date | None = None
        self._unsettled_credits.sort(key=lambda c: c.settlement_date)
        kept: list[UnsettledCredit] = []
        for credit in self._unsettled_credits:
            if remaining > 0 and credit.amount > 0:
                take = min(credit.amount, remaining)
                credit.amount -= take
                remaining -= take
                self.unsettled_cash -= take
                latest = credit.settlement_date if latest is None else max(latest, credit.settlement_date)
            if credit.amount > 0:
                kept.append(credit)
        self._unsettled_credits = kept
        if remaining > 0:
            # No debería ocurrir (buying_power lo impide).
            self.settled_cash -= remaining
        return latest

    # ------------------------------------------------------------------ costos
    def _get_slippage_rate(self, symbol: str) -> Decimal:
        """Determina la tasa de slippage según si el símbolo es un ETF o una acción."""
        if symbol.upper() in self.etf_symbols:
            return self.etf_slippage
        return self.stock_slippage

    def _get_half_spread_rate(self, symbol: str) -> Decimal:
        """Determina la tasa de medio spread según si el símbolo es un ETF o una acción."""
        if symbol.upper() in self.etf_symbols:
            return self.etf_half_spread
        return self.stock_half_spread

    def calculate_sell_regulatory_fees(self, qty: Decimal, exit_price: Decimal) -> Decimal:
        """Calcula las tarifas regulatorias SEC fee, FINRA TAF y CAT fee en ventas."""
        notional = qty * exit_price
        sec_fee = notional * self.sec_fee_rate
        finra_fee = min(qty * self.finra_taf_per_share, self.max_finra_taf)
        cat_fee = qty * self.cat_fee_per_share
        return round(sec_fee + finra_fee + cat_fee, 4)

    def get_equity(self, current_prices: dict[str, Decimal]) -> Decimal:
        """Calcula el valor liquidativo total (efectivo + valor de mercado de posiciones)."""
        pos_value = Decimal("0.0")
        for sym, pos in self.positions.items():
            price = current_prices.get(sym, pos.entry_price)
            pos_value += pos.qty * price
        return self.cash + pos_value

    # ------------------------------------------------------------------ órdenes
    def submit_buy(
        self,
        signal: Signal,
        qty: Decimal,
        next_bar_open: Decimal,
        timestamp: datetime,
    ) -> SimulatedPosition | None:
        """Ejecuta una compra al precio de referencia dado (`next_bar_open`) con slippage y medio spread.

        El nombre del parámetro se conserva por compatibilidad: el motor pasa el precio de
        ejecución que corresponda (cierre de la sesión o apertura de la siguiente).
        """
        self.process_settlement(timestamp)
        available_funds = self.buying_power

        if self.integer_shares:
            qty = Decimal(int(qty))

        if qty <= 0 or available_funds <= 0:
            return None

        slippage_rate = self._get_slippage_rate(signal.symbol)
        half_spread_rate = self._get_half_spread_rate(signal.symbol)
        # Compra: Se paga ligeramente más debido al slippage y al medio spread
        fill_price = round(next_bar_open * (Decimal("1.0") + slippage_rate + half_spread_rate), 4)
        notional = qty * fill_price

        # Comprobar poder de compra disponible
        if notional > available_funds:
            qty = Decimal(int(available_funds / fill_price))
            if qty <= 0:
                return None
            notional = qty * fill_price

        unsettled_until: date | None = None
        if notional <= self.settled_cash:
            self.settled_cash -= notional
        else:
            # Parte de la compra se financia con créditos no liquidados (gfv_aware o margin).
            from_unsettled = notional - max(Decimal("0.0"), self.settled_cash)
            self.settled_cash = min(self.settled_cash, Decimal("0.0"))
            unsettled_until = self._consume_unsettled(from_unsettled)

        if isinstance(signal.max_holding, int):
            max_bars = signal.max_holding
        elif isinstance(signal.max_holding, timedelta):
            max_bars = int(signal.max_holding.total_seconds() // 3600)
        else:
            max_bars = 0

        pos = SimulatedPosition(
            symbol=signal.symbol,
            qty=qty,
            entry_price=fill_price,
            entry_time=timestamp,
            initial_stop=signal.stop_price,
            current_stop=signal.stop_price,
            take_profit=signal.take_profit_price,
            strategy_id=signal.strategy_id,
            exit_at_close=signal.exit_at_close,
            max_holding_bars=max_bars,
            unsettled_funding_until=unsettled_until if self.account_type == "cash" else None,
        )
        self.positions[signal.symbol] = pos
        return pos

    def close_position(
        self,
        symbol: str,
        exit_price_raw: Decimal,
        timestamp: datetime,
        reason: str,
        is_gap: bool = False,
    ) -> SimulatedTrade | None:
        """Cierra una posición abierta, deduciendo slippage, medio spread y tarifas regulatorias."""
        self.process_settlement(timestamp)
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return None

        is_gfv = bool(
            self.account_type == "cash"
            and pos.unsettled_funding_until is not None
            and _to_date(timestamp) < pos.unsettled_funding_until
        )
        if is_gfv:
            self.gfv_count += 1

        slippage_rate = self._get_slippage_rate(symbol)
        half_spread_rate = self._get_half_spread_rate(symbol)
        # Venta: Se recibe ligeramente menos debido al slippage y medio spread
        fill_price = round(exit_price_raw * (Decimal("1.0") - slippage_rate - half_spread_rate), 4)

        notional_gross = pos.qty * fill_price
        fees = self.calculate_sell_regulatory_fees(pos.qty, fill_price)
        net_proceeds = notional_gross - fees

        if self.account_type == "cash":
            self.unsettled_cash += net_proceeds
            settle_date = self.calculate_settlement_date(timestamp)
            self._unsettled_credits.append(
                UnsettledCredit(amount=net_proceeds, settlement_date=settle_date)
            )
        else:
            self.settled_cash += net_proceeds

        cost_basis = pos.qty * pos.entry_price
        pnl = net_proceeds - cost_basis
        pnl_pct = float(pnl / cost_basis) if cost_basis > 0 else 0.0

        # Cálculo de PnL en R (riesgo inicial asumido)
        initial_risk_per_unit = pos.entry_price - pos.initial_stop
        if initial_risk_per_unit > 0:
            initial_risk_total = pos.qty * initial_risk_per_unit
            pnl_r = float(pnl / initial_risk_total)
        else:
            initial_risk_total = Decimal("0.0")
            pnl_r = 0.0

        slippage_cost = pos.qty * (exit_price_raw - fill_price)

        self.trade_counter += 1
        trade = SimulatedTrade(
            trade_id=f"T{self.trade_counter:05d}",
            symbol=symbol,
            strategy_id=pos.strategy_id,
            entry_time=pos.entry_time,
            exit_time=timestamp,
            entry_price=pos.entry_price,
            exit_price=fill_price,
            qty=pos.qty,
            pnl=round(pnl, 4),
            pnl_pct=round(pnl_pct, 6),
            pnl_r=round(pnl_r, 4),
            fees=fees,
            slippage_cost=round(slippage_cost, 4),
            exit_reason=reason,
            initial_risk_usd=round(initial_risk_total, 4),
            account_type=self.account_type,
            gfv=is_gfv,
        )
        self.closed_trades.append(trade)
        return trade

    def evaluate_bar(
        self,
        symbol: str,
        bar_open: Decimal,
        bar_high: Decimal,
        bar_low: Decimal,
        bar_close: Decimal,
        timestamp: datetime,
        is_market_close: bool = False,
        enforce_max_holding: bool = False,
    ) -> SimulatedTrade | None:
        """Evalúa si la barra activa el Stop Loss, el Take Profit o una salida al cierre.

        Orden: órdenes en reposo primero (stop, luego take profit) y después las salidas al
        cierre (exit_at_close y, si `enforce_max_holding`, el límite de barras mantenidas).
        """
        self.process_settlement(timestamp)
        pos = self.positions.get(symbol)
        if pos is None:
            return None

        pos.bars_held += 1

        # Compatibilidad v2.x: exit_at_close se evaluaba ANTES que el stop (ignoraba stops intradía).
        if self.legacy and pos.exit_at_close and is_market_close:
            return self.close_position(symbol, bar_close, timestamp, reason="exit_at_close")

        # 1. Stop Loss (con modelado de gaps)
        if bar_low <= pos.current_stop:
            if bar_open <= pos.current_stop:
                exit_price = bar_open
                is_gap = True
            else:
                exit_price = pos.current_stop
                is_gap = False
            return self.close_position(
                symbol, exit_price, timestamp, reason="stop_loss", is_gap=is_gap
            )

        # 2. Take Profit (si está definido)
        if pos.take_profit is not None and bar_high >= pos.take_profit:
            exit_price = bar_open if bar_open >= pos.take_profit else pos.take_profit
            return self.close_position(symbol, exit_price, timestamp, reason="take_profit")

        # 3. Salidas al cierre
        if is_market_close and pos.exit_at_close:
            return self.close_position(symbol, bar_close, timestamp, reason="exit_at_close")
        if (
            enforce_max_holding
            and is_market_close
            and pos.max_holding_bars > 0
            and pos.bars_held >= pos.max_holding_bars
        ):
            return self.close_position(symbol, bar_close, timestamp, reason="max_holding")

        return None
