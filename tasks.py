import platform
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
    # --reload-dir scopes the file watcher to backend/ only — without it,
    # uvicorn watches the whole project (including .venv), so any package
    # install triggers a spurious reload.
    c.run("uv run uvicorn backend.main:app --reload --reload-dir backend --port 8000")


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
        "print('SUPABASE_URL loaded:', bool(os.getenv('SUPABASE_URL'))); "
        "print('SUPABASE_KEY loaded:', bool(os.getenv('SUPABASE_KEY')))\""
    )

# ── Clubs ─────────────────────────────────────────────────────

@task
def add_club(c, code, slug, name, description=None):
    """
    Add a new club to the database.

    Usage:
        invoke add-club --code=The-SENCO-Swingers --slug=The-SENCO-Swingers --name="The SENCO Swingers"
    """
    import requests

    payload = {
        "code": code,
        "slug": slug,
        "name": name,
        "description": description,
    }

    response = requests.post("http://localhost:8000/clubs/", json=payload)

    if response.status_code == 201:
        club = response.json()
        print(f"Club created: {club['name']} ({club['code']}) id: {club['id']}")
    else:
        print(f"Error {response.status_code}: {response.json()}")


@task
def list_clubs(c):
    """List all clubs in the database."""
    import requests

    response = requests.get("http://localhost:8000/clubs/")

    if response.status_code == 200:
        clubs = response.json()
        if not clubs:
            print("No clubs found.")
            return

        print(f"\n{'ID':<38} {'Code':<25} {'Name':<25} {'Description'}")
        print("-" * 110)

        for g in clubs:
            description = g.get("description") or "—"
            print(f"{g['id']:<38} {g['code']:<25} {g['name']:<25} {description}")
    else:
        print(f"Error {response.status_code}: {response.json()}")

@task
def delete_club(c, club_id):
    """
    Delete a club by ID.

    Usage:
        uv run invoke delete-club --club-id=<club-id>
    """
    import requests

    response = requests.delete(f"http://localhost:8000/clubs/{club_id}")

    if response.status_code == 200:
        club = response.json()
        print(f"Club deleted: {club['name']} ({club['code']}) id: {club['id']}")
    elif response.status_code == 404:
        print("Club not found.")
    else:
        print(f"Error {response.status_code}: {response.json()}")

# ── Club players ──────────────────────────────────────────────

@task
def add_player_to_club(c, club_id, player_id):
    """
    Add a player to a club.

    Usage:
        uv run invoke add-player-to-club --club-id=<club-id> --player-id=<player-id>
    """
    import requests

    payload = {
        "club_id": club_id,
        "player_id": player_id,
    }

    response = requests.post("http://localhost:8000/club-players/", json=payload)

    if response.status_code == 201:
        club_player = response.json()
        print(
            f"Player added to club: "
            f"club_id={club_player['club_id']} player_id={club_player['player_id']}"
        )
    else:
        print(f"Error {response.status_code}: {response.json()}")


@task
def list_players_in_club(c, club_id):
    """
    List all players in a club.

    Usage:
        uv run invoke list-players-in-club --club-id=<club-id>
    """
    import requests

    response = requests.get(f"http://localhost:8000/club-players/club/{club_id}")

    if response.status_code == 200:
        rows = response.json()

        if not rows:
            print("No players found in this club.")
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
def list_clubs_for_player(c, player_id):
    """
    List all clubs for a player.

    Usage:
        uv run invoke list-clubs-for-player --player-id=<player-id>
    """
    import requests

    response = requests.get(f"http://localhost:8000/club-players/player/{player_id}")

    if response.status_code == 200:
        rows = response.json()

        if not rows:
            print("No clubs found for this player.")
            return

        print(f"\n{'Club ID':<38} {'Code':<25} {'Name':<25}")
        print("-" * 95)

        for row in rows:
            club = row.get("clubs") or {}

            print(
                f"{row['club_id']:<38} "
                f"{club.get('code', '—'):<25} "
                f"{club.get('name', '—'):<25}"
            )
    else:
        print(f"Error {response.status_code}: {response.json()}")


