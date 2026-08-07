import subprocess
import time

from invoke import task


# ── Environment ────────────────────────────────────────────────

@task
def setup(c):
    """Create venv and install all dependencies."""
    c.run("uv venv")
    c.run("uv pip install -e '.[dev]'")
    print("Setup complete. Activate your venv with: .venv\\Scripts\\activate")


@task
def install(c):
    """Install/sync dependencies (run after updating pyproject.toml)."""
    c.run("uv pip install -e '.[dev]'")


# ── Development server ─────────────────────────────────────────

@task
def dev(c):
    """Start the FastAPI development server with auto-reload."""
    c.run("uv run uvicorn backend.main:app --reload --port 8000")


# ── Players ────────────────────────────────────────────────────

@task
def add_player(c, first_name, surname, dob=None):
    """
    Add a new player to the database.

    Usage:
        invoke add-player --first-name=Sam --surname=Adams
        invoke add-player --first-name=Sam --surname=Adams --dob=1996-01-02
    """
    import requests

    payload = {"first_name": first_name, "surname": surname}
    if dob:
        payload["date_of_birth"] = dob

    response = requests.post("http://localhost:8000/players/", json=payload)

    if response.status_code == 201:
        player = response.json()
        print(f"Player created: {player['first_name']} {player['surname']} (id: {player['id']})")
    else:
        print(f"Error {response.status_code}: {response.json()}")


@task
def list_players(c):
    """List all players in the database."""
    import requests

    response = requests.get("http://localhost:8000/players/")

    if response.status_code == 200:
        players = response.json()
        if not players:
            print("No players found.")
            return
        print(f"\n{'ID':<38} {'First name':<15} {'Surname':<15} {'DOB'}")
        print("-" * 80)
        for p in players:
            dob = p.get("date_of_birth") or "—"
            print(f"{p['id']:<38} {p['first_name']:<15} {p['surname']:<15} {dob}")
    else:
        print(f"Error {response.status_code}: {response.json()}")


# ── Testing ────────────────────────────────────────────────────

@task
def test(c):
    """Run the test suite."""
    c.run("uv run pytest")

# ── Utilities ──────────────────────────────────────────────────

@task
def routes(c):
    """Print all registered API routes."""
    c.run("python -c \"from backend.main import app; [print(r.methods, r.path) for r in app.routes]\"")


@task
def check_env(c):
    """Check whether required user-level environment variables are available."""
    c.run(
        "uv run python -c "
        "\"from pathlib import Path; "
        "from dotenv import load_dotenv; "
        "import os; "
        "env_file = Path.home() / '.env'; "
        "print('ENV_FILE:', env_file); "
        "print('EXISTS:', env_file.exists()); "
        "load_dotenv(env_file, override=True); "
        "print('t3g_sbdb_URL loaded:', bool(os.getenv('t3g_sbdb_URL'))); "
        "print('t3g_sbdb_KEY loaded:', bool(os.getenv('t3g_sbdb_KEY')))\""
    )

# ── Groups ─────────────────────────────────────────────────────

@task
def add_group(c, code, slug, name, description=None):
    """
    Add a new group to the database.

    Usage:
        invoke add-group --code=The-SENCO-Swingers --slug=The-SENCO-Swingers --name="The SENCO Swingers"
    """
    import requests

    payload = {
        "code": code,
        "slug": slug,
        "name": name,
        "description": description,
    }

    response = requests.post("http://localhost:8000/groups/", json=payload)

    if response.status_code == 201:
        group = response.json()
        print(f"Group created: {group['name']} ({group['code']}) id: {group['id']}")
    else:
        print(f"Error {response.status_code}: {response.json()}")


