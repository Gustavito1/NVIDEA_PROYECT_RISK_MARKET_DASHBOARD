#!/usr/bin/env python
"""
Descarga de datos intradia (velas de 1 hora) para DAY TRADING.

Activos: NVDA (target) + SOXQ (benchmark semiconductores).

Fuente única: IBKR TWS/Gateway (api socket).
  - Barras >= 1 min NO tienen limite de antiguedad de 6 meses.
  - Se solicitan en chunks de 1 mes ("1 M") y se encadenan hacia atras.
  - Pacing IBKR: max 60 requests/10 min -> chunks con sleep.

Salida:
  - core/output/intradia/nvda_intradia.xlsx (legacy, sheets NVDA_1H/SOXQ_1H)
  - core/output/intradia/NVDA_intraday.csv / SOXQ_intraday.csv
    (fuente de verdad del pipeline de features, formato UTC por simbolo)

Uso:
    python -m core.data_providers.ibkr_intraday
    python -m core.data_providers.ibkr_intraday --months 12
"""
import argparse
import logging
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
from ibapi.client import EClient
from ibapi.common import BarData
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper

from core.config import (
    IBKR_HOST,
    IBKR_INTRADIA_CLIENT_ID,
    IBKR_INTRADIA_TIMEOUT,
    IBKR_PORT,
    INTRADAY_BAR_SIZE,
    INTRADAY_DURATION_PER_REQ,
    INTRADAY_RECENT_DURATION,
    INTRADIA_CSV,
    INTRADIA_FILE,
    INTRADAY_MARKETS,
    INTRADAY_MONTHS_BACK,
    INTRADAY_USE_RTH,
    INTRADAY_WHAT_TO_SHOW,
    INTRADIA_NVDA_CSV,
    INTRADIA_SOXQ_CSV,
    IntradaySpec,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logging.getLogger("ibapi").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

LIMA_TZ = ZoneInfo("America/Lima")
CHUNK_SLEEP_SEC = 1.0

# CSV expandido por simbolo: es la fuente de verdad que lee
# core/feature_engineering.py (formato: symbol,timeframe,date,open,high,low,
# close,volume con `date` en UTC). Las descargas de IBKR se fusionan aqui para
# que el pipeline SIEMPRE se alimente de datos frescos (bug de doble fuente).
_EXPANDED_CSV_BY_SHEET = {
    "NVDA_1H": INTRADIA_NVDA_CSV,
    "SOXQ_1H": INTRADIA_SOXQ_CSV,
}


def _update_expanded_csv(sheet_name: str, df: pd.DataFrame) -> bool:
    """
    Fusiona las barras nuevas de IBKR en el CSV expandido por simbolo.

    Merge por timestamp (keep='last'): nunca pierde historial y actualiza
    barras corregidas. Devuelve True si el CSV quedo escrito.
    """
    path = _EXPANDED_CSV_BY_SHEET.get(sheet_name)
    if path is None or df is None or df.empty or "Datetime" not in df.columns:
        return False

    symbol = sheet_name.replace("_1H", "")
    new = df.copy()
    new["date"] = pd.to_datetime(new["Datetime"], utc=True)
    new = new.rename(columns={"Open": "open", "High": "high", "Low": "low",
                              "Close": "close", "Volume": "volume"})
    new = new[["date", "open", "high", "low", "close", "volume"]]
    new.insert(0, "timeframe", "intraday")
    new.insert(0, "symbol", symbol)

    try:
        if path.exists():
            old = pd.read_csv(path)
            old["date"] = pd.to_datetime(old["date"], utc=True)
            combined = pd.concat([old, new], ignore_index=True)
        else:
            combined = new
        combined = (combined.drop_duplicates(subset=["date"], keep="last")
                            .sort_values("date").reset_index(drop=True))
        combined.to_csv(path, index=False)
        logger.info(f"CSV expandido actualizado: {path.name} ({len(combined)} barras)")
        return True
    except Exception as e:
        logger.error(f"Error actualizando CSV expandido {path.name}: {e}")
        return False


def _is_wsl() -> bool:
    """True si corremos dentro de WSL (TWS en Windows, no en el mismo Linux)."""
    try:
        if Path("/proc/version").exists():
            return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        pass
    return False


def _get_cached_ibkr_host(cache_file="/tmp/ibkr_host_cache", ttl=300) -> str:
    """
    Resuelve el host de TWS/IB Gateway.

    - Linux nativo (tu Ubuntu): usa IBKR_HOST del .env (default 127.0.0.1).
    - WSL: detecta la IP de Windows via PowerShell (TWS corre en Windows).
    El resultado se cachea 5 min para no repetir la deteccion.
    """
    if not _is_wsl():
        return IBKR_HOST

    now = time.time()
    try:
        with open(cache_file, 'r') as f:
            cached_ip, cached_ts = f.read().strip().split(':')
            if now - float(cached_ts) < ttl:
                return cached_ip
    except (FileNotFoundError, ValueError, IndexError):
        pass

    ip = IBKR_HOST
    try:
        import subprocess
        ps_cmd = (
            "$ifIndex = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' | "
            "Sort-Object RouteMetric | Select-Object -First 1).ifIndex; "
            "(Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $ifIndex).IPAddress"
        )
        result = subprocess.run(
            ['powershell.exe', '-Command', ps_cmd],
            capture_output=True, text=True, timeout=8,
        )
        detected = result.stdout.strip()
        if detected:
            ip = detected
    except Exception:
        pass

    try:
        with open(cache_file, 'w') as f:
            f.write(f"{ip}:{now}")
    except OSError:
        pass
    return ip


class IBIntradayApp(EWrapper, EClient):
    """App IBKR para descarga de historicos intradia."""

    def __init__(self):
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)
        self._ready = threading.Event()
        self._next_req_id: Optional[int] = None
        self._hist: Dict[int, List[dict]] = {}
        self._hist_done: Dict[int, threading.Event] = {}
        self._errors: Dict[int, List[tuple]] = {}

    def nextValidId(self, orderId: int):
        self._next_req_id = orderId
        self._ready.set()

    def wait_ready(self, timeout: int = 10):
        if not self._ready.wait(timeout):
            raise RuntimeError("No llego nextValidId; revisa que TWS/IB Gateway tenga API habilitada.")

    def new_req_id(self) -> int:
        if self._next_req_id is None:
            raise RuntimeError("App no esta lista (sin nextValidId).")
        self._next_req_id += 1
        return self._next_req_id

    def historicalData(self, reqId: int, bar: BarData):
        if reqId not in self._hist:
            self._hist[reqId] = []
        dt_str = str(bar.date)
        try:
            if dt_str.isdigit() and len(dt_str) >= 9:
                ts_utc = pd.to_datetime(int(dt_str), unit="s", utc=True)
            else:
                ts_utc = pd.to_datetime(dt_str, utc=True)
        except Exception:
            ts_utc = pd.Timestamp.now(tz="UTC")
        self._hist[reqId].append({
            "Datetime": ts_utc,
            "Open": bar.open,
            "High": bar.high,
            "Low": bar.low,
            "Close": bar.close,
            "Volume": bar.volume,
        })

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        if reqId in self._hist_done:
            self._hist_done[reqId].set()

    def error(self, reqId: int, errorCode: int, errorString: str):
        if reqId not in self._errors:
            self._errors[reqId] = []
        self._errors[reqId].append((errorCode, errorString))
        if errorCode >= 2000:
            logger.warning(f"IBKR Warning {reqId}: {errorCode} - {errorString}")
        else:
            logger.error(f"IBKR Error {reqId}: {errorCode} - {errorString}")