@task
def remove_player_from_club(c, club_id, player_id):
    """
    Remove a player from a club.

    Usage:
        uv run invoke remove-player-from-club --club-id=<club-id> --player-id=<player-id>
    """
    import requests

    payload = {
        "club_id": club_id,
        "player_id": player_id,
    }

    response = requests.delete("http://localhost:8000/club-players/", json=payload)

    if response.status_code == 200:
        club_player = response.json()
        print(
            f"Player removed from club: "
            f"club_id={club_player['club_id']} player_id={club_player['player_id']}"
        )
    elif response.status_code == 404:
        print("Player is not in that club.")
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
def list_latest_handicaps_for_club(c, club_id):
    """
    Get the latest handicaps for all players in a club.

    Usage:
        uv run invoke list-club-handicaps --club-id=<club-id>
    """
    import requests

    response = requests.get(f"http://localhost:8000/handicaps/club/{club_id}/latest")

    if response.status_code != 200:
        print(f"Error {response.status_code}: {response.json()}")
        return

    rows = response.json()

    if not rows:
        print("No players found in this club.")
        return

    print(f"\n{'Player':<25} {'Handicap':<10} {'Valid from'}")
    print("-" * 50)

    for row in rows:
        player_name = f"{row.get('first_name', '—')} {row.get('surname', '—')}"
        latest = row.get("latest_handicap") or {}

        handicap = latest.get("handicap", "—")
        valid_from = latest.get("valid_from", "—")

        print(f"{player_name:<25} {str(handicap):<10} {valid_from}")


def _kill_port(port: int) -> None:
    """Kill whatever process is currently listening on the given port, if any."""
    system = platform.system()

    if system == "Windows":
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
        pids = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            _proto, local_addr, _remote_addr, state, pid = parts[:5]
            if state == "LISTENING" and local_addr.endswith(f":{port}"):
                pids.add(pid)
        for pid in pids:
            print(f"  Killing stale process {pid} on port {port}")
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
    else:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"], capture_output=True, text=True
        )
        for pid in result.stdout.split():
            print(f"  Killing stale process {pid} on port {port}")
            subprocess.run(["kill", "-9", pid])


@task
def dev_all(c):
    """Start both the backend API and frontend Dash app together."""
    print("Checking for stale processes on 8000/8050...")
    _kill_port(8000)
    _kill_port(8050)

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

# ── Admin cleanup ────────────────────────────────────────────
# Every command below hits backend/routers/admin.py, which just wraps
# backend/services/admin_cleanup.py -- see that module's own docstring
# for the full cascade logic. All three default to a DRY RUN (prints
# counts of what WOULD be deleted, deletes nothing) -- only pass
# --execute once the preview numbers look right. There is no undo.


def _print_admin_summary(result: dict) -> None:
    player_name = result.pop("player_name", None)
    player_email = result.pop("player_email", None)
    if player_name is not None or player_email is not None:
        print(f"Player: {player_name or '—'} ({player_email or '—'})\n")

    any_nonzero = False
    for key, count in result.items():
        if count:
            any_nonzero = True
            print(f"  {key}: {count}")
    if not any_nonzero:
        print("  (nothing to delete)")


@task
def list_player_accounts(c):
    """
    List all player accounts (the login/email record, not the player
    roster record) -- shows each account's own id, which is what
    delete-player-account needs (not the player's own id from
    list-players).

    Usage:
        uv run invoke list-player-accounts
    """
    import requests

    response = requests.get("http://localhost:8000/admin/player-accounts")

    if response.status_code != 200:
        print(f"Error {response.status_code}: {response.json()}")
        return

    accounts = response.json()
    if not accounts:
        print("No player accounts found.")
        return

    print(f"\n{'Account ID':<38} {'Email':<30} {'Name':<20} {'Google?'}")
    print("-" * 100)
    for a in accounts:
        print(f"{a['id']:<38} {a['email']:<30} {a['name']:<20} {'yes' if a.get('google_id') else 'no'}")


