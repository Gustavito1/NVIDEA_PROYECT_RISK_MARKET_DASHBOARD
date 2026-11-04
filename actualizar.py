#!/usr/bin/env python
"""
ACTUALIZACION PERIODICA del sistema NVDA (pensada para cron / GitHub Actions).

Paso a paso:
  1. Incremental: calcula SOLO la cola nueva (features, labels, agregados
     diarios, impacto de noticias) usando el watermark persistido -> DB de
     analisis. Idempotente: si se ejecuta 2 veces seguidas no duplica nada.
  2. Regenera el informe HTML desde la DB de analisis y actualiza los
     productos del modelo (predicciones, backtest, metricas).

Uso:
    python actualizar.py

Ejemplo cron (cada hora a los :40, despues del ETL de IBKR del companero):
    40 * * * *  cd /ruta/PRECIARIO_NVDA && venv/bin/python actualizar.py >> logs/actualizar.log 2>&1

Si la DB de PRODUCCION (Aiven) tiene datos nuevos, aqui se incorporan;
si no hay nada nuevo, el incremental es casi instantaneo y no escribe nada.
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
    print(f"ACTUALIZACION NVDA - {time.strftime('%Y-%m-%d %H:%M:%S')}")

    ok_inc = paso("PASO 1/2: Actualizacion incremental -> DB de analisis",
                  [sys.executable, "-m", "core.incremental"])
    if not ok_inc:
        print("\nFALLO en la actualizacion incremental")
        return 1

    ok_html = paso("PASO 2/2: Informe HTML desde la DB + productos del modelo",
                   [sys.executable, "scripts/report/build_report.py"])
    if not ok_html:
        print("\nFALLO generando el informe HTML")
        return 1

    print(f"\nActualizacion completa en {time.time() - t0:.0f}s "
          f"(web/REPORT_NVDA/nvda_report.html)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
