# SAP BTP AI Operations Agent

Details: [docs/foundation.md](docs/foundation.md) · [docs/auth-rbac.md](docs/auth-rbac.md) · [docs/agent.md](docs/agent.md) · [docs/ai-safety.md](docs/ai-safety.md)

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

# First platform administrator (one-time; prompts for the password)
python -m app.cli create-admin --email admin@example.com --display-name "Platform Admin"

# Configured BTP mapping
python -m app.cli btp add-global-account --external-id <ga-guid> --name "Acme Global"
python -m app.cli btp add-subaccount --external-id <sa-guid> --name "Acme EU" --parent <ga-guid>
python -m app.cli btp add-environment --account <sa-guid> --name Development --runtime cloudfoundry
python -m app.cli btp list

# Pre-authentication conversations: assign an owner explicitly
python -m app.cli assign-legacy-conversations --email someone@example.com --all

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
