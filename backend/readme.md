```powershell
# Check PostgreSQL (existing btp-postgres container, host port 5444)
docker compose ps

# Navigate to Backend
cd backend

# Create Virtual Environment
py -3.13 -m venv .venv

# Activate Virtual Environment
.\.venv\Scripts\Activate.ps1

# Install Dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Verify Dependencies
python -m pip check

# Migrations
python -m alembic current
python -m alembic upgrade head

# Run FastAPI (selects a psycopg-compatible event loop on Windows)
python -m app --reload

# Alternative: plain uvicorn (on Windows, --loop is required without --reload)
python -m uvicorn app.main:app --port 8000 --loop asyncio:SelectorEventLoop

# Run Tests (uses the separate btp_agent_test database)
python -m pytest -v

# Check Code Quality
python -m ruff check .

# Deactivate Virtual Environment
deactivate
```
