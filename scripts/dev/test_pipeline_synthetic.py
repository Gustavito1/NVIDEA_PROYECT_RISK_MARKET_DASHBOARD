#!/usr/bin/env python
"""
Valida el pipeline intradia con datos SINTETICOS (sin TWS/IBKR).

Genera ~4 meses de barras 1h RTH para NVDA y SOXQ, las guarda en el formato
real de nvda_intradia.xlsx y corre feature_engineering + labels.

Uso:
    python scripts/dev/test_pipeline_synthetic.py
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from core.config import INTRADIA_FILE
from core.feature_engineering import build_features
from core.labels import build_labels


def _serie(rng: np.random.Generator, n: int, base: float, drift: float = 0.0002) -> pd.DataFrame:
    rets = rng.normal(drift, 0.006, n)
    close = base * np.exp(np.cumsum(rets))
    open_ = close * (1 + rng.normal(0, 0.001, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.002, n)))
    volume = rng.integers(500_000, 5_000_000, n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def generar_sintetico(dias: int = 85) -> pd.DataFrame:
    """Barras 1h RTH (7 por dia) para NVDA y SOXQ."""
    rng = np.random.default_rng(42)
    fechas = pd.bdate_range("2026-03-02", periods=dias, tz="America/New_York")
    horas_ny = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]

    timestamps = []
    for f in fechas:
        for h in horas_ny:
            hh, mm = map(int, h.split(":"))
            timestamps.append(pd.Timestamp(f.year, f.month, f.day, hh, mm, tz="America/New_York"))
    dt = pd.DatetimeIndex(timestamps)

    nvda = _serie(rng, len(dt), base=180.0)
    soxq = _serie(rng, len(dt), base=52.0, drift=0.00015)
    nvda["Datetime"] = dt.tz_convert("UTC")
    soxq["Datetime"] = dt.tz_convert("UTC")
    return nvda[["Datetime", "Open", "High", "Low", "Close", "Volume"]], \
           soxq[["Datetime", "Open", "High", "Low", "Close", "Volume"]]


def guardar_excel(nvda, soxq) -> None:
    from zoneinfo import ZoneInfo
    lima = ZoneInfo("America/Lima")
    INTRADIA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(INTRADIA_FILE, engine="openpyxl") as writer:
        for sheet, df in [("NVDA_1H", nvda), ("SOXQ_1H", soxq)]:
            d = df.copy()
            d["Datetime"] = pd.to_datetime(d["Datetime"], utc=True).dt.tz_convert(lima).dt.strftime(
                "%Y-%m-%d %H:%M:%S-05:00"
            )
            d.to_excel(writer, sheet_name=sheet, index=False)
    print(f"[OK] Sintetico guardado en {INTRADIA_FILE}")


def main() -> int:
    import core.config as config
    import core.feature_engineering as fe

    backup = None
    if INTRADIA_FILE.exists():
        backup = INTRADIA_FILE.read_bytes()
        print("[i] Datos reales detectados: se respaldan y restauran al final")

    orig_sheets = fe.load_intradia_sheets
    orig_source = config.DATA_SOURCE
    try:
        nvda, soxq = generar_sintetico()
        print(f"Generadas {len(nvda)} barras NVDA y {len(soxq)} SOXQ")
        guardar_excel(nvda, soxq)

        # Aislamiento total: el test SIEMPRE usa los datos sinteticos, sin DB
        # remota ni CSVs reales (antes DATA_SOURCE=auto/CSVs expandidos se
        # comian los datos sinteticos y el test no probaba lo que prometia).
        config.DATA_SOURCE = "csv"
        fe.load_intradia_sheets = lambda sheets=("NVDA_1H", "SOXQ_1H"): {
            "NVDA_1H": nvda.copy(), "SOXQ_1H": soxq.copy()}

        feats = build_features(include_patterns=True, save=True)
        if feats is None:
            print("[FALLO] build_features devolvio None")
            return 1
        print(f"[OK] Features: {feats.shape[0]} filas x {feats.shape[1]} columnas")

        labels = build_labels()
        if labels is None:
            print("[FALLO] build_labels devolvio None")
            return 1

        print("\nColumnas de features (primeras 40):")
        print(", ".join(sorted(feats.columns)[:40]))
        print("\nDistribucion de labels:")
        print(labels["label"].value_counts().to_string())
        print("\nUltimas filas etiquetadas:")
        print(labels[["Datetime", "Close", "future_ret", "tau", "label"]].tail(3).to_string(index=False))
        print("\n[OK] Pipeline sintetico completo")
        return 0
    finally:
        fe.load_intradia_sheets = orig_sheets
        config.DATA_SOURCE = orig_source
        if backup is not None:
            INTRADIA_FILE.write_bytes(backup)
            print("[i] Datos reales restaurados. Regenerando features/labels reales...")
            build_features(save=True)
            build_labels()
            print("[i] Listo: el proyecto quedo con datos reales")


if __name__ == "__main__":
    sys.exit(main())
