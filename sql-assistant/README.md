# SQL Assistant

## Run it
1. Database:   `docker compose up -d`
2. Backend:    `cd backend && python -m venv .venv && source .venv/bin/activate`
               `pip install -r requirements.txt && uvicorn app.main:app --reload`
3. Frontend:   `cd frontend && npm install && npm run dev`
4. Open http://localhost:5173

(Optional) `cp .env.example .env` to override settings. On Windows, activate the venv with `.venv\Scripts\activate`.
