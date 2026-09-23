"""Comparador experimental de Variante A (Engine Only) vs Variante B (Engine + AI Veto).

Pregunta que busca responder: ¿el veto de IA agrega valor estadístico o reduce la rentabilidad neta?

Estado (motor v2.3): el veto de IA (asíncrono, con noticias y features) todavía no está integrado a la
generación de señales del backtest. La versión anterior ejecutaba la MISMA simulación para ambas
variantes y presentaba la comparación como si fuera real. Hasta integrar el veto, el comparador aborta
de forma explícita para no producir conclusiones inválidas.
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from tbot.backtest.guards import assert_not_holdout


async def run_comparison(
    start_date: date = date(2020, 1, 1),
    end_date: date = date(2022, 12, 31),
    capital: float = 2000.0,
    data_dir: Path | str = "data/historical_2020_2022",
) -> int:
    assert_not_holdout(start=start_date, end=end_date, resolution="daily")
    raise NotImplementedError(
        "Variante B (veto de IA) no está integrada al motor de backtest: la comparación A vs B "
        "no es válida. Integrar AIVeto en la generación de señales antes de usar este comparador."
    )


def main() -> int:
    return asyncio.run(run_comparison())


if __name__ == "__main__":
    import sys

    sys.exit(main())