def _build_contract(spec: IntradaySpec) -> Contract:
    contract = Contract()
    contract.symbol = spec.symbol
    contract.secType = spec.sec_type
    contract.exchange = spec.exchange
    contract.primaryExchange = spec.primary_exchange
    contract.currency = spec.currency
    return contract


def _fetch_chunk(app: IBIntradayApp, contract: Contract, end_datetime: str) -> Optional[pd.DataFrame]:
    """Solicita un chunk historico. end_datetime vacio = ahora (UTC strings YYYYMMDD-HH:MM:SS)."""
    req_id = app.new_req_id()
    app._hist[req_id] = []
    app._hist_done[req_id] = threading.Event()

    app.reqHistoricalData(
        req_id,
        contract,
        end_datetime,
        INTRADAY_DURATION_PER_REQ,
        INTRADAY_BAR_SIZE,
        INTRADAY_WHAT_TO_SHOW,
        INTRADAY_USE_RTH,
        2,       # formatDate=2 -> epoch
        False,   # keepUpToDate
        [],      # chartOptions
    )

    if not app._hist_done[req_id].wait(timeout=IBKR_INTRADIA_TIMEOUT):
        logger.warning(f"Timeout esperando chunk ({INTRADAY_DURATION_PER_REQ})")
        app.cancelHistoricalData(req_id)
        return None

    if req_id in app._errors and any(e[0] >= 500 for e in app._errors[req_id]):
        logger.error(f"Error critico en chunk: {app._errors[req_id][:2]}")
        return None

    data = app._hist.get(req_id, [])
    if not data:
        return None

    df = pd.DataFrame(data).sort_values("Datetime").reset_index(drop=True)
    return df


