#!/bin/sh
set -e

echo "[START] Inicializando banco de dados..."

# Exibe qual banco será usado (ajuda a debugar no Railway)
if [ -n "$DATABASE_URL" ]; then
  echo "[START] Usando DATABASE_URL (Railway/PaaS)"
else
  echo "[START] Usando DB_HOST=${DB_HOST:-localhost}:${DB_PORT:-5432}/${DB_NAME:-analise_ia}"
fi

python -c "from app import init_db; init_db()"

echo "[START] Iniciando servidor Gunicorn..."
exec gunicorn app:app \
  --bind "0.0.0.0:${PORT:-5000}" \
  --workers 2 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
