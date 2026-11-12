# Marketing Attribution Engine — API + dashboard image
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ATTRIBUTION_DATA_DIR=/app/data/generated \
    DATA_SCALE=starter \
    DATA_SEED=20261022

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY attribution_engine/ attribution_engine/
COPY dashboard/ dashboard/
COPY scripts/ scripts/
COPY postman/ postman/
COPY tests/ tests/
COPY docs/ docs/
COPY pyproject.toml README.md ./
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh \
    && useradd --create-home --uid 10001 appuser && mkdir -p /app/data && chown -R appuser /app
USER appuser

EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)" || exit 1

ENTRYPOINT ["entrypoint.sh"]
# api (default) | dashboard | generate | test | any other command
CMD ["api"]
