# SAP BTP AI Operations Agent

Details: [docs/foundation.md](docs/foundation.md)

```powershell
# Configuration (first time only)
Copy-Item .env.example .env

# PostgreSQL (existing container btp-postgres, host port 5444)
docker compose ps
docker exec btp-postgres pg_isready -U btp_agent -d btp_agent

# Backend setup (first time only)
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# Database migrations
python -m alembic current
python -m alembic upgrade head

# Run the API (http://127.0.0.1:8000, docs at /docs)
python -m app --reload

# Backend tests and lint
python -m pytest
python -m ruff check .

# Frontend (second terminal, from the repository root)
cd frontend
npm install
npm run dev        # http://localhost:5173
npm test
npm run build

# Health checks
curl.exe http://127.0.0.1:8000/health/live
curl.exe http://127.0.0.1:8000/health/ready
```
