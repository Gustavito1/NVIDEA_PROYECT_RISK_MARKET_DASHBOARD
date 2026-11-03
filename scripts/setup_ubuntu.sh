#!/bin/bash
# PRECIARIO_NVDA — instalacion en Ubuntu (22.04+)
# Uso: bash scripts/setup_ubuntu.sh
set -e

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

echo "============================================================"
echo " PRECIARIO_NVDA — Setup Ubuntu"
echo "============================================================"

# 1) Python 3.11+
if command -v python3.11 >/dev/null 2>&1; then
    PY=python3.11
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    echo "ERROR: instala Python: sudo apt install python3 python3-venv python3-pip"
    exit 1
fi
echo "[1/5] Python: $($PY --version)"

# 2) venv
if [ ! -d "venv" ]; then
    echo "[2/5] Creando venv..."
    $PY -m venv venv
else
    echo "[2/5] venv ya existe"
fi
source venv/bin/activate

# 3) dependencias
echo "[3/5] Instalando requirements (puede tardar unos minutos)..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 4) directorios + .env
echo "[4/5] Preparando carpetas y .env..."
mkdir -p data core/output/data_history core/output/intradia core/output/features \
         core/output/news_history core/output/nvda_models
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "      .env creado desde .env.example (completa tus keys)"
else
    echo "      .env ya existe"
fi

# 5) smoke test
echo "[5/5] Smoke test (imports)..."
python - <<'EOF'
import core.config, core.feature_engineering, core.labels
import core.nvda_trainer, core.nvda_predictor
print("      imports OK")
EOF

echo ""
echo "Listo. Siguientes pasos:"
echo "  1. Edita .env (IBKR_HOST=127.0.0.1, IBKR_PORT=7497 paper / 7496 live)"
echo "  2. Abre TWS/IB Gateway con API habilitada"
echo "  3. source venv/bin/activate"
echo "  4. python run_nvda.py --data-only"
echo "  5. python run_nvda.py            # features + labels"
echo "  6. python run_nvda.py --train    # entrena modelos"
echo "  7. python run_nvda.py --predict --dry-run"
echo "  8. python run_nvda.py --bot --dry-run   # bot horario"
