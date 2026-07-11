FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/src ./backend/src
COPY agent-runtime/pyproject.toml ./agent-runtime/pyproject.toml
COPY agent-runtime/src ./agent-runtime/src

RUN pip install --upgrade pip \
    && pip install ./backend \
    && pip install ./agent-runtime

USER app

CMD ["python", "-m", "agent_runtime.handler"]
