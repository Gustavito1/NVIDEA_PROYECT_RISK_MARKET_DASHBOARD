#!/usr/bin/env python
"""
Orquestador PRECIARIO_NVDA — DAY TRADING (velas 1 hora).

Modos:
    python run_nvda.py                  # Flujo completo una vez
    python run_nvda.py --data-only      # Solo descargar intradia
    python run_nvda.py --schedule       # Loop continuo (cron / systemd)
    python run_nvda.py --inspect        # Diagnostico de datos descargados

Flujo:
    PASO 1: Intradia IBKR (NVDA + SOXQ, 1h, ~6 meses)
    PASO 2: Feature engineering (tecnicos + sesion + SOX + patrones)
    PASO 3: Labels BUY/HOLD/SELL day trading
    PASO 4: Snapshot + backup
    PASO 5: Prediccion (pendiente: modelo RF se entrena en tu PC)
"""
import argparse
import logging
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).parent

log_file = BASE_DIR / 'run_nvda.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(str(log_file), encoding='utf-8',
                            maxBytes=5 * 1024 * 1024, backupCount=2),
    ],
)
logger = logging.getLogger(__name__)


def step_1_intradia(months: int = None) -> bool:
    logger.info("=" * 60)
    logger.info("PASO 1: Descarga intradia (1h) NVDA + SOXQ")
    logger.info("=" * 60)
    try:
        from core.data_providers.ibkr_intraday import run_intraday_update
        ok = run_intraday_update(months_back=months)
        if ok:
            logger.info("Intradia actualizado correctamente")
        else:
            logger.error("NO HAY CONEXION CON IBKR o no llegaron datos. No se usaran datos alternativos.")
        return ok
    except Exception as e:
        logger.error(f"Error en paso 1: {e}")
        return False


def step_2_features(include_patterns: bool = False):
    """Construye features. Devuelve el DataFrame (None si falla) para reutilizarlo."""
    logger.info("=" * 60)
    logger.info("PASO 2: Feature engineering intradia")
    logger.info("=" * 60)
    try:
        from core.feature_engineering import build_features
        from core.config import FEATURES_CSV
        df = build_features(include_patterns=include_patterns, save=True)
        if df is None:
            logger.error("No se generaron features (faltan datos intradia)")
            return None
        logger.info(f"Features: {len(df)} filas x {len(df.columns)} columnas -> {FEATURES_CSV}")
        return df
    except Exception as e:
        logger.error(f"Error en paso 2: {e}")
        import traceback
        traceback.print_exc()
        return None


def step_3_labels() -> bool:
    logger.info("=" * 60)
    logger.info("PASO 3: Labels BUY/HOLD/SELL (day trading)")
    logger.info("=" * 60)
    try:
        from core.labels import build_labels
        from core.config import LABELED_CSV
        df = build_labels()
        if df is None:
            logger.error("No se generaron labels (faltan features)")
            return False
        logger.info(f"Dataset etiquetado -> {LABELED_CSV}")
        return True
    except Exception as e:
        logger.error(f"Error en paso 3: {e}")
        import traceback
        traceback.print_exc()
        return False


def step_4_snapshot() -> bool:
    logger.info("=" * 60)
    logger.info("PASO 4: Snapshot y backup intradia")
    logger.info("=" * 60)
    try:
        from core.nvda_snapshot import run_snapshot
        ok = run_snapshot()
        if ok:
            logger.info("Snapshot/backup completado")
        else:
            logger.warning("Snapshot/backup con advertencias")
        return ok
    except Exception as e:
        logger.error(f"Error en paso 4: {e}")
        return False


def step_5_predict(dry_run: bool = False, model_name: str = "logreg",
                   features_df=None) -> dict:
    """
    Predice sobre la ultima barra cerrada.

    Devuelve {"status": "ok"|"sin_modelo"|"error", "barra": iso|None,
    "pred": dict|None} para que el caller pueda deduplicar por barra predicha
    y distinguir "sin modelo entrenado" de un error real.
    """
    from core.nvda_predictor import ModelNotTrainedError, predict_latest, format_signal_message

    logger.info("=" * 60)
    logger.info("PASO 5: Prediccion sobre la ultima barra cerrada")
    logger.info("=" * 60)
    try:
        pred = predict_latest(model_name=model_name, features_df=features_df)
        logger.info("\n" + format_signal_message(pred))

        from core.notifications import send_signal
        send_signal(pred, dry_run=dry_run)

        return {"status": "ok", "barra": pred["datetime_barra"], "pred": pred}
    except ModelNotTrainedError:
        logger.info("Aun no existe modelo entrenado. Entrena con: python run_nvda.py --train")
        return {"status": "sin_modelo", "barra": None, "pred": None}
    except Exception as e:
        logger.error(f"Error en paso 5: {e}")
        return {"status": "error", "barra": None, "pred": None}


