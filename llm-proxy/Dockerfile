FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

# Copy poetry files
COPY pyproject.toml poetry.lock* /app/

# Install dependencies
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root

# Install runtime helpers required by gunicorn and OpenTelemetry instrumentation.
RUN pip install --no-cache-dir gunicorn 'setuptools<85'

# app.py imports every one of these as a sibling module; copying only app.py
# and usage.py boots gunicorn into `ModuleNotFoundError: No module named
# 'upstream'` and the container dies before serving a single request.
# A new top-level module in llm-proxy/ has to be added here too.
COPY app.py usage.py upstream.py catalogue.py provider_keys.py telemetry.py /app/

EXPOSE 5100

CMD ["gunicorn", "-b", "0.0.0.0:5100", "-w", "2", "--timeout", "120", "app:app"]
