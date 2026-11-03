# INSTALL — PRECIARIO_NVDA en Ubuntu

Guía paso a paso para tu ordenador personal (Ubuntu 22.04+).
El proyecto es portable: todas las rutas son relativas a la carpeta.

---

## 1. Requisitos

| Software | Para qué | Instalación |
|---|---|---|
| Python 3.11+ | Todo el pipeline | `sudo apt install python3 python3-venv python3-pip` |
| TWS o IB Gateway | Datos IBKR (opcional) | https://www.interactivebrokers.com/en/trading/ibgateway-latest.php |
| Git (opcional) | Versionar | `sudo apt install git` |

---

## 2. Instalación

```bash
# copia la carpeta PRECIARIO_NVDA a tu PC, luego:
cd PRECIARIO_NVDA
bash scripts/setup_ubuntu.sh
```

Esto crea `venv/`, instala `requirements.txt`, crea carpetas de salida y `.env`.

---

## 3. Configuración `.env`

```bash
nano .env
```

| Variable | Valor típico | Nota |
|---|---|---|
| `IBKR_HOST` | `127.0.0.1` | **En tu PC TWS corre local** (si usas WSL, deja que lo detecte solo) |
| `IBKR_RUNTIME` | `tws` o `gateway` | Define los puertos por defecto |
| `IBKR_PORT` | TWS: `7497` / Gateway: `4002` paper | Debe coincidir con la aplicación |
| `IBKR_INTRADIA_CLIENT_ID` | `20` | Cámbialo si otro bot usa el 20 |
| `INTRADAY_MONTHS_BACK` | `12` | 12 meses = 12 requests (~30s) |
| `NVDA_WEBHOOK_ENABLED` | `0`/`1` | Alertas a Synology u otro webhook |
| `NVDA_WEBHOOK_URL` | URL completa con token | Opcional |
| `SIGNAL_PROBA_THRESHOLD` | `0.40` | Si BUY/SELL no supera → HOLD |
| `MODEL_HORIZON_BARS` | `4` | Barras futuras del label |
| `PREDICT_STRICT_SUPPORT` | `0` | `1` = suprime señales en barras fuera del soporte de entrenamiento (barras 4-7 del día con H=4) |
| `NEWS_*` | ver `.env.example` | Sentimiento Alpha (compañero) |

### TWS instalado localmente

**TWS API**: File → Global Configuration → API → Settings:
- ✅ Enable ActiveX and Socket Clients
- Puerto `7497` (paper) / `7496` (live)
- Trusted IPs: `127.0.0.1`
- Read-Only API si solo vas a descargar datos

### IB Gateway nativo (alternativa a TWS)

IB Gateway es el cliente oficial ligero de IBKR y usa el mismo protocolo
socket que TWS. Instálalo de forma nativa desde la web de IBKR y usa el puerto
`4002` (paper) / `4001` (live). No se usa Docker ni se almacenan credenciales
de IBKR en este proyecto: TWS/IB Gateway se abre manualmente.

```bash
nc -vz 127.0.0.1 4002
python run_nvda.py --data-only
```

---

## 4. Uso diario

```bash
source venv/bin/activate

# 1) Datos 1h NVDA+SOXQ exclusivamente desde IBKR
python run_nvda.py --data-only

# 2) Features + labels
python run_nvda.py

# 3) Entrenar los 3 modelos (LogReg productivo, RF/XGB challengers)
python run_nvda.py --train

# 4) Predicción única (sin enviar alerta)
python run_nvda.py --predict --dry-run

# 5) Bot horario: cada cierre de barra RTH descarga 48h, recalcula y predice
python run_nvda.py --bot --dry-run
python run_nvda.py --bot              # con webhook si está configurado
```

---

## 5. Bot como servicio (systemd)

`/etc/systemd/system/nvda-bot.service`:

```ini
[Unit]
Description=NVDA Day Trading Bot
After=network-online.target

[Service]
User=TU_USUARIO
WorkingDirectory=/home/TU_USUARIO/PRECIARIO_NVDA
ExecStart=/home/TU_USUARIO/PRECIARIO_NVDA/venv/bin/python run_nvda.py --bot
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nvda-bot
journalctl -u nvda-bot -f
```

---

## 6. Troubleshooting

| Síntoma | Causa / solución |
|---|---|
| `NO HAY CONEXION CON IBKR` | TWS/Gateway cerrado, sesión sin iniciar o puerto distinto; no se usa otra fuente |
| `No llego nextValidId` | API deshabilitada en TWS o firewall |
| `Client ID already in use` | Cambia `IBKR_INTRADIA_CLIENT_ID` (otro bot conectado) |
| `No existe modelo entrenado` | Corre `python run_nvda.py --train` |
| `No se pudieron construir features` | Faltan datos: `python run_nvda.py --data-only` |
| `xgboost no disponible` | Opcional: `pip install xgboost` |

---

## 7. Estructura generada

```
core/output/intradia/nvda_intradia.xlsx   # NVDA_1H / SOXQ_1H
core/output/intradia/nvda_intradia.csv    # formato largo para BI
core/output/features/nvda_features.csv    # features (97 cols)
core/output/features/nvda_labeled.csv     # + label BUY/HOLD/SELL
core/output/nvda_models/                  # .pkl + metrics.json + feature_list.json
data/nvda_snapshot_horario.csv            # snapshot por barra
```
