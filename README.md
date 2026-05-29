# The Gang Goes Golfing

Backend API and tooling for managing golf groups, players, handicaps, and group membership.

## Current status

The backend currently supports:

- Listing players
- Adding players
- Listing groups
- Adding groups
- Deleting groups
- Adding players to groups
- Listing players in a group
- Listing groups for a player
- Removing players from groups

## Project structure

```text
the-gang-goes-golfing/
├─ .venv/
├─ backend/
│  ├─ models/
│  │  ├─ __init__.py
│  │  ├─ group.py
│  │  ├─ group_player.py
│  │  └─ player.py
│  ├─ routers/
│  │  ├─ __init__.py
│  │  ├─ group_players.py
│  │  ├─ groups.py
│  │  └─ players.py
│  ├─ services/
│  │  ├─ __init__.py
│  │  ├─ group_players.py
│  │  ├─ groups.py
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

## Backend folder pattern

The backend uses three main folders for API functionality:

```text
models/    = data shapes and validation
services/  = database logic and application logic
routers/   = API endpoints
```

The general flow is:

```text
router -> service -> Supabase table
```

For example, adding a player to a group follows this path:

```text
POST /group-players/
    -> backend/routers/group_players.py
    -> backend/services/group_players.py
    -> group_players table in Supabase
```

### Models

Models define the shape of incoming and outgoing data.

Examples:

```text
backend/models/player.py
backend/models/group.py
backend/models/group_player.py
```

Models should contain Pydantic classes such as:

```python
class PlayerCreate(BaseModel):
    first_name: str
    surname: str
    date_of_birth: date | None = None
```

Models should not talk directly to Supabase.

### Services

Services contain the logic for interacting with Supabase.

Examples:

```text
backend/services/players.py
backend/services/groups.py
backend/services/group_players.py
```

Services should contain functions such as:

```python
def list_players() -> list[dict]:
    ...

def create_group(group: GroupCreate) -> dict:
    ...

def add_player_to_group(group_player: GroupPlayerCreate) -> dict:
    ...
```

Services are where `.table(...).select(...)`, `.insert(...)`, `.delete(...)`, and `.execute()` should live.

### Routers

Routers define the actual API endpoints.

Examples:

```text
backend/routers/players.py
backend/routers/groups.py
backend/routers/group_players.py
```

Routers should contain FastAPI routes such as:

```python
@router.get("/")
def list_players_route():
    return list_players()
```

Routers should stay thin. They should mainly:

- Define the URL
- Define the HTTP method
- Accept request data
- Call a service function
- Return the response
- Raise HTTP errors where needed

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

### Delete a group

Run:

```powershell
uv run invoke delete-group --group-id=<group-id>
```

This calls:

```http
DELETE /groups/{group_id}
```

Example:

```powershell
uv run invoke delete-group --group-id=00000000-0000-0000-0000-000000000000
```

Expected output:

```text
Group deleted: Weekend Golfers (Weekend-Golfers) id: 00000000-0000-0000-0000-000000000000
```

If the group does not exist, the task should return:

```text
Group not found.
```

Deleting a group also removes matching rows in the `group_players` join table because the database foreign key uses `ON DELETE CASCADE`.

## Player groups

Player groups are managed through the `group_players` join table.

This table connects players to groups:

```text
group_id  -> groups.id
player_id -> players.id
```

A player can belong to many groups, and a group can contain many players.

The table has a composite primary key:

```sql
PRIMARY KEY (group_id, player_id)
```

That means the same player cannot be added to the same group twice.

### Add a player to a group

First list your players and groups:

```powershell
uv run invoke list-players
uv run invoke list-groups
```

Copy one `player_id` and one `group_id`.

Then run:

```powershell
uv run invoke add-player-to-group --group-id=<group-id> --player-id=<player-id>
```

This calls:

```http
POST /group-players/
```

Example request body:

```json
{
  "group_id": "group-uuid",
  "player_id": "player-uuid"
}
```

Example response:

```json
{
  "group_id": "group-uuid",
  "player_id": "player-uuid",
  "created_at": "2025-01-01T12:00:00+00:00"
}
```

### List players in a group

Run:

```powershell
uv run invoke list-players-in-group --group-id=<group-id>
```

This calls:

```http
GET /group-players/group/{group_id}
```

Example response:

```json
[
  {
    "group_id": "group-uuid",
    "player_id": "player-uuid",
    "created_at": "2025-01-01T12:00:00+00:00",
    "players": {
      "id": "player-uuid",
      "first_name": "Alex",
      "surname": "Taylor",
      "date_of_birth": "1995-12-28",
      "created_at": "2025-01-01T12:00:00+00:00"
    }
  }
]
```

### List groups for a player

Run:

```powershell
uv run invoke list-groups-for-player --player-id=<player-id>
```

This calls:

```http
GET /group-players/player/{player_id}
```

Example response:

```json
[
  {
    "group_id": "group-uuid",
    "player_id": "player-uuid",
    "created_at": "2025-01-01T12:00:00+00:00",
    "groups": {
      "id": "group-uuid",
      "code": "Weekend-Golfers",
      "slug": "weekend-golfers",
      "name": "Weekend Golfers",
      "description": "Example golf group",
      "created_at": "2025-01-01T12:00:00+00:00"
    }
  }
]
```

### Remove a player from a group

Run:

```powershell
uv run invoke remove-player-from-group --group-id=<group-id> --player-id=<player-id>
```

This calls:

```http
DELETE /group-players/
```

Example request body:

```json
{
  "group_id": "group-uuid",
  "player_id": "player-uuid"
}
```

Example response:

```json
{
  "group_id": "group-uuid",
  "player_id": "player-uuid",
  "created_at": "2025-01-01T12:00:00+00:00"
}
```

If the player is not in that group, the task should return:

```text
Player is not in that group.
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

uv run invoke add-player-to-group --group-id=<group-id> --player-id=<player-id>
uv run invoke list-players-in-group --group-id=<group-id>
uv run invoke list-groups-for-player --player-id=<player-id>
uv run invoke remove-player-from-group --group-id=<group-id> --player-id=<player-id>
```

## Operation reference

### Environment

```powershell
uv run invoke check-env
```

### Server

```powershell
uv run invoke dev
uv run invoke routes
```

### Players

```powershell
uv run invoke list-players
uv run invoke add-player --first-name=Alex --surname=Taylor --dob=1995-12-28
uv run invoke add-player --first-name=Alex --surname=Taylor
```

### Groups

```powershell
uv run invoke list-groups
uv run invoke add-group --code=Weekend-Golfers --slug=weekend-golfers --name="Weekend Golfers"
uv run invoke add-group --code=Weekend-Golfers --slug=weekend-golfers --name="Weekend Golfers" --description="Example golf group"
uv run invoke delete-group --group-id=<group-id>
```

### Player groups

```powershell
uv run invoke add-player-to-group --group-id=<group-id> --player-id=<player-id>
uv run invoke list-players-in-group --group-id=<group-id>
uv run invoke list-groups-for-player --player-id=<player-id>
uv run invoke remove-player-from-group --group-id=<group-id> --player-id=<player-id>
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
from backend.routers import players, groups, group_players

app.include_router(players.router)
app.include_router(groups.router)
app.include_router(group_players.router)
```

### Duplicate player in group

If you try to add the same player to the same group twice, the database will reject it because `group_players` uses:

```sql
PRIMARY KEY (group_id, player_id)
```

This is expected behaviour.