@task
def wipe_tournaments(c, execute=False):
    """
    Delete every tournament in the app (every club) -- entrants, pairs,
    tournament rounds, tee times, every round played under any of them,
    and any club feed post about a tournament.

    Defaults to a dry run. Pass --execute to actually delete.

    Usage:
        uv run invoke wipe-tournaments               # preview, deletes nothing
        uv run invoke wipe-tournaments --execute      # actually delete
    """
    import requests

    dry_run = not execute
    response = requests.post(
        "http://localhost:8000/admin/tournaments/wipe",
        params={"dry_run": str(dry_run).lower()},
    )

    if response.status_code != 200:
        print(f"Error {response.status_code}: {response.json()}")
        return

    print("DRY RUN -- nothing deleted. Re-run with --execute to actually delete.\n" if dry_run else "DELETED:\n")
    _print_admin_summary(response.json())


@task
def reset_clubs(c, keep_slug=None, keep_none=False, execute=False):
    """
    Delete every club except the one matching --keep-slug, and every
    tournament everywhere (including the kept club's own -- see
    wipe-tournaments). The kept club's regular membership, casual rounds,
    and non-tournament feed posts are left alone.

    Pass --keep-none instead of --keep-slug to delete EVERY club, with no
    exception -- this is a separate, explicit flag on purpose, so a
    forgotten or typo'd --keep-slug can never silently turn into a full
    wipe. One of --keep-slug or --keep-none is required.

    Defaults to a dry run. Pass --execute to actually delete.

    Usage:
        uv run invoke reset-clubs --keep-slug=the-gang                # preview, keep one club
        uv run invoke reset-clubs --keep-slug=the-gang --execute       # actually delete, keep one club
        uv run invoke reset-clubs --keep-none                         # preview, delete every club
        uv run invoke reset-clubs --keep-none --execute                # actually delete every club
    """
    import requests

    if keep_slug and keep_none:
        print("Pass only one of --keep-slug or --keep-none, not both.")
        return
    if not keep_slug and not keep_none:
        print("Pass --keep-slug=<slug> to keep one club, or --keep-none to delete every club.")
        return

    dry_run = not execute
    params = {"dry_run": str(dry_run).lower(), "delete_all": str(keep_none).lower()}
    if keep_slug:
        params["keep_slug"] = keep_slug
    response = requests.post("http://localhost:8000/admin/clubs/reset", params=params)

    if response.status_code == 404:
        print(f"No club with slug '{keep_slug}'. Run `uv run invoke list-clubs` to check the exact slug.")
        return
    if response.status_code != 200:
        print(f"Error {response.status_code}: {response.json()}")
        return

    print("DRY RUN -- nothing deleted. Re-run with --execute to actually delete.\n" if dry_run else "DELETED:\n")
    _print_admin_summary(response.json())


@task
def delete_club_cascade(c, slug, execute=False):
    """
    Delete ONE club and everything scoped to it -- its own tournaments
    (entrants, pairs, tournament rounds, tee times, every round played
    under any of them), its own casual rounds, membership, invites, feed
    posts, and its uploaded photo. Every other club is left completely
    untouched.

    This is the safe way to delete a single club -- the plain
    `delete-club` task just does a raw DB delete with no cascade, which
    will fail (or worse, leave orphaned rows) the moment the club has any
    real data attached. Use this one instead for any club that isn't
    already empty.

    Defaults to a dry run. Pass --execute to actually delete.

    Usage:
        uv run invoke delete-club-cascade --slug=senco-squad             # preview
        uv run invoke delete-club-cascade --slug=senco-squad --execute    # actually delete
    """
    import requests

    dry_run = not execute
    response = requests.delete(
        f"http://localhost:8000/admin/clubs/{slug}",
        params={"dry_run": str(dry_run).lower()},
    )

    if response.status_code == 404:
        print(f"No club with slug '{slug}'. Run `uv run invoke list-clubs` to check the exact slug.")
        return
    if response.status_code != 200:
        print(f"Error {response.status_code}: {response.json()}")
        return

    print("DRY RUN -- nothing deleted. Re-run with --execute to actually delete.\n" if dry_run else "DELETED:\n")
    _print_admin_summary(response.json())


