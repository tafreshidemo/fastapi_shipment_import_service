FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml requirements.txt ./

RUN set -eux; \
    for attempt in 1 2 3; do \
        pip install \
            --no-cache-dir \
            --retries 20 \
            --timeout 100 \
            "setuptools>=69" \
            wheel \
            -r requirements.txt \
        && break; \
        if [ "$attempt" = "3" ]; then exit 1; fi; \
        sleep 5; \
    done

COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./

RUN chmod +x /app/scripts/run_migrations.sh \
    && pip install \
        --no-cache-dir \
        --no-deps \
        --no-build-isolation \
        .

RUN addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p /app/data/uploads \
    && chown -R app:app /app

USER app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
