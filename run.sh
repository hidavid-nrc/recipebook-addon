#!/usr/bin/with-contenv bashio

export ANTHROPIC_API_KEY=$(bashio::config 'anthropic_api_key')
export OPENAI_API_KEY=$(bashio::config 'openai_api_key')
export BRING_ENTITY=$(bashio::config 'bring_entity')

# Durable storage: DB lives in /share (survives add-on uninstall),
# daily backups go to /backup (captured by HA's backup system).
export DATA_DIR="/share/recipebook"
export BACKUP_DIR="/backup/recipebook"
export PORT=8000
VERSION="${APP_VERSION:-0.6.0}"

mkdir -p "${DATA_DIR}" "${BACKUP_DIR}"

# One-time migration: move the DB out of the volatile /data into /share.
if [ ! -f "${DATA_DIR}/recipes.db" ] && [ -f /data/recipes.db ]; then
  cp -a /data/recipes.db        "${DATA_DIR}/recipes.db"        2>/dev/null || true
  cp -a /data/recipes.db-wal    "${DATA_DIR}/recipes.db-wal"    2>/dev/null || true
  cp -a /data/recipes.db-shm    "${DATA_DIR}/recipes.db-shm"    2>/dev/null || true
  bashio::log.info "Migrated existing DB from /data to ${DATA_DIR}"
fi

# SUPERVISOR_TOKEN is auto-injected by HA when homeassistant_api: true
bashio::log.info "Starting Recipe Book v${VERSION} (data: ${DATA_DIR})"
cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --workers 1
