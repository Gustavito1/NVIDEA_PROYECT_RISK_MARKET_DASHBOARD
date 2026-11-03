"""
Notificaciones de senales NVDA (Synology Chat u otro webhook compatible).

Configuracion en .env:
    NVDA_WEBHOOK_ENABLED=1
    NVDA_WEBHOOK_URL=https://tu-nas:5001/webapi/entry.cgi?api=...&token=...
"""
import json
import logging
from typing import Dict

import requests

from core.config import NVDA_WEBHOOK_ENABLED, NVDA_WEBHOOK_URL

logger = logging.getLogger(__name__)


def send_webhook(message: str) -> bool:
    """Envia mensaje al webhook configurado. Si esta deshabilitado, solo loguea."""
    if not NVDA_WEBHOOK_ENABLED or not NVDA_WEBHOOK_URL:
        logger.info("[webhook deshabilitado] " + message.replace("\n", " | "))
        return False

    payload = json.dumps({"text": message})
    try:
        response = requests.post(NVDA_WEBHOOK_URL, data={"payload": payload},
                                 timeout=15, verify=False)
        response.raise_for_status()
        logger.info("Alerta enviada al webhook")
        return True
    except Exception as e:
        logger.error(f"Error enviando webhook: {e}")
        return False


def send_signal(pred: Dict, dry_run: bool = False) -> bool:
    """Envia la senal formateada. dry_run=True imprime sin enviar."""
    from core.nvda_predictor import format_signal_message

    message = format_signal_message(pred)
    if dry_run:
        logger.info("[DRY-RUN] Senal que se enviaria:\n" + message)
        return True
    return send_webhook(message)
