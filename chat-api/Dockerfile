# syntax=docker/dockerfile:1.6
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
# Bake the agent system prompts into the image so the chat-api can call the
# model directly with the same persona as the deployed Foundry agents.
COPY agents_prompts ./agents_prompts

RUN pip install --upgrade pip build && \
    pip wheel --wheel-dir /wheels .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels chat-api && rm -rf /wheels

USER app
EXPOSE 8080

CMD ["sh", "-c", "uvicorn chat_api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
