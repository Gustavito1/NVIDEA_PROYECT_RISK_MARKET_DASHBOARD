"""
Snapshot y backup de datos intradia NVDA (equivalente a PRECIARIO_OIL/core/wti_snapshot.py).

Funciones:
1. backup_intradia_xlsx() - Copia diaria de los datos intradia (CSV expandidos
   y/o Excel legacy) a backups/
2. guardar_snapshot_nvda() - Guarda cierre por barra del dia en CSV acumulativo

El CSV permite reconstruir la evolucion intradia NVDA/SOXQ aunque se pierdan
los datos fuente. Usa indice de barra (bar_1..bar_N) en lugar de hora fija
para ser robusto ante cambios de horario DST.
"""
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from core.config import (
    INTRADIA_BACKUP_DIR,
    INTRADIA_FILE,
    INTRADIA_NVDA_CSV,
    INTRADIA_SOXQ_CSV,
    SNAPSHOT_BAR_COUNT,
    SNAPSHOT_CSV,
)

logger = logging.getLogger(__name__)

LIMA_TZ = ZoneInfo("America/Lima")
NY_TZ = ZoneInfo("America/New_York")
BACKUP_KEEP_DAYS = 30


def _limpiar_backups_antiguos(dias_mantener: int = BACKUP_KEEP_DAYS) -> None:
    if not INTRADIA_BACKUP_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=dias_mantener)
    for f in list(INTRADIA_BACKUP_DIR.glob("nvda_intradia_*.xlsx")) + \
            list(INTRADIA_BACKUP_DIR.glob("*_intraday_*.csv")):
        try:
            fecha = datetime.strptime(f.stem[-10:], "%Y-%m-%d")
            if fecha < cutoff:
                f.unlink()
                logger.info(f"Backup antiguo eliminado: {f.name}")
        except (ValueError, OSError):
            pass


def backup_intradia_xlsx() -> bool:
    """
    Backup diario de los datos intradia (una copia por dia).

    Cubre tanto el Excel legacy (nvda_intradia.xlsx) como los CSV expandidos
    por simbolo (fuente de verdad del pipeline). True si al menos una copia OK.
    """
    INTRADIA_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(LIMA_TZ).strftime("%Y-%m-%d")
    ok = False

    # 1. Excel legacy (si existe)
    if INTRADIA_FILE.exists():
        backup_path = INTRADIA_BACKUP_DIR / f"nvda_intradia_{today}.xlsx"
        if backup_path.exists():
            logger.debug(f"Backup ya existe para hoy: {backup_path.name}")
            ok = True
        else:
            try:
                shutil.copy2(str(INTRADIA_FILE), str(backup_path))
                logger.info(f"Backup intradia creado: {backup_path.name}")
                ok = True
            except Exception as e:
                logger.error(f"Error haciendo backup del Excel intradia: {e}")

    # 2. CSV expandidos por simbolo (fuente de verdad)
    for csv_path in (INTRADIA_NVDA_CSV, INTRADIA_SOXQ_CSV):
        if not csv_path.exists():
            continue
        backup_path = INTRADIA_BACKUP_DIR / f"{csv_path.stem}_{today}.csv"
        if backup_path.exists():
            ok = True
            continue
        try:
            shutil.copy2(str(csv_path), str(backup_path))
            logger.info(f"Backup intradia creado: {backup_path.name}")
            ok = True
        except Exception as e:
            logger.error(f"Error haciendo backup de {csv_path.name}: {e}")

    if not ok:
        logger.warning("No hay datos intradia que respaldar")
        return False
    _limpiar_backups_antiguos()
    return True


def _load_sheet(sheet: str) -> pd.DataFrame | None:
    """
    Carga barras intradia desde la fuente de verdad del pipeline:
    CSV expandido por simbolo, con fallback al Excel IBKR legacy.
    """
    try:
        from core.feature_engineering import load_intradia_sheet
        df = load_intradia_sheet(sheet)
        if df is None or df.empty:
            return None
        return df.sort_values("Datetime").reset_index(drop=True)
    except Exception as e:
        logger.warning(f"No se pudo leer sheet {sheet}: {e}")
        return None


def _resumen_diario(df: pd.DataFrame, prefijo: str) -> pd.DataFrame:
    """
    Una fila por dia con cierre de cada barra y retorno del dia.

    Agrupa por fecha NY (consistente con date_ny del resto del pipeline;
    antes usaba hora de Lima y podia desalinearse en cambios DST).
    """
    df = df.copy()
    dt_ny = df["Datetime"].dt.tz_convert(NY_TZ)
    df["fecha"] = dt_ny.dt.date
    df["bar_idx"] = df.groupby("fecha", sort=False).cumcount() + 1

    piv = df.pivot_table(index="fecha", columns="bar_idx", values="Close", aggfunc="last")
    piv = piv.reindex(columns=range(1, SNAPSHOT_BAR_COUNT + 1))
    piv.columns = [f"{prefijo}_bar_{i}" for i in piv.columns]
    piv = piv.reset_index()

    cierre = df.groupby("fecha", sort=False)["Close"].last().rename(f"{prefijo}_close").reset_index()
    apertura = df.groupby("fecha", sort=False)["Open"].first().rename(f"{prefijo}_open").reset_index()
    ret = (cierre.set_index("fecha")[f"{prefijo}_close"] /
           apertura.set_index("fecha")[f"{prefijo}_open"] - 1).rename(f"{prefijo}_day_ret").reset_index()

    out = piv.merge(apertura, on="fecha", how="left") \
             .merge(cierre, on="fecha", how="left") \
             .merge(ret, on="fecha", how="left")
    return out


def guardar_snapshot_nvda() -> bool:
    """
    Extrae el cierre de cada barra del dia (bar_1..bar_N) y el retorno diario
    para NVDA y SOXQ, y lo acumula en data/nvda_snapshot_horario.csv.
    """
    nvda = _load_sheet("NVDA_1H")
    if nvda is None:
        logger.warning("No hay datos NVDA_1H para snapshot")
        return False

    try:
        snap = _resumen_diario(nvda, "nvda")
        soxq = _load_sheet("SOXQ_1H")
        if soxq is not None:
            snap_sox = _resumen_diario(soxq, "soxq")
            snap = snap.merge(snap_sox, on="fecha", how="left", suffixes=("", "_y"))
            snap = snap[[c for c in snap.columns if not c.endswith("_y")]]

        snap["fecha"] = pd.to_datetime(snap["fecha"])

        if SNAPSHOT_CSV.exists():
            existing = pd.read_csv(SNAPSHOT_CSV)
            existing["fecha"] = pd.to_datetime(existing["fecha"])
            existing = existing[~existing["fecha"].isin(snap["fecha"])]
            combined = pd.concat([existing, snap], ignore_index=True)
        else:
            combined = snap

        combined = combined.sort_values("fecha").drop_duplicates(subset=["fecha"], keep="last")
        combined.to_csv(SNAPSHOT_CSV, index=False)
        logger.info(f"Snapshot NVDA guardado: {len(snap)} filas nuevas (total {len(combined)}) -> {SNAPSHOT_CSV.name}")
        return True
    except Exception as e:
        logger.error(f"Error guardando snapshot NVDA: {e}")
        return False


def run_snapshot() -> bool:
    backup_ok = backup_intradia_xlsx()
    snap_ok = guardar_snapshot_nvda()
    if not backup_ok:
        logger.warning("Backup intradia omitido o fallido")
    return snap_ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    run_snapshot()
