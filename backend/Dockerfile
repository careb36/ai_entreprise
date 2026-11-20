FROM python:3.12-slim

WORKDIR /app

# Install system dependencies. Pango (which brings HarfBuzz) and the DejaVu
# fonts (Latin with Spanish, Greek, Cyrillic; sans, serif and mono) are what
# WeasyPrint needs to write the documentation pack as PDF (issue #694,
# docs/design/PROJECT_DOCUMENTATION.md, "Export to DOCX and PDF"); they come
# from the distribution, so the image's SBOM and vulnerability scan cover them.
# HarfBuzz's subsetter (libharfbuzz-subset0) is left out on purpose: without it
# WeasyPrint subsets fonts with fontTools, the path validated with veraPDF and
# reproducible under SOURCE_DATE_EPOCH. Without these packages only the PDF
# export is unavailable, with the reason named.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

# Copy dependency manifests first for layer caching — code changes won't
# invalidate the pip install layer as long as these files don't change.
COPY pyproject.toml poetry.lock* /app/

# Install only production dependencies; skip dev extras and the root package
# itself (no installable package here, just application code).
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root --only main

# Copy only the application source trees needed at runtime.
# Avoids copying .git, node_modules, frontend/, docs/, tests/, etc.
COPY app/     /app/app/
COPY telemetry.py /app/telemetry.py
COPY alembic.ini /app/alembic.ini
COPY alembic/  /app/alembic/

EXPOSE 8000

# /app      — repo root (pyproject.toml, poetry.lock live here)
# /app/app  — FastAPI application package (routers, models, services, …)
ENV PYTHONPATH="/app:/app/app"

# The schema first: the migrations to head (backend/alembic/), then the app,
# which refuses a database behind head. uvicorn's own X-Forwarded-For handling
# is off: the app decides which proxies to believe (TRUSTED_PROXY_IPS,
# security.TrustedProxyMiddleware), so there is one rule, not two.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers"]
