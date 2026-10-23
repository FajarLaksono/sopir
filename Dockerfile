FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY alembic/ alembic/
COPY backend/ backend/
COPY worker/ worker/

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload --reload-dir backend --reload-dir worker --reload-dir alembic"]