#!/usr/bin/env python
"""
EJECUCION COMPLETA del sistema NVDA (primera vez o re-sincronizacion total).

Paso a paso:
  1. Verifica que la DB de ANALISIS tenga las 11 tablas (analytics.*)
  2. Backfill completo: features + labels + agregados diarios + impacto de
     noticias -> DB de analisis (lee de la DB de PRODUCCION/Aiven)
  3. Genera el informe HTML desde la DB de analisis y persiste los productos
     del modelo (predicciones, backtest, metricas, matriz de confusion)

Uso:
    python ejecutar_todo.py

Equivalente a:
    python -m core.incremental --backfill
    python scripts/report/build_report.py
"""
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def paso(nombre: str, cmd: list) -> bool:
    print(f"\n{'=' * 60}\n  {nombre}\n{'=' * 60}", flush=True)
    result = subprocess.run(cmd, cwd=str(BASE_DIR))
    return result.returncode == 0


def main() -> int:
    t0 = time.time()
    print("EJECUCION COMPLETA - PRECIARIO_NVDA")

    ok_backfill = paso("PASO 1/2: Backfill completo -> DB de analisis",
                       [sys.executable, "-m", "core.incremental", "--backfill"])
    if not ok_backfill:
        print("\nFALLO en el backfill. Si faltan tablas, crea el esquema con:")
        print("  db/create-analitycs-db.sql  (o scripts/db/schema_analytics.sql)")
        return 1

    ok_html = paso("PASO 2/2: Informe HTML desde la DB + productos del modelo",
                   [sys.executable, "scripts/report/build_report.py"])
    if not ok_html:
        print("\nFALLO generando el informe HTML")
        return 1

    print(f"\n{'=' * 60}")
    print(f"TODO OK en {time.time() - t0:.0f}s")
    print(f"  Informe: web/REPORT_NVDA/nvda_report.html")
    print(f"{'=' * 60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