def _datos_desde_db() -> bool:
    """True si la fuente de datos activa es la DB remota (Aiven/ibsync)."""
    from core.config import DATA_SOURCE
    if DATA_SOURCE == "csv":
        return False
    if DATA_SOURCE == "db":
        return True
    from core.data_providers.db_source import ping
    return ping()


def _hay_datos_locales() -> bool:
    """True si existen datos intradia con los que trabajar sin IBKR."""
    from core.config import INTRADIA_FILE, INTRADIA_NVDA_CSV, INTRADIA_SOXQ_CSV
    return (INTRADIA_NVDA_CSV.exists() or INTRADIA_FILE.exists()) and \
           (INTRADIA_SOXQ_CSV.exists() or INTRADIA_FILE.exists())


def run_pipeline(args) -> int:
    start_time = datetime.now()
    logger.info("INICIANDO PIPELINE NVDA DAY TRADING")
    logger.info(f"Hora inicio: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")

    steps = []
    feat_df = None

    if not args.skip_intradia:
        if _datos_desde_db():
            logger.info("PASO 1 omitido: los datos OHLCV vienen de la DB remota (ibsync en Docker)")
            steps.append(("Intradia", "OK (DB)"))
        else:
            intradia_ok = step_1_intradia(months=args.months)
            steps.append(("Intradia", "OK" if intradia_ok else "ERROR"))
            if not intradia_ok:
                if _hay_datos_locales():
                    logger.warning(
                        "IBKR no disponible, pero hay datos intradia locales: "
                        "el pipeline continua con los ultimos datos guardados."
                    )
                else:
                    logger.error("Flujo detenido: sin IBKR y sin datos intradia locales.")
                    return 1
    if not args.skip_features:
        feat_df = step_2_features(include_patterns=args.patterns)
        steps.append(("Features", "OK" if feat_df is not None else "ERROR"))
    if not args.skip_labels:
        steps.append(("Labels", "OK" if step_3_labels() else "ERROR"))
    if not args.skip_snapshot:
        steps.append(("Snapshot", "OK" if step_4_snapshot() else "ERROR"))
    if not args.skip_predict:
        res = step_5_predict(dry_run=args.dry_run, model_name=args.model,
                             features_df=feat_df)
        steps.append(("Prediccion",
                      {"ok": "OK", "sin_modelo": "SIN MODELO", "error": "ERROR"}[res["status"]]))

    duration = (datetime.now() - start_time).total_seconds()
    logger.info("")
    logger.info("=" * 60)
    logger.info("RESUMEN")
    logger.info("=" * 60)
    for name, estado in steps:
        logger.info(f"  {name}: {estado}")
    logger.info(f"Duracion: {duration:.1f}s")

    return 0 if all(estado != "ERROR" for _, estado in steps) else 1


def run_scheduled(args) -> None:
    interval = args.interval
    logger.info(f"MODO PROGRAMADO - cada {interval} minuto(s). Ctrl+C para detener.")
    while True:
        try:
            run_pipeline(args)
        except KeyboardInterrupt:
            logger.info("Detenido por usuario")
            break
        except Exception as e:
            logger.error(f"Error en ciclo programado: {e}")
        time.sleep(interval * 60)


