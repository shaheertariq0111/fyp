FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/src ./backend/src
COPY agent-runtime/pyproject.toml ./agent-runtime/pyproject.toml
COPY agent-runtime/constraints.txt ./agent-runtime/constraints.txt
COPY agent-runtime/src ./agent-runtime/src

RUN pip install --upgrade pip \
    && pip install -c ./agent-runtime/constraints.txt ./backend \
    && pip install -c ./agent-runtime/constraints.txt ./agent-runtime

USER app

EXPOSE 8080

CMD ["uvicorn", "agent_runtime.server:app", "--host", "0.0.0.0", "--port", "8080"]
