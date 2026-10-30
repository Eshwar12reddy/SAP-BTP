```powershell
# Start PostgreSQL
docker compose up -d
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

# Run FastAPI
python -m uvicorn app.main:app --reload --port 8000

# Run Tests
python -m pytest tests -v

# Check Code Quality
python -m ruff check .

# Deactivate Virtual Environment
deactivate
```