def run_bot(args) -> None:
    """
    BOT HORARIO: en cada cierre de barra RTH (minuto 0-5 de cada hora)
    actualiza datos, recalcula features y emite la senal.

    - Solo dias de semana (sin festivos NASDAQ) y horario de mercado NY.
    - Dedupe por BARRA PREDICHA (no por reloj): evita senales duplicadas en
      cierres anticipados o con feed retrasado.
    - La ultima barra del dia (cierre 16:00 ET) admite reintentos hasta el
      minuto BOT_RUN_MINUTE_WINDOW-1.
    - --dry-run no envia webhook.
    """
    from zoneinfo import ZoneInfo
    from core.config import BOT_RUN_MINUTE_WINDOW, BOT_POLL_SECONDS, NASDAQ_HOLIDAYS
    from core.labels import bar_end_time

    ny_tz = ZoneInfo("America/New_York")
    logger.info(f"BOT NVDA iniciado (poll {BOT_POLL_SECONDS}s, dry_run={args.dry_run})")
    last_bar_key = None
    last_predicted_bar = None

    while True:
        try:
            now = datetime.now(ny_tz)
            es_semana = now.weekday() < 5
            es_festivo = now.strftime("%Y-%m-%d") in NASDAQ_HOLIDAYS
            # La ultima barra cierra a las 16:00: se admite la ventana completa
            # hasta (16, WINDOW-1) para poder reintentar el cierre del dia.
            en_sesion = (9, 30) <= (now.hour, now.minute) <= (16, BOT_RUN_MINUTE_WINDOW - 1)
            en_ventana = now.minute < BOT_RUN_MINUTE_WINDOW

            if es_semana and not es_festivo and en_sesion and en_ventana:
                bar_key = now.strftime("%Y-%m-%d %H:00")
                if bar_key != last_bar_key:
                    logger.info("=" * 60)
                    logger.info(f"CIERRE DE BARRA ~{bar_key} ET - actualizando y prediciendo")
                    logger.info("=" * 60)

                    if _datos_desde_db():
                        logger.info("Datos frescos desde la DB remota (descarga IBKR omitida)")
                        updated = True
                    else:
                        from core.data_providers.ibkr_intraday import run_intraday_update
                        updated = run_intraday_update(recent=True)
                    if not updated:
                        logger.warning(
                            "No se ejecuto prediccion: no llego una actualizacion de datos "
                            "(se reintentara mientras siga la ventana de la barra)"
                        )
                        time.sleep(BOT_POLL_SECONDS)
                        continue

                    feat_df = step_2_features(include_patterns=args.patterns)
                    res = step_5_predict(dry_run=args.dry_run, model_name=args.model,
                                         features_df=feat_df) if feat_df is not None \
                        else {"status": "error", "barra": None}

                    if res["status"] == "ok":
                        if res["barra"] == last_predicted_bar:
                            logger.info(
                                f"Barra {res['barra']} ya predicha antes: "
                                "senal duplicada evitada (feed posiblemente desfasado)"
                            )
                        else:
                            last_predicted_bar = res["barra"]
                            # Aviso de frescura: si la barra predicha ya es vieja,
                            # los datos estan desfasados y la senal es stale.
                            antiguedad = (datetime.now(ny_tz) -
                                          bar_end_time(res["barra"]).tz_convert(ny_tz)).total_seconds() / 60
                            if antiguedad > 90:
                                logger.warning(
                                    f"Datos desfasados: la barra predicha ({res['barra']}) "
                                    f"cerro hace {antiguedad:.0f} min"
                                )
                        last_bar_key = bar_key
                    elif res["status"] == "sin_modelo":
                        logger.info("Sin modelo entrenado: no se reintenta esta barra")
                        last_bar_key = bar_key
                    else:
                        logger.warning(
                            f"Barra {bar_key} incompleta (features/prediccion fallaron): "
                            "se reintentara en esta misma ventana"
                        )

        except KeyboardInterrupt:
            logger.info("Bot detenido por usuario")
            break
        except Exception as e:
            logger.error(f"Error en bot: {e}")
        time.sleep(BOT_POLL_SECONDS)


def step_train(args) -> bool:
    logger.info("=" * 60)
    logger.info("ENTRENAMIENTO DE MODELOS (LogReg / RF / XGB)")
    logger.info("=" * 60)
    try:
        import subprocess
        cmd = [sys.executable, "-m", "core.nvda_trainer",
               "--folds", str(args.folds), "--feature-set", args.feature_set]
        if args.final_model:
            cmd += ["--final-model", args.final_model]
        result = subprocess.run(cmd, cwd=str(BASE_DIR))
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Error entrenando: {e}")
        return False


