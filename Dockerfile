FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --system signbridge && useradd --system --gid signbridge signbridge
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

ARG INSTALL_MEDIAPIPE=false
RUN if [ "$INSTALL_MEDIAPIPE" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends libegl1 libgles2 \
        && rm -rf /var/lib/apt/lists/* \
        && python -m pip install --no-cache-dir 'absl-py~=2.3' 'flatbuffers~=25.9' 'matplotlib>=3.10,<4' 'sounddevice~=0.5' \
        && python -m pip install --no-cache-dir --no-deps mediapipe==0.10.35; \
    fi

COPY alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY tests ./tests

USER signbridge
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --ws websockets --ws-max-size 98304 --ws-max-queue 1 --no-access-log"]