@task
def delete_player_account(c, player_account_id, execute=False):
    """
    Delete one player account and every piece of data anywhere in the app
    tied to that player -- their own rows inside rounds/tournaments
    (including ones shared with players who are staying), club
    membership, posts, handicaps, friend requests, notifications, and
    their uploaded photos.

    Needs a player_account_id (see list-player-accounts), not a player id.

    Defaults to a dry run. Pass --execute to actually delete.

    Usage:
        uv run invoke delete-player-account --player-account-id=<uuid>              # preview
        uv run invoke delete-player-account --player-account-id=<uuid> --execute     # actually delete
    """
    import requests

    dry_run = not execute
    response = requests.delete(
        f"http://localhost:8000/admin/player-accounts/{player_account_id}",
        params={"dry_run": str(dry_run).lower()},
    )

    if response.status_code == 404:
        print(
            f"No player account with id {player_account_id}. "
            "Run `uv run invoke list-player-accounts` to find the right id."
        )
        return
    if response.status_code != 200:
        print(f"Error {response.status_code}: {response.json()}")
        return

    print("DRY RUN -- nothing deleted. Re-run with --execute to actually delete.\n" if dry_run else "DELETED:\n")
    _print_admin_summary(response.json())

@task
def diagnose_tournament_rounds(c, slug):
    """
    Read-only -- never deletes or changes anything. For every tournament
    round in every tournament this club has run, lists every played round
    under it: its real status, whether it's "orphaned" (its tee_time_id
    no longer matches any of the CURRENTLY generated Start Sheet slots for
    that day -- see diagnose_tournament_rounds's own docstring in
    admin_cleanup.py for exactly how this happens), and each player's
    sign-off state.

    Use this when the Start Sheet shows a group as never-started but you
    know they played -- it'll surface the real round (with its id) even
    though the Start Sheet itself can no longer find it.

    Usage:
        uv run invoke diagnose-tournament-rounds --slug=senco-squad
    """
    import requests

    response = requests.get(f"http://localhost:8000/admin/clubs/{slug}/diagnose-rounds")

    if response.status_code == 404:
        print(f"No club with slug '{slug}'. Run `uv run invoke list-clubs` to check the exact slug.")
        return
    if response.status_code != 200:
        print(f"Error {response.status_code}: {response.json()}")
        return

    data = response.json()
    tournament_rounds = data.get("tournament_rounds", [])
    if not tournament_rounds:
        print("No tournament rounds with any played rounds under them for this club.")
        return

    for tr in tournament_rounds:
        print(
            f"\n{tr['tournament_name']} -- Round {tr['round_number']} ({tr['round_date']})  "
            f"[tournament_round_id={tr['tournament_round_id']}, currently {tr['current_slot_count']} slot(s) generated]"
        )
        for r in tr["rounds"]:
            flag = "  <-- ORPHANED: tee_time_id no longer matches a current Start Sheet slot" if r["orphaned"] else ""
            print(f"  round_id={r['round_id']}  status={r['status']}  completed_at={r['completed_at']}{flag}")
            for p in r["players"]:
                signed = "signed off" if p["signed_off"] else "NOT signed off"
                print(f"      {p['name']:<20} {p['membership_status']:<10} {signed}")