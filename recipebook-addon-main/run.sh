#!/usr/bin/with-contenv bashio

export ANTHROPIC_API_KEY=$(bashio::config 'anthropic_api_key')
export OPENAI_API_KEY=$(bashio::config 'openai_api_key')
export BRING_ENTITY=$(bashio::config 'bring_entity')
export DATA_DIR="/data"
export PORT=8000

# SUPERVISOR_TOKEN is auto-injected by HA when homeassistant_api: true
bashio::log.info "Starting Recipe Book v0.3.0..."
cd /app
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --workers 1
