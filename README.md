# The Gang Goes Golfing

Backend API and tooling for managing golf groups, players, handicaps, and group membership.

## Current status

The backend currently supports:

- Listing players
- Adding players
- Listing groups
- Adding groups

## Project structure

```text
the-gang-goes-golfing/
├─ .venv/
├─ backend/
│  ├─ models/
│  │  ├─ __init__.py
│  │  └─ player.py
│  ├─ routers/
│  │  ├─ __init__.py
│  │  ├─ groups.py
│  │  └─ players.py
│  ├─ services/
│  │  ├─ __init__.py
│  │  └─ players.py
│  ├─ database.py
│  └─ main.py
├─ frontend/
├─ .gitignore
├─ pyproject.toml
├─ README.md
├─ tasks.py
└─ uv.lock
```

## Tech stack

This project uses:

- `uv` for Python environment and dependency management
- `invoke` for developer tasks
- FastAPI for the backend API
- Supabase as the database backend

## Environment setup

Environment variables are loaded from a user-level `.env` file.

On Windows, this should live at:

```text
C:\Users\<YourUsername>\.env
```

Example:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_key_here
```

Check that your environment variables are available:

```powershell
uv run invoke check-env
```

Expected output:

```text
EXISTS: True
SUPABASE_URL loaded: True
SUPABASE_KEY loaded: True
```

## Install dependencies

From the project root:

```powershell
uv run invoke setup
```

After updating `pyproject.toml`, reinstall or sync dependencies with:

```powershell
uv run invoke install
```

## Start the development server

Run:

```powershell
uv run invoke dev
```

The API should start on:

```text
http://localhost:8000
```

You can view the interactive FastAPI docs at:

```text
http://localhost:8000/docs
```

## Check registered routes

Run:

```powershell
uv run invoke routes
```

This prints all API routes currently registered with FastAPI.

## Players

### List all players

Run:

```powershell
uv run invoke list-players
```

This calls:

```http
GET /players/
```

Example response:

```json
[
  {
    "id": "player-uuid",
    "first_name": "Alex",
    "surname": "Taylor",
    "date_of_birth": "1995-12-28",
    "created_at": "2025-01-01T12:00:00+00:00"
  }
]
```

### Add a new player

Run:

```powershell
uv run invoke add-player --first-name=Alex --surname=Taylor --dob=1995-12-28
```

Or without a date of birth:

```powershell
uv run invoke add-player --first-name=Alex --surname=Taylor
```

This calls:

```http
POST /players/
```

Example request body:

```json
{
  "first_name": "Alex",
  "surname": "Taylor",
  "date_of_birth": "1995-12-28"
}
```

Example response:

```json
{
  "id": "player-uuid",
  "first_name": "Alex",
  "surname": "Taylor",
  "date_of_birth": "1995-12-28",
  "created_at": "2025-01-01T12:00:00+00:00"
}
```

## Groups

### List all groups

Run:

```powershell
uv run invoke list-groups
```

This calls:

```http
GET /groups/
```

Example response:

```json
[
  {
    "id": "group-uuid",
    "code": "Weekend-Golfers",
    "slug": "weekend-golfers",
    "name": "Weekend Golfers",
    "description": "Example golf group",
    "created_at": "2025-01-01T12:00:00+00:00"
  }
]
```

### Add a new group

Run:

```powershell
uv run invoke add-group --code=Weekend-Golfers --slug=weekend-golfers --name="Weekend Golfers"
```

With a description:

```powershell
uv run invoke add-group --code=Weekend-Golfers --slug=weekend-golfers --name="Weekend Golfers" --description="Example golf group"
```

This calls:

```http
POST /groups/
```

Example request body:

```json
{
  "code": "Weekend-Golfers",
  "slug": "weekend-golfers",
  "name": "Weekend Golfers",
  "description": "Example golf group"
}
```

Example response:

```json
{
  "id": "group-uuid",
  "code": "Weekend-Golfers",
  "slug": "weekend-golfers",
  "name": "Weekend Golfers",
  "description": "Example golf group",
  "created_at": "2025-01-01T12:00:00+00:00"
}
```

## Case sensitivity for group codes

Group codes are stored in a PostgreSQL `text` column with a unique constraint.

That means this project treats the following as different values:

```text
Weekend-Golfers
weekend-golfers
WEEKEND-GOLFERS
```

Recommended convention:

```text
code: Weekend-Golfers
slug: weekend-golfers
name: Weekend Golfers
```

Use:

- `code` for the case-sensitive group code
- `slug` for URL-friendly routes
- `name` for display in the frontend

## Useful development workflow

Start the API in one terminal:

```powershell
uv run invoke dev
```

Then use a second terminal to run tasks:

```powershell
uv run invoke list-players
uv run invoke add-player --first-name=Alex --surname=Taylor --dob=1995-12-28
uv run invoke list-groups
uv run invoke add-group --code=Weekend-Golfers --slug=weekend-golfers --name="Weekend Golfers"
```

## Troubleshooting

### `invoke` is not recognized

Use:

```powershell
uv run invoke <task-name>
```

Instead of:

```powershell
invoke <task-name>
```

Or activate the virtual environment first:

```powershell
.venv\Scripts\activate
```

### Missing Supabase environment variables

If you see an error like:

```text
Missing SUPABASE_URL or SUPABASE_KEY
```

Check that your user-level `.env` exists and contains:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_key_here
```

Then run:

```powershell
uv run invoke check-env
```

### API route not found

Check the registered routes:

```powershell
uv run invoke routes
```

If a route is missing, check that the router is included in `backend/main.py`.

Example:

```python
from backend.routers import players, groups

app.include_router(players.router)
app.include_router(groups.router)
```