FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir alembic>=1.13,<2.0

COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY database ./database

EXPOSE 8080

CMD ["sh", "-c", "alembic upgrade head && python -m app"]
