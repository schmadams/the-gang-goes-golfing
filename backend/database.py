from pathlib import Path
import os

from dotenv import load_dotenv
from supabase import create_client, Client


ENV_FILE = Path.home() / ".env"

if not ENV_FILE.exists():
    raise FileNotFoundError(f"Expected .env file at: {ENV_FILE}")

load_dotenv(dotenv_path=ENV_FILE, override=True)

SUPABASE_URL = os.getenv("t3g_sbdb_URL")
SUPABASE_KEY = os.getenv("t3g_sbdb_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise EnvironmentError(
        "Missing t3g_sbdb_URL or t3g_sbdb_KEY.\n"
        f"Loaded .env from: {ENV_FILE}\n"
        f"t3g_sbdb_URL found: {bool(SUPABASE_URL)}\n"
        f"t3g_sbdb_KEY found: {bool(SUPABASE_KEY)}"
    )

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)