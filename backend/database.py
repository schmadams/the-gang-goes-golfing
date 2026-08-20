# target path: backend/database.py (full replacement)
from pathlib import Path
import os

from dotenv import load_dotenv
from supabase import create_client, Client

# Local dev convenience only -- your Windows machine keeps t3g_sbdb_URL/
# t3g_sbdb_KEY in a user-level .env file (see README), so this loads it if
# present. On a real host (Railway, etc.) there is no such file -- the
# platform injects environment variables directly into the process before
# Python ever starts, so ENV_FILE.exists() is simply False there and this
# block is skipped entirely. override=False means even if some other host
# happens to have an unrelated file at this path, it can never clobber
# variables the platform already set for real.
ENV_FILE = Path.home() / ".env"
if ENV_FILE.exists():
    load_dotenv(dotenv_path=ENV_FILE, override=False)

SUPABASE_URL = os.getenv("t3g_sbdb_URL")
SUPABASE_KEY = os.getenv("t3g_sbdb_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise EnvironmentError(
        "Missing t3g_sbdb_URL or t3g_sbdb_KEY.\n"
        f"Checked a user-level .env at: {ENV_FILE} (exists: {ENV_FILE.exists()})\n"
        "and the process environment directly -- on a deployed host, set "
        "these as real environment variables in that host's dashboard "
        "rather than adding a .env file.\n"
        f"t3g_sbdb_URL found: {bool(SUPABASE_URL)}\n"
        f"t3g_sbdb_KEY found: {bool(SUPABASE_KEY)}"
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)