def fetch_ibkr_history(app: IBIntradayApp, spec: IntradaySpec,
                       months_back: Optional[int] = None) -> Optional[pd.DataFrame]:
    """
    Descarga months_back meses de barras encadenando chunks de INTRADAY_DURATION_PER_REQ.
    Cada chunk se pide terminando 1 segundo antes del primer bar del chunk anterior.
    """
    months_back = months_back or INTRADAY_MONTHS_BACK
    logger.info(f"[{spec.symbol}] Descargando ~{months_back} meses de barras {INTRADAY_BAR_SIZE}...")

    contract = _build_contract(spec)
    chunks: List[pd.DataFrame] = []
    end_datetime = ""
    months_done = 0
    max_chunks = months_back + 3

    while months_done < months_back and len(chunks) < max_chunks:
        df_chunk = _fetch_chunk(app, contract, end_datetime)
        if df_chunk is None or df_chunk.empty:
            break

        chunks.append(df_chunk)
        months_done += 1
        logger.info(f"  [{spec.symbol}] chunk {months_done}: {len(df_chunk)} barras "
                    f"({df_chunk['Datetime'].min()} -> {df_chunk['Datetime'].max()})")

        earliest = df_chunk["Datetime"].min().tz_convert("UTC")
        end_datetime = (earliest - pd.Timedelta(seconds=1)).strftime("%Y%m%d-%H:%M:%S")
        time.sleep(CHUNK_SLEEP_SEC)

    if not chunks:
        return None

    df_all = pd.concat(chunks, ignore_index=True)
    df_all = df_all.drop_duplicates(subset=["Datetime"]).sort_values("Datetime").reset_index(drop=True)
    logger.info(f"[{spec.symbol}] Total IBKR: {len(df_all)} barras")
    return df_all


def fetch_ibkr_recent(app: IBIntradayApp, spec: IntradaySpec) -> Optional[pd.DataFrame]:
    """Descarga solo las ultimas 48h de barras (para el bot horario)."""
    contract = _build_contract(spec)
    req_id = app.new_req_id()
    app._hist[req_id] = []
    app._hist_done[req_id] = threading.Event()

    app.reqHistoricalData(
        req_id, contract, "", INTRADAY_RECENT_DURATION, INTRADAY_BAR_SIZE,
        INTRADAY_WHAT_TO_SHOW, INTRADAY_USE_RTH, 2, False, [],
    )

    if not app._hist_done[req_id].wait(timeout=IBKR_INTRADIA_TIMEOUT):
        logger.warning(f"Timeout esperando datos recientes de {spec.symbol}")
        app.cancelHistoricalData(req_id)
        return None

    data = app._hist.get(req_id, [])
    if not data:
        return None
    return pd.DataFrame(data).sort_values("Datetime").reset_index(drop=True)


def get_latest_intradia_data() -> Dict[str, pd.DataFrame]:
    """Carga los ultimos datos intradia desde el Excel."""
    if not INTRADIA_FILE.exists():
        return {}
    try:
        all_data = {}
        with pd.ExcelFile(INTRADIA_FILE) as excel:
            for sheet_name in excel.sheet_names:
                df = pd.read_excel(excel, sheet_name=sheet_name)
                if 'Datetime' in df.columns:
                    df['Datetime'] = pd.to_datetime(df['Datetime'], utc=True)
                all_data[sheet_name] = df
        return all_data
    except Exception as e:
        logger.error(f"Error cargando datos intradia: {e}")
        return {}


def _save_intradia(all_data: Dict[str, pd.DataFrame], existing_data: Dict[str, pd.DataFrame]) -> bool:
    """Guarda Excel con verificacion de seguridad para no perder historial."""
    if not all_data:
        logger.warning("No se obtuvieron datos - manteniendo Excel existente")
        return False

    has_data = any(len(df) > 0 for df in all_data.values())
    if not has_data:
        logger.warning("Todos los sheets estan vacios - manteniendo Excel existente")
        return False

    if existing_data:
        for sheet_name, df_new in all_data.items():
            if sheet_name in existing_data:
                existing_count = len(existing_data[sheet_name])
                new_count = len(df_new)
                if new_count < existing_count * 0.5:
                    logger.error(
                        f"ALERTA: {sheet_name} perdio {existing_count - new_count} registros "
                        f"({existing_count} -> {new_count}). Manteniendo datos existentes."
                    )
                    return False

    with pd.ExcelWriter(INTRADIA_FILE, engine='openpyxl') as writer:
        csv_frames = []
        for sheet_name, df in all_data.items():
            # Primero se fusiona en el CSV expandido (fuente de verdad del
            # pipeline de features) con los Datetime originales en UTC.
            _update_expanded_csv(sheet_name, df)

            df = df.copy()
            if 'Datetime' in df.columns:
                df['Datetime'] = (
                    pd.to_datetime(df['Datetime'], utc=True)
                    .dt.tz_convert(LIMA_TZ)
                    .dt.strftime('%Y-%m-%d %H:%M:%S-05:00')
                )
            df.to_excel(writer, sheet_name=sheet_name, index=False)

            csv_df = df.copy()
            csv_df.insert(1, 'symbol', sheet_name.replace('_1H', ''))
            csv_frames.append(csv_df)

    if csv_frames:
        csv_all = pd.concat(csv_frames, ignore_index=True)
        csv_all = csv_all.sort_values(['symbol', 'Datetime']).reset_index(drop=True)
        csv_all.to_csv(INTRADIA_CSV, index=False)
        logger.info(f"CSV guardado en: {INTRADIA_CSV} ({len(csv_all)} filas)")

    logger.info(f"Datos guardados en: {INTRADIA_FILE}")
    return True