def inspect_data() -> int:
    """Diagnostico rapido de los datos intradia (CSVs expandidos o Excel), features y labels."""
    from core.config import FEATURES_CSV, INTRADIA_FILE, LABELED_CSV
    from core.feature_engineering import load_intradia_sheet
    import pandas as pd

    hay_datos = False
    for sheet in ("NVDA_1H", "SOXQ_1H"):
        df = load_intradia_sheet(sheet)
        if df is None or df.empty:
            print(f"\n=== {sheet}: SIN DATOS ===")
            continue
        hay_datos = True
        df["Datetime"] = pd.to_datetime(df["Datetime"], utc=True)
        dias = df["Datetime"].dt.date.nunique()
        barras_dia = df.groupby(df["Datetime"].dt.date).size()
        print(f"\n=== {sheet} ({len(df)} barras) ===")
        print(f"  rango: {df['Datetime'].min()} -> {df['Datetime'].max()}")
        print(f"  dias: {dias} | barras/dia min={barras_dia.min()} max={barras_dia.max()} "
              f"mediana={int(barras_dia.median())}")

    if not hay_datos:
        print(f"No hay datos intradia (ni CSVs expandidos ni {INTRADIA_FILE.name}).")
        print("Corre: python run_nvda.py --data-only")
        return 1

    for path, nombre in [(FEATURES_CSV, "features"), (LABELED_CSV, "labeled")]:
        if path.exists():
            df = pd.read_csv(path)
            print(f"\n=== {nombre}: {len(df)} filas x {len(df.columns)} columnas ===")
            if "label" in df.columns:
                print(df["label"].value_counts().to_string())
            if "bar_of_day" in df.columns:
                print("  barras del dia presentes (soporte de entrenamiento):")
                print("  " + df["bar_of_day"].value_counts().sort_index().to_string().replace("\n", "\n  "))
    return 0


def main():
    parser = argparse.ArgumentParser(description="PRECIARIO_NVDA - Day Trading (1h)")
    parser.add_argument("--schedule", action="store_true", help="Ejecucion continua")
    parser.add_argument("--bot", action="store_true", help="Bot horario (cada cierre de barra RTH)")
    parser.add_argument("--train", action="store_true", help="Entrenar modelos (LogReg/RF/XGB)")
    parser.add_argument("--predict", action="store_true", help="Predecir con el modelo entrenado")
    parser.add_argument("--dry-run", action="store_true", help="No enviar webhook (solo log)")
    parser.add_argument("--folds", type=int, default=5, help="Folds walk-forward para entrenar")
    parser.add_argument("--final-model", choices=["logreg", "rf", "xgb"], default="logreg")
    parser.add_argument("--model", choices=["logreg", "rf", "xgb"], default="logreg",
                        help="Modelo a usar en --predict/--bot (default logreg)")
    parser.add_argument(
        "--feature-set",
        choices=["A_nvda_only", "B_nvda_sox", "C_nvda_news", "D_nvda_sox_news"],
        default="D_nvda_sox_news",
        help="Variables finales: NVDA, SOX, noticias o todos",
    )
    parser.add_argument("--interval", type=int, default=60, help="Minutos entre ciclos (default 60)")
    parser.add_argument("--months", type=int, default=None, help="Meses de historial intradia")
    parser.add_argument("--data-only", action="store_true", help="Solo descargar intradia")
    parser.add_argument("--inspect", action="store_true", help="Diagnostico de datos")
    parser.add_argument("--patterns", action="store_true",
                        help="Calcular los 57 patrones (no usados por el modelo; lento)")

    parser.add_argument("--skip-intradia", action="store_true")
    parser.add_argument("--skip-features", action="store_true")
    parser.add_argument("--skip-labels", action="store_true")
    parser.add_argument("--skip-snapshot", action="store_true")
    parser.add_argument("--skip-predict", action="store_true")

    args = parser.parse_args()

    if args.inspect:
        return inspect_data()

    if args.train:
        return 0 if step_train(args) else 1

    if args.predict:
        res = step_5_predict(dry_run=args.dry_run, model_name=args.model)
        return 0 if res["status"] != "error" else 1

    if args.bot:
        run_bot(args)
        return 0

    if args.data_only:
        args.skip_features = True
        args.skip_labels = True
        args.skip_snapshot = True
        args.skip_predict = True

    if args.schedule:
        run_scheduled(args)
        return 0

    return run_pipeline(args)


if __name__ == "__main__":
    sys.exit(main())