@task
def list_groups(c):
    """List all groups in the database."""
    import requests

    response = requests.get("http://localhost:8000/groups/")

    if response.status_code == 200:
        groups = response.json()
        if not groups:
            print("No groups found.")
            return

        print(f"\n{'ID':<38} {'Code':<25} {'Name':<25} {'Description'}")
        print("-" * 110)

        for g in groups:
            description = g.get("description") or "—"
            print(f"{g['id']:<38} {g['code']:<25} {g['name']:<25} {description}")
    else:
        print(f"Error {response.status_code}: {response.json()}")

@task
def delete_group(c, group_id):
    """
    Delete a group by ID.

    Usage:
        uv run invoke delete-group --group-id=<group-id>
    """
    import requests

    response = requests.delete(f"http://localhost:8000/groups/{group_id}")

    if response.status_code == 200:
        group = response.json()
        print(f"Group deleted: {group['name']} ({group['code']}) id: {group['id']}")
    elif response.status_code == 404:
        print("Group not found.")
    else:
        print(f"Error {response.status_code}: {response.json()}")

# ── Group players ──────────────────────────────────────────────

@task
def add_player_to_group(c, group_id, player_id):
    """
    Add a player to a group.

    Usage:
        uv run invoke add-player-to-group --group-id=<group-id> --player-id=<player-id>
    """
    import requests

    payload = {
        "group_id": group_id,
        "player_id": player_id,
    }

    response = requests.post("http://localhost:8000/group-players/", json=payload)

    if response.status_code == 201:
        group_player = response.json()
        print(
            f"Player added to group: "
            f"group_id={group_player['group_id']} player_id={group_player['player_id']}"
        )
    else:
        print(f"Error {response.status_code}: {response.json()}")


@task
def list_players_in_group(c, group_id):
    """
    List all players in a group.

    Usage:
        uv run invoke list-players-in-group --group-id=<group-id>
    """
    import requests

    response = requests.get(f"http://localhost:8000/group-players/group/{group_id}")

    if response.status_code == 200:
        rows = response.json()

        if not rows:
            print("No players found in this group.")
            return

        print(f"\n{'Player ID':<38} {'First name':<15} {'Surname':<15} {'DOB'}")
        print("-" * 90)

        for row in rows:
            player = row.get("players") or {}
            dob = player.get("date_of_birth") or "—"

            print(
                f"{row['player_id']:<38} "
                f"{player.get('first_name', '—'):<15} "
                f"{player.get('surname', '—'):<15} "
                f"{dob}"
            )
    else:
        print(f"Error {response.status_code}: {response.json()}")


@task
def list_groups_for_player(c, player_id):
    """
    List all groups for a player.

    Usage:
        uv run invoke list-groups-for-player --player-id=<player-id>
    """
    import requests

    response = requests.get(f"http://localhost:8000/group-players/player/{player_id}")

    if response.status_code == 200:
        rows = response.json()

        if not rows:
            print("No groups found for this player.")
            return

        print(f"\n{'Group ID':<38} {'Code':<25} {'Name':<25}")
        print("-" * 95)

        for row in rows:
            group = row.get("groups") or {}

            print(
                f"{row['group_id']:<38} "
                f"{group.get('code', '—'):<25} "
                f"{group.get('name', '—'):<25}"
            )
    else:
        print(f"Error {response.status_code}: {response.json()}")


@task
def remove_player_from_group(c, group_id, player_id):
    """
    Remove a player from a group.

    Usage:
        uv run invoke remove-player-from-group --group-id=<group-id> --player-id=<player-id>
    """
    import requests

    payload = {
        "group_id": group_id,
        "player_id": player_id,
    }

    response = requests.delete("http://localhost:8000/group-players/", json=payload)

    if response.status_code == 200:
        group_player = response.json()
        print(
            f"Player removed from group: "
            f"group_id={group_player['group_id']} player_id={group_player['player_id']}"
        )
    elif response.status_code == 404:
        print("Player is not in that group.")
    else:
        print(f"Error {response.status_code}: {response.json()}")


# ── Handicaps ──────────────────────────────────────────────────

