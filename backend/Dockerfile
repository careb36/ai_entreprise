FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi==0.109.0 \
    uvicorn[standard]==0.27.0 \
    langgraph==0.0.20 \
    langchain-core==0.1.10 \
    nats-py==2.3.1 \
    neo4j==5.15.0 \
    psycopg2-binary==2.9.9 \
    pydantic==2.5.3 \
    pydantic-settings==2.1.0 \
    python-dotenv==1.0.0 \
    requests==2.31.0 \
    sse-starlette==1.8.2 \
    aiofiles==23.2.1 \
    numpy==1.26.4 \
    passlib[bcrypt]==1.7.4 \
    httpx==0.27.0

COPY app/ /app/

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
