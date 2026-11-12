#!/bin/sh
# Generates the dataset on first start (if missing), then runs the requested service.
set -e
DB="${ATTRIBUTION_DATA_DIR}/attribution.db"

ensure_data() {
  if [ ! -f "$DB" ]; then
    echo "[entrypoint] no dataset at $DB - generating scale=${DATA_SCALE} seed=${DATA_SEED}"
    python -m attribution_engine.cli generate --scale "${DATA_SCALE}" --seed "${DATA_SEED}" --output "${ATTRIBUTION_DATA_DIR}"
  fi
}

case "$1" in
  api)
    ensure_data
    exec uvicorn attribution_engine.api:app --host 0.0.0.0 --port 8000 ;;
  dashboard)
    ensure_data
    exec streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true ;;
  generate)
    shift
    exec python -m attribution_engine.cli generate --output "${ATTRIBUTION_DATA_DIR}" "$@" ;;
  test)
    exec python -m pytest -q ;;
  *)
    exec "$@" ;;
esac