def run_intraday_update(months_back: Optional[int] = None,
                        symbols: Optional[List[str]] = None,
                        recent: bool = False) -> bool:
    """
    Actualiza datos intradia NVDA/SOXQ combinando con historial existente.

    Args:
        months_back: meses a descargar (solo si recent=False)
        symbols: filtrar simbolos (default todos)
        recent: solo ultimas 48h (modo bot horario, 2 requests)

    Returns:
        True si guardo datos; False si no pudo.
    """
    mode = "RECIENTE (2 D)" if recent else f"HISTORICO ~{months_back or INTRADAY_MONTHS_BACK} meses"
    logger.info("=" * 60)
    logger.info(f"ACTUALIZANDO DATOS INTRADIA {mode} ({INTRADAY_BAR_SIZE})")
    logger.info("=" * 60)

    markets = [m for m in INTRADAY_MARKETS if m.enabled]
    if symbols:
        markets = [m for m in markets if m.symbol.upper() in {s.upper() for s in symbols}]

    existing_data = get_latest_intradia_data()
    all_data: Dict[str, pd.DataFrame] = {}

    host = _get_cached_ibkr_host()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, IBKR_PORT))
        sock.close()
    except Exception as e:
        logger.warning(f"Error verificando conexion TWS: {e}")
        result = -1

    if result != 0:
        logger.error(
            f"NO HAY CONEXION CON IBKR en {host}:{IBKR_PORT}. "
            "No se usara ninguna fuente alternativa."
        )
        return False

    app = IBIntradayApp()
    try:
        app.connect(host, IBKR_PORT, IBKR_INTRADIA_CLIENT_ID)
        thread = threading.Thread(target=app.run, daemon=True)
        thread.start()
        app.wait_ready(timeout=10)
        logger.info(f"Conectado a IBKR ({host}:{IBKR_PORT})")
    except Exception as e:
        logger.error(f"Error conectando a IBKR: {e}")
        return False

    try:
        for market in markets:
            if recent:
                df_new = fetch_ibkr_recent(app, market)
            else:
                df_new = fetch_ibkr_history(app, market, months_back)
            if df_new is not None and not df_new.empty:
                all_data[market.sheet] = _merge_sheet(existing_data.get(market.sheet), df_new)
            time.sleep(1)

        app.disconnect()

        expected_sheets = {market.sheet for market in markets}
        missing_sheets = expected_sheets - set(all_data)
        if missing_sheets:
            logger.error(
                "IBKR no devolvio todos los activos; no se sobrescribiran datos: "
                f"faltan {sorted(missing_sheets)}"
            )
            return False

        if not all_data:
            logger.warning("No se obtuvieron datos - manteniendo Excel existente")
            return False

        return _save_intradia(all_data, existing_data)

    except Exception as e:
        logger.error(f"Error en actualizacion intradia: {e}")
        try:
            app.disconnect()
        except Exception:
            pass
        return False


def _merge_sheet(existing: Optional[pd.DataFrame], df_new: pd.DataFrame) -> pd.DataFrame:
    """Combina historial existente con datos nuevos, deduplicando por Datetime."""
    if existing is not None and not existing.empty and 'Datetime' in existing.columns:
        df_existing = existing.copy()
        df_existing['Datetime'] = pd.to_datetime(df_existing['Datetime'], utc=True)
        if df_new['Datetime'].dt.tz is None:
            df_new = df_new.copy()
            df_new['Datetime'] = df_new['Datetime'].dt.tz_localize('UTC')
        df_combined = pd.concat([df_existing, df_new], ignore_index=True)
        df_combined = df_combined.drop_duplicates(subset=['Datetime'], keep='last')
        return df_combined.sort_values('Datetime').reset_index(drop=True)
    df_new = df_new.copy()
    if df_new['Datetime'].dt.tz is None:
        df_new['Datetime'] = df_new['Datetime'].dt.tz_localize('UTC')
    return df_new


def main():
    parser = argparse.ArgumentParser(description="Descarga intradia NVDA/SOXQ (1h)")
    parser.add_argument("--months", type=int, default=None,
                        help=f"Meses hacia atras (default: {INTRADAY_MONTHS_BACK})")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Lista separada por comas. Ej: NVDA,SOXQ")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None
    ok = run_intraday_update(months_back=args.months, symbols=symbols)
    print("OK" if ok else "FALLO")


if __name__ == "__main__":
    main()