@task
def add_handicap(c, player_id, handicap, valid_from=None):
    """
    Add a handicap for a player.

    Usage:
        uv run invoke add-handicap --player-id=<player-id> --handicap=18.4
        uv run invoke add-handicap --player-id=<player-id> --handicap=18.4 --valid-from=2025-01-01
    """
    import requests

    payload = {
        "player_id": player_id,
        "handicap": float(handicap),
    }

    if valid_from:
        payload["valid_from"] = valid_from

    response = requests.post("http://localhost:8000/handicaps/", json=payload)

    if response.status_code == 201:
        row = response.json()
        print(
            f"Handicap added: player_id={row['player_id']} "
            f"handicap={row['handicap']} valid_from={row['valid_from']}"
        )
    else:
        print(f"Error {response.status_code}: {response.json()}")


@task
def list_handicaps(c, player_id):
    """
    List handicap history for a player.

    Usage:
        uv run invoke list-handicaps --player-id=<player-id>
    """
    import requests

    response = requests.get(f"http://localhost:8000/handicaps/player/{player_id}")

    if response.status_code == 200:
        rows = response.json()

        if not rows:
            print("No handicaps found for this player.")
            return

        print(f"\n{'ID':<38} {'Player ID':<38} {'Handicap':<10} {'Valid from'}")
        print("-" * 100)

        for row in rows:
            print(
                f"{row['id']:<38} "
                f"{row['player_id']:<38} "
                f"{row['handicap']:<10} "
                f"{row['valid_from']}"
            )
    else:
        print(f"Error {response.status_code}: {response.json()}")


@task
def current_handicap(c, player_id):
    """
    Get the current handicap for a player.

    Usage:
        uv run invoke current-handicap --player-id=<player-id>
    """
    import requests

    response = requests.get(f"http://localhost:8000/handicaps/player/{player_id}/current")

    if response.status_code == 200:
        row = response.json()
        print(
            f"Current handicap: player_id={row['player_id']} "
            f"handicap={row['handicap']} valid_from={row['valid_from']}"
        )
    elif response.status_code == 404:
        print("No handicap found for this player.")
    else:
        print(f"Error {response.status_code}: {response.json()}")


@task
def list_latest_handicaps_for_group(c, group_id):
    """
    Get the latest handicaps for all players in a group.

    Usage:
        uv run invoke list-group-handicaps --group-id=<group-id>
    """
    import requests

    response = requests.get(f"http://localhost:8000/handicaps/group/{group_id}/latest")

    if response.status_code != 200:
        print(f"Error {response.status_code}: {response.json()}")
        return

    rows = response.json()

    if not rows:
        print("No players found in this group.")
        return

    print(f"\n{'Player':<25} {'Handicap':<10} {'Valid from'}")
    print("-" * 50)

    for row in rows:
        player_name = f"{row.get('first_name', '—')} {row.get('surname', '—')}"
        latest = row.get("latest_handicap") or {}

        handicap = latest.get("handicap", "—")
        valid_from = latest.get("valid_from", "—")

        print(f"{player_name:<25} {str(handicap):<10} {valid_from}")


@task
def dev_all(c):
    """Start both the backend API and frontend Dash app together."""
    backend = subprocess.Popen(
        ["uv", "run", "uvicorn", "backend.main:app", "--reload", "--port", "8000"]
    )
    frontend = subprocess.Popen(
        ["uv", "run", "python", "frontend/src/app.py"]
    )

    print("\nBackend:  http://localhost:8000")
    print("Frontend: http://localhost:8050")
    print("Press Ctrl+C to stop both.\n")

    try:
        while True:
            if backend.poll() is not None:
                print("Backend process exited — stopping frontend too.")
                break
            if frontend.poll() is not None:
                print("Frontend process exited — stopping backend too.")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        for proc in (backend, frontend):
            if proc.poll() is None:
                proc.terminate()
        for proc in (backend, frontend):
            proc.wait()