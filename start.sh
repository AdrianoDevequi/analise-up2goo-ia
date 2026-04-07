#!/bin/sh
set -e

echo "[START] Inicializando banco de dados..."
python -c "from app import init_db; init_db()"

echo "[START] Iniciando servidor Gunicorn..."
exec gunicorn app:app \
  --bind "0.0.0.0:${PORT:-5000}" \
  --workers 2 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
