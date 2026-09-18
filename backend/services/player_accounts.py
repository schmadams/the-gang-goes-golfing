# target path: backend/services/player_accounts.py
from postgrest.exceptions import APIError

from backend.database import supabase
from backend.models.player_account import GoogleAccountSignIn, PlayerAccountCreate


class DuplicateAccountError(Exception):
    """Raised when the email or player_id is already linked to an account."""


def create_player_account(account: PlayerAccountCreate) -> dict:
    payload = {
        "email": account.email,
        "name": account.name,
        "player_id": str(account.player_id),
    }

    try:
        response = (
            supabase
            .table("player_accounts")
            .insert(payload)
            .execute()
        )
    except APIError as exc:
        # Postgres unique-violation error code
        if exc.code == "23505":
            if "player_accounts_player_id_unique" in (exc.message or ""):
                raise DuplicateAccountError(
                    f"Player {account.player_id} already has an account."
                ) from exc
            if "email" in (exc.message or ""):
                raise DuplicateAccountError(
                    f"An account already exists for {account.email}."
                ) from exc
            raise DuplicateAccountError("This account already exists.") from exc
        raise

    return response.data[0]


def get_account_by_email(email: str) -> dict | None:
    response = (
        supabase
        .table("player_accounts")
        .select("*")
        .eq("email", email)
        .maybe_single()
        .execute()
    )

    # Some versions of the Supabase client return None outright (rather than
    # a response object with data=None) when maybe_single() finds no match.
    if response is None:
        return None

    return response.data


def get_account_by_google_id(google_id: str) -> dict | None:
    """Same shape/caveats as get_account_by_email -- looked up first on
    every Google sign-in (see sign_in_with_google) since it's the
    strongest possible match: a google_id can only ever have been written
    onto an account by a previous successful sign-in from that exact
    Google account, unlike email which this app never independently
    verifies on its own."""
    response = (
        supabase
        .table("player_accounts")
        .select("*")
        .eq("google_id", google_id)
        .maybe_single()
        .execute()
    )

    if response is None:
        return None

    return response.data


def link_google_id(account_id, google_id: str) -> dict:
    """Attaches a google_id to an existing player_accounts row found by
    email (see sign_in_with_google) -- this is the actual "link to
    existing account" behavior: no new players/player_accounts row gets
    created, the old email-only account just gains a second way in. Safe
    to do without any extra verification step because the ID token this
    google_id came from was already checked against Google's own signing
    keys before this is ever called (see auth_google.py's callback), and
    Google only issues a token for an email it has itself verified --
    id_token's email_verified claim is checked there before this point is
    ever reached."""
    response = (
        supabase
        .table("player_accounts")
        .update({"google_id": google_id})
        .eq("id", str(account_id))
        .execute()
    )
    return response.data[0]


def sign_in_with_google(payload: GoogleAccountSignIn) -> dict:
    """The one entry point frontend/src/auth_google.py's callback route
    calls once it's verified a Google ID token -- three cases, tried in
    order:

    1. An account already has this exact google_id -- they've signed in
       with Google before, just return it.
    2. No account has this google_id yet, but one exists with this email
       -- an old email-only account made before Google sign-in existed
       (or one an admin pre-added ahead of a player registering
       themselves, see this module's original
       attach the google_id to that existing row rather than creating a
       duplicate. See link_google_id's own docstring for why this is
       safe without asking the person to first prove they own the old
       account some other way.
    3. Neither matches -- genuinely new person. Creates both a `players`
       row (the roster/handicap identity) and a `player_accounts` row in
       one go, with google_id already set, mirroring what signin.py's
       manual "Create account" flow does across two separate API calls --
       collapsed into one here since Google already supplied a verified
       name, there's no separate registration form to fill in first."""
    existing_by_google = get_account_by_google_id(payload.google_id)
    if existing_by_google:
        return existing_by_google

    existing_by_email = get_account_by_email(payload.email)
    if existing_by_email:
        return link_google_id(existing_by_email["id"], payload.google_id)

    # Local import -- avoids a module-load-time circular import between
    # players.py and player_accounts.py (same reasoning as every other
    # local-import-for-a-cross-service-call in this codebase, e.g.
    # tournaments.py's create_tournament_post call).
    from backend.services.players import create_player

    player = create_player(payload.first_name, payload.surname)

    account_payload = {
        "email": payload.email,
        "name": f"{payload.first_name} {payload.surname}",
        "player_id": player["id"],
        "google_id": payload.google_id,
    }

    try:
        response = (
            supabase
            .table("player_accounts")
            .insert(account_payload)
            .execute()
        )
    except APIError as exc:
        # Race condition: someone else's request created a matching
        # account (by email or google_id) between the lookups above and
        # this insert. Re-fetch by whichever this collided on rather than
        # surfacing a raw 500 -- a second concurrent Google sign-in from
        # the same person should still just log them in.
        if exc.code == "23505":
            retry = get_account_by_google_id(payload.google_id) or get_account_by_email(payload.email)
            if retry:
                return retry
        raise

    return response.data[0]