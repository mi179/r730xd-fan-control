FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apk add --no-cache ca-certificates ipmitool \
    && addgroup -S -g 10001 r730xd \
    && adduser -S -D -H -u 10001 -G r730xd -h /app r730xd \
    # Telemetry volume mountpoint. It must exist in the image and be owned by
    # the runtime UID, otherwise Docker creates the named volume root-owned and
    # the non-root process cannot write telemetry.db.
    && mkdir -p /data \
    && chown 10001:10001 /data

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=r730xd:r730xd app.py ./app.py
COPY --chown=r730xd:r730xd templates ./templates
COPY --chown=r730xd:r730xd static ./static

USER 10001:10001
EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
