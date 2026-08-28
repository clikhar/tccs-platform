# TCCS Controller Backend

FastAPI service for the TCCS controller.

## Local development

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start PostgreSQL with Docker Compose from the repository root:

```bash
docker compose up -d postgres
```

Set the database URL:

```bash
export DATABASE_URL='postgresql+asyncpg://tccs:change-me-local@localhost:5432/tccs'
```

Initialize SQLAlchemy tables:

```bash
python -m app.init_db
```

Run the API:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Health endpoint:

```text
http://SERVER-IP:8000/health
```
