FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    fastapi>=0.115 \
    "uvicorn[standard]>=0.32" \
    "pydantic>=2.9" \
    "pydantic-settings>=2.6" \
    "asyncpg>=0.30" \
    "pgvector>=0.3" \
    "httpx>=0.27" \
    "python-dotenv>=1.0" \
    "langchain-anthropic>=0.3" \
    "langchain-openai>=0.2" \
    "langgraph>=0.2" \
    "openai>=1.50" \
    "anthropic>=0.40"

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
