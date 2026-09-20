# target path: backend/services/admin_cleanup.py (new file)
"""Admin/one-off cleanup operations -- not part of the normal user-facing
app surface (no player ever reaches these through ordinary use), but
built as real, reusable functions rather than throwaway SQL, so the same
cascade logic can be re-run safely later (another unwanted club shows up
during dev, another account needs removing) instead of being
reconstructed from scratch each time.

Both operations below are genuinely destructive and irreversible -- no
soft-delete, no undo, no Supabase point-in-time-recovery substitute.
Every function defaults to dry_run=True: it walks the exact same logic
and returns the exact same summary shape, but only SELECTs (counts what
WOULD be deleted) instead of actually deleting anything. Always run with
the default first, read the counts, and only pass dry_run=False once
they look right -- see backend/routers/admin.py and tasks.py's own
invoke tasks for how this is meant to be driven.

backend/routers/admin.py's routes wrap these with nothing beyond plain
HTTP access -- this app has no admin-role/auth concept at all yet, so
these are "reachable by anyone who can reach your backend", not gated
behind a real permission check. Fine for a private trip app on a private
Railway URL operated only by you today; would need real auth in front of
these specifically before this app is ever opened up more broadly.
"""
from backend.database import supabase

ROUND_POST_PHOTO_BUCKET = "round-post-photos"
PROFILE_PICTURE_BUCKET = "profile-pictures"
CLUB_PHOTO_BUCKET = "club-photos"


class ClubNotFoundError(Exception):
    """Raised when the club slug passed to reset_clubs_keep_one doesn't
    match any real club -- deliberately loud (never silently no-ops) since
    the whole point of that function is "delete every club except this
    one", and proceeding with no valid "this one" would delete everything."""


class PlayerAccountNotFoundError(Exception):
    """Raised when the id passed to delete_player_account_cascade doesn't
    match any real player_accounts row."""


def _new_counts() -> dict:
    return {
        "players": 0,
        "player_accounts": 0,
        "player_handicaps": 0,
        "friend_requests": 0,
        "notifications": 0,
        "club_players": 0,
        "club_invites": 0,
        "club_posts": 0,
        "clubs": 0,
        "clubs_admin_reassigned": 0,
        "tournaments": 0,
        "tournament_entrants": 0,
        "tournament_pairs": 0,
        "tournament_rounds": 0,
        "tournament_tee_times": 0,
        "tournament_tee_time_players": 0,
        "rounds": 0,
        "rounds_reassigned_owner": 0,
        "round_players": 0,
        "round_scores": 0,
        "round_posts": 0,
        "round_post_photos": 0,
        "storage_objects_removed": 0,
    }


def _count_and_maybe_delete(table: str, filters: dict, dry_run: bool) -> int:
    """The one primitive every cascade step below is built from: either
    SELECT id (dry run -- just count what matches) or DELETE (real run),
    both filtered by the same equality conditions, both returning how
    many rows matched. Keeping this as one function is what makes dry_run
    a single default-True parameter threaded through everything rather
    than two divergent code paths that could quietly drift apart."""
    query = supabase.table(table).select("id") if dry_run else supabase.table(table).delete()
    for column, value in filters.items():
        query = query.eq(column, value)
    response = query.execute()
    return len(response.data or [])


def _count_and_maybe_delete_in(table: str, column: str, values: list, dry_run: bool) -> int:
    if not values:
        return 0
    query = supabase.table(table).select("id") if dry_run else supabase.table(table).delete()
    query = query.in_(column, values)
    response = query.execute()
    return len(response.data or [])


def _select_ids(table: str, filters: dict, id_column: str = "id") -> list:
    query = supabase.table(table).select(id_column)
    for column, value in filters.items():
        query = query.eq(column, value)
    response = query.execute()
    return [row[id_column] for row in (response.data or [])]


def _storage_path_from_url(url: str | None, bucket: str) -> str | None:
    """Supabase's get_public_url returns a full URL
    (".../storage/v1/object/public/<bucket>/<path>") -- Storage's own
    .remove() call needs just <path>, not the whole URL. Derived this
    way (split on the bucket's own public-URL marker) instead of
    reconstructing each caller's storage_path convention by hand, so
    this keeps working even if a given upload site's path shape changes
    later."""
    if not url:
        return None
    marker = f"/public/{bucket}/"
    if marker not in url:
        return None
    return url.split(marker, 1)[1]


def _remove_storage_objects(bucket: str, paths: list[str], dry_run: bool, counts: dict) -> None:
    paths = [p for p in paths if p]
    if not paths:
        return
    counts["storage_objects_removed"] += len(paths)
    if dry_run:
        return
    try:
        supabase.storage.from_(bucket).remove(paths)
    except Exception as exc:
        # Best-effort, same "never let a secondary cleanup step block the
        # real action" convention as this codebase's other best-effort
        # call sites (feed posts, notifications) -- a Storage failure
        # here just leaves an orphaned file behind, it shouldn't abort a
        # DB cascade that's otherwise already committed.
        print(f"[ADMIN_CLEANUP] Failed to remove {len(paths)} object(s) from bucket '{bucket}': {exc}")


def _delete_round_cascade(round_id: str, dry_run: bool, counts: dict) -> None:
    """One whole `rounds` row and everything under it -- scores, player
    rows, its round_post (if it has one) and that post's photos, both the
    DB rows and the actual Storage objects. Called when a round is being
    removed entirely (either because a tournament/club it belongs to is
    being wiped, or because removing one player emptied it out -- see
    delete_player_account_cascade)."""
    counts["round_scores"] += _count_and_maybe_delete("round_scores", {"round_id": round_id}, dry_run)
    counts["round_players"] += _count_and_maybe_delete("round_players", {"round_id": round_id}, dry_run)

    post_ids = _select_ids("round_posts", {"round_id": round_id})
    for post_id in post_ids:
        photos_response = (
            supabase.table("round_post_photos").select("id, image_url").eq("round_id", round_id).execute()
        )
        photo_paths = [
            _storage_path_from_url(row["image_url"], ROUND_POST_PHOTO_BUCKET)
            for row in (photos_response.data or [])
        ]
        _remove_storage_objects(ROUND_POST_PHOTO_BUCKET, photo_paths, dry_run, counts)
        counts["round_post_photos"] += _count_and_maybe_delete("round_post_photos", {"round_id": round_id}, dry_run)
        counts["round_posts"] += _count_and_maybe_delete("round_posts", {"id": post_id}, dry_run)

    counts["rounds"] += _count_and_maybe_delete("rounds", {"id": round_id}, dry_run)


def _delete_tournament_cascade(tournament_id: str, dry_run: bool, counts: dict) -> None:
    """Everything under one tournament: entrants, pairs, every
    tournament_round's tee-times/tee-time-players, and every real
    `rounds` row actually played under one of those tournament rounds
    (full _delete_round_cascade each). Does not touch club_posts -- the
    caller (wipe_all_tournaments) clears tournament/tournament_finalized
    posts globally in one pass afterward instead, since a post's
    tournament reference lives inside its metadata jsonb, not a real FK
    column, so there's nothing here to cascade from."""
    counts["tournament_entrants"] += _count_and_maybe_delete(
        "tournament_entrants", {"tournament_id": tournament_id}, dry_run
    )
    counts["tournament_pairs"] += _count_and_maybe_delete(
        "tournament_pairs", {"tournament_id": tournament_id}, dry_run
    )

    tournament_round_ids = _select_ids("tournament_rounds", {"tournament_id": tournament_id})
    for tournament_round_id in tournament_round_ids:
        tee_time_ids = _select_ids("tournament_tee_times", {"tournament_round_id": tournament_round_id})
        counts["tournament_tee_time_players"] += _count_and_maybe_delete_in(
            "tournament_tee_time_players", "tee_time_id", tee_time_ids, dry_run
        )
        counts["tournament_tee_times"] += _count_and_maybe_delete(
            "tournament_tee_times", {"tournament_round_id": tournament_round_id}, dry_run
        )

        played_round_ids = _select_ids("rounds", {"tournament_round_id": tournament_round_id})
        for round_id in played_round_ids:
            _delete_round_cascade(round_id, dry_run, counts)

    counts["tournament_rounds"] += _count_and_maybe_delete(
        "tournament_rounds", {"tournament_id": tournament_id}, dry_run
    )
    counts["tournaments"] += _count_and_maybe_delete("tournaments", {"id": tournament_id}, dry_run)


def list_player_accounts() -> list[dict]:
    """All player_accounts rows -- exists for exactly one purpose: `uv run
    invoke list-player-accounts` needs a way to show a player_account's
    own id (not the player's id), since delete_player_account_cascade
    takes a player_account_id, and no other endpoint in this app returns
    player_accounts rows in bulk (backend/routers/player_accounts.py only
    ever looks one up by email or creates one)."""
    response = supabase.table("player_accounts").select("*").order("created_at").execute()
    return response.data or []


def wipe_all_tournaments(dry_run: bool = True) -> dict:
    """Deletes every tournament in the whole app (every club, including
    ones not otherwise being touched) -- entrants, pairs, tournament
    rounds, tee times, and every round actually played under any of them.
    Also clears every 'tournament'/'tournament_finalized' club_posts row
    everywhere, so no club's feed is left pointing at a tournament that
    no longer exists.

    tournaments.linked_tournament_id is a self-reference (pairs-format
    tournaments linking to their own roster/pairing source, see
    tournaments.py's _resolve_source_tournament_id) -- every row gets
    that column nulled out FIRST, before any row is deleted, so deleting
    tournament A while tournament B's linked_tournament_id still points
    at A can never fail a foreign-key check partway through."""
    counts = _new_counts()

    tournament_ids = _select_ids("tournaments", {})

    if not dry_run and tournament_ids:
        supabase.table("tournaments").update({"linked_tournament_id": None}).neq(
            "id", "00000000-0000-0000-0000-000000000000"
        ).execute()

    for tournament_id in tournament_ids:
        _delete_tournament_cascade(tournament_id, dry_run, counts)

    tournament_post_ids = (
        supabase.table("club_posts").select("id").in_("post_type", ["tournament", "tournament_finalized"]).execute()
    )
    post_ids = [row["id"] for row in (tournament_post_ids.data or [])]
    counts["club_posts"] += _count_and_maybe_delete_in("club_posts", "id", post_ids, dry_run)

    return counts


def reset_clubs_keep_one(keep_club_slug: str | None, dry_run: bool = True) -> dict:
    """Deletes every club except the one matching keep_club_slug, and
    (via wipe_all_tournaments) every tournament everywhere -- including
    the kept club's own. The kept club's regular membership, casual
    rounds, and non-tournament feed posts are left completely alone.

    keep_club_slug may be None, meaning "keep nothing" -- every club in
    the database is deleted, with no exception. This is deliberately a
    separate, explicit code path rather than something a caller can
    trigger by leaving keep_club_slug blank/typo'd by accident -- see
    reset_clubs_route's own delete_all flag for the guard that requires
    a caller to opt into this on purpose.

    Order matters: tournaments (and everything nested under them, per
    club) are wiped FIRST, so that by the time a to-be-deleted club's
    casual `rounds` are swept by club_id, nothing tournament-linked is
    left to double-count or conflict with."""
    if keep_club_slug is None:
        keep_club_id = None
    else:
        keep_club = (
            supabase.table("clubs").select("id, slug").eq("slug", keep_club_slug).maybe_single().execute()
        )
        keep_club_data = keep_club.data if keep_club is not None else None
        if not keep_club_data:
            raise ClubNotFoundError(f"No club with slug '{keep_club_slug}'. Nothing was deleted.")
        keep_club_id = keep_club_data["id"]

    counts = wipe_all_tournaments(dry_run=dry_run)

    if keep_club_id is None:
        other_club_ids = [row["id"] for row in (supabase.table("clubs").select("id").execute().data or [])]
    else:
        other_club_ids = [
            row["id"]
            for row in (supabase.table("clubs").select("id").neq("id", keep_club_id).execute().data or [])
        ]

    for club_id in other_club_ids:
        round_ids = _select_ids("rounds", {"club_id": club_id})
        for round_id in round_ids:
            _delete_round_cascade(round_id, dry_run, counts)

        counts["club_players"] += _count_and_maybe_delete("club_players", {"club_id": club_id}, dry_run)
        counts["club_invites"] += _count_and_maybe_delete("club_invites", {"club_id": club_id}, dry_run)

        # Excludes tournament/tournament_finalized posts on purpose --
        # wipe_all_tournaments (called above) already swept those
        # globally by post_type. Re-matching this club's posts by
        # club_id alone (with no post_type filter) would re-select the
        # very same rows: harmless on a real run (they're already gone,
        # so this just matches nothing for them), but in dry_run mode
        # nothing has actually been removed between the two sweeps, so
        # they'd get counted twice. Same double-counting failure mode as
        # the one fixed in delete_player_account_cascade -- see that
        # function's own comment for the general principle.
        club_post_rows = (
            supabase.table("club_posts").select("id, post_type").eq("club_id", club_id).execute().data or []
        )
        non_tournament_post_ids = [
            row["id"] for row in club_post_rows if row.get("post_type") not in ("tournament", "tournament_finalized")
        ]
        counts["club_posts"] += _count_and_maybe_delete_in("club_posts", "id", non_tournament_post_ids, dry_run)

        club_response = supabase.table("clubs").select("photo_url").eq("id", club_id).maybe_single().execute()
        club_data = club_response.data if club_response is not None else None
        photo_path = _storage_path_from_url((club_data or {}).get("photo_url"), CLUB_PHOTO_BUCKET)
        _remove_storage_objects(CLUB_PHOTO_BUCKET, [photo_path], dry_run, counts)

        counts["clubs"] += _count_and_maybe_delete("clubs", {"id": club_id}, dry_run)

    return counts


def delete_club_cascade(club_slug: str, dry_run: bool = True) -> dict:
    """Deletes ONE club and everything scoped to it: its own tournaments
    (entrants, pairs, tournament rounds, tee times, every round played
    under any of them), its own casual (non-tournament) rounds, its
    membership, invites, feed posts, and its uploaded photo. Every other
    club -- its clubs, tournaments, rounds, posts -- is left completely
    untouched. This is the "delete one club at a time" primitive;
    reset_clubs_keep_one is the separate "wipe down to one club / wipe
    everything" bulk operation and isn't involved here at all.

    tournaments.linked_tournament_id is a self-reference (pairs-format
    tournaments linking to their own roster/pairing source elsewhere in
    the app -- see tournaments.py's _resolve_source_tournament_id). Any
    OTHER club's tournament could theoretically link to one of this
    club's tournaments as its source, so those references get nulled out
    first, same as wipe_all_tournaments does, rather than assuming links
    only ever point within one club."""
    club = supabase.table("clubs").select("id, slug, photo_url").eq("slug", club_slug).maybe_single().execute()
    club_data = club.data if club is not None else None
    if not club_data:
        raise ClubNotFoundError(f"No club with slug '{club_slug}'. Nothing was deleted.")
    club_id = club_data["id"]

    counts = _new_counts()

    tournament_ids = _select_ids("tournaments", {"club_id": club_id})

    if not dry_run and tournament_ids:
        supabase.table("tournaments").update({"linked_tournament_id": None}).in_(
            "linked_tournament_id", tournament_ids
        ).execute()

    for tournament_id in tournament_ids:
        _delete_tournament_cascade(tournament_id, dry_run, counts)

    # Casual rounds tagged to this club (tournament rounds never carry
    # club_id -- see rounds.py's start_tournament_round round_payload --
    # so this can't re-match anything the loop above already handled).
    casual_round_ids = _select_ids("rounds", {"club_id": club_id})
    for round_id in casual_round_ids:
        _delete_round_cascade(round_id, dry_run, counts)

    counts["club_players"] += _count_and_maybe_delete("club_players", {"club_id": club_id}, dry_run)
    counts["club_invites"] += _count_and_maybe_delete("club_invites", {"club_id": club_id}, dry_run)

    # Unlike reset_clubs_keep_one (which sweeps club_posts globally by
    # post_type across every club BEFORE this point, and so has to
    # exclude those already-handled rows here to avoid recounting them),
    # nothing earlier in this function has touched club_posts at all --
    # _delete_tournament_cascade explicitly doesn't (see its own
    # docstring). So one plain club_id-scoped sweep, covering every post
    # type including tournament/tournament_finalized, is both correct
    # and the only place these rows get counted.
    counts["club_posts"] += _count_and_maybe_delete("club_posts", {"club_id": club_id}, dry_run)

    photo_path = _storage_path_from_url(club_data.get("photo_url"), CLUB_PHOTO_BUCKET)
    _remove_storage_objects(CLUB_PHOTO_BUCKET, [photo_path], dry_run, counts)

    counts["clubs"] += _count_and_maybe_delete("clubs", {"id": club_id}, dry_run)

    return counts


def delete_player_account_cascade(player_account_id: str, dry_run: bool = True) -> dict:
    """Deletes one player_accounts row, its underlying players row, and
    every other row anywhere in the app that references that player_id --
    including their own rows inside rounds/tournaments they shared with
    players who are staying (their scores disappear from that round;
    everyone else's own data in it is untouched). If removing them empties
    a round out entirely, the round itself is deleted too. If they owned a
    round (rounds.player_id) or admin'd a club (clubs.club_admin) that
    isn't otherwise being removed, ownership is reassigned to another
    remaining participant/member rather than left dangling.

    Returns a counts summary (see _new_counts) plus the player's own
    name/email under "player_name"/"player_email" so a caller can log or
    display who this was, even after dry_run=True (nothing to look up
    again afterward once it's actually gone)."""
    account_response = (
        supabase.table("player_accounts").select("*").eq("id", player_account_id).maybe_single().execute()
    )
    account = account_response.data if account_response is not None else None
    if not account:
        raise PlayerAccountNotFoundError(f"No player account with id {player_account_id}.")

    player_id = account["player_id"]
    player_response = supabase.table("players").select("*").eq("id", player_id).maybe_single().execute()
    player = (player_response.data if player_response is not None else None) or {}

    counts = _new_counts()
    counts["player_name"] = f"{player.get('first_name', '')} {player.get('surname', '')}".strip() or None
    counts["player_email"] = account.get("email")

    # --- Rounds this player took part in -------------------------------
    round_ids = _select_ids("round_players", {"player_id": player_id}, id_column="round_id")
    for round_id in round_ids:
        # Emptiness has to be checked BEFORE anything for this round is
        # deleted, and the two outcomes below have to be fully separate
        # paths, not "delete my own rows, then maybe also cascade-delete
        # the whole round" -- doing both would double-delete (harmless in
        # a real run, since deleting an already-gone row is a no-op) but
        # double-COUNT in dry_run specifically, since dry_run never
        # actually removes anything from the DB between the two checks.
        # A dry-run preview that overcounts is exactly the kind of thing
        # that undermines trusting it before a real, irreversible run.
        remaining_players_response = (
            supabase.table("round_players").select("player_id").eq("round_id", round_id).neq("player_id", player_id).execute()
        )
        remaining_player_ids = [row["player_id"] for row in (remaining_players_response.data or [])]

        if not remaining_player_ids:
            # Removing this player emptied the round out -- nothing left
            # to reassign to, so the round itself goes too. One single
            # call handles every row under it (scores, players, post,
            # photos + their Storage objects) -- nothing here duplicates
            # that work.
            _delete_round_cascade(round_id, dry_run, counts)
            continue

        counts["round_scores"] += _count_and_maybe_delete(
            "round_scores", {"round_id": round_id, "player_id": player_id}, dry_run
        )
        counts["round_players"] += _count_and_maybe_delete(
            "round_players", {"round_id": round_id, "player_id": player_id}, dry_run
        )

        photos_response = (
            supabase.table("round_post_photos").select("id, image_url").eq("round_id", round_id).eq("author_id", player_id).execute()
        )
        own_photo_paths = [
            _storage_path_from_url(row["image_url"], ROUND_POST_PHOTO_BUCKET)
            for row in (photos_response.data or [])
        ]
        _remove_storage_objects(ROUND_POST_PHOTO_BUCKET, own_photo_paths, dry_run, counts)
        counts["round_post_photos"] += _count_and_maybe_delete(
            "round_post_photos", {"round_id": round_id, "author_id": player_id}, dry_run
        )

        round_response = supabase.table("rounds").select("player_id").eq("id", round_id).maybe_single().execute()
        round_data = round_response.data if round_response is not None else None
        if round_data and round_data.get("player_id") == player_id:
            new_owner_id = remaining_player_ids[0]
            counts["rounds_reassigned_owner"] += 1
            if not dry_run:
                supabase.table("rounds").update({"player_id": new_owner_id}).eq("id", round_id).execute()

        if not dry_run:
            # Best-effort -- strips this player out of the round_post's
            # own player_ids list (used to gate who can add a retroactive
            # photo, see round_posts.add_round_post_photo) so a deleted
            # player doesn't linger in that list forever. Cosmetic/
            # permission hygiene, not something a missing round_post
            # should ever block on.
            try:
                post_response = supabase.table("round_posts").select("id, metadata").eq("round_id", round_id).maybe_single().execute()
                post = post_response.data if post_response is not None else None
                if post:
                    metadata = post.get("metadata") or {}
                    player_ids = [pid for pid in metadata.get("player_ids", []) if pid != player_id]
                    metadata["player_ids"] = player_ids
                    supabase.table("round_posts").update({"metadata": metadata}).eq("id", post["id"]).execute()
            except Exception as exc:
                print(f"[ADMIN_CLEANUP] Failed to strip player {player_id} from round_post metadata for round={round_id}: {exc}")

    # --- Tournament participation ---------------------------------------
    counts["tournament_entrants"] += _count_and_maybe_delete("tournament_entrants", {"player_id": player_id}, dry_run)

    pair_ids_a = _select_ids("tournament_pairs", {"player_id_a": player_id})
    pair_ids_b = _select_ids("tournament_pairs", {"player_id_b": player_id})
    counts["tournament_pairs"] += _count_and_maybe_delete_in(
        "tournament_pairs", "id", list(set(pair_ids_a + pair_ids_b)), dry_run
    )

    counts["tournament_tee_time_players"] += _count_and_maybe_delete(
        "tournament_tee_time_players", {"player_id": player_id}, dry_run
    )

    # --- Club membership --------------------------------------------------
    club_ids = _select_ids("club_players", {"player_id": player_id}, id_column="club_id")
    for club_id in club_ids:
        club_response = supabase.table("clubs").select("club_admin").eq("id", club_id).maybe_single().execute()
        club_data = club_response.data if club_response is not None else None
        if club_data and club_data.get("club_admin") == player_id:
            other_member_response = (
                supabase.table("club_players").select("player_id").eq("club_id", club_id).neq("player_id", player_id).limit(1).execute()
            )
            other_members = other_member_response.data or []
            new_admin_id = other_members[0]["player_id"] if other_members else None
            counts["clubs_admin_reassigned"] += 1
            if not dry_run:
                supabase.table("clubs").update({"club_admin": new_admin_id}).eq("id", club_id).execute()

    counts["club_players"] += _count_and_maybe_delete("club_players", {"player_id": player_id}, dry_run)

    invite_ids_out = _select_ids("club_invites", {"inviter_id": player_id})
    invite_ids_in = _select_ids("club_invites", {"invitee_id": player_id})
    counts["club_invites"] += _count_and_maybe_delete_in(
        "club_invites", "id", list(set(invite_ids_out + invite_ids_in)), dry_run
    )

    counts["club_posts"] += _count_and_maybe_delete("club_posts", {"author_id": player_id}, dry_run)

    # --- Everything else keyed directly on this player -------------------
    counts["player_handicaps"] += _count_and_maybe_delete("player_handicaps", {"player_id": player_id}, dry_run)

    req_ids_out = _select_ids("friend_requests", {"requester_id": player_id})
    req_ids_in = _select_ids("friend_requests", {"recipient_id": player_id})
    counts["friend_requests"] += _count_and_maybe_delete_in(
        "friend_requests", "id", list(set(req_ids_out + req_ids_in)), dry_run
    )

    counts["notifications"] += _count_and_maybe_delete("notifications", {"player_id": player_id}, dry_run)

    profile_picture_path = _storage_path_from_url(player.get("profile_picture_url"), PROFILE_PICTURE_BUCKET)
    _remove_storage_objects(PROFILE_PICTURE_BUCKET, [profile_picture_path], dry_run, counts)

    counts["player_accounts"] += _count_and_maybe_delete("player_accounts", {"id": player_account_id}, dry_run)
    counts["players"] += _count_and_maybe_delete("players", {"id": player_id}, dry_run)

    return counts


def diagnose_tournament_rounds(club_slug: str) -> dict:
    """Read-only -- no dry_run, nothing here ever mutates anything.
    Built for one specific failure mode: generate_tee_times (the Start
    Sheet's "Generate" action) does a wholesale delete-and-reinsert of a
    tournament_round's tournament_tee_times rows every time it's run
    (see that function's own docstring -- "whatever's there gets thrown
    out and rebuilt"), and it does this with NO check for whether any of
    the slots it's about to delete already has a real `rounds` row
    pointing at it (rounds.tee_time_id is set once, at Start, and is
    never updated afterward).

    So: group starts and plays their round (rounds row created, tied to
    tee_time X) -> admin re-runs Generate for that same day (late add,
    fixing a grouping, whatever) -> tee_time X is deleted and a new slot
    Y is created in its place -> the played round's tee_time_id still
    says X, which no longer exists. The round itself, its scores, and
    its sign-off state are all completely untouched in the `rounds`/
    `round_scores`/`round_players` tables -- this is purely a broken
    display join. But every UI surface that renders the Start Sheet by
    walking the CURRENT tournament_tee_times rows and asking "does any
    round point at this slot" (fetch_live_rounds_by_tee_time) will never
    find it, since it's looking for slot Y and the round says X -- so
    that grouping shows up as never-started even though it was played
    and (possibly) even fully scored.

    For every tournament round in every tournament this club has ever
    run, returns every `rounds` row under it with its real status,
    tee_time_id, whether that tee_time_id is still among the CURRENTLY
    generated slots for that tournament round ("orphaned": true if not),
    and each accepted player's sign-off state -- so a specific missing
    round can be found and diagnosed without guessing."""
    club = supabase.table("clubs").select("id, slug").eq("slug", club_slug).maybe_single().execute()
    club_data = club.data if club is not None else None
    if not club_data:
        raise ClubNotFoundError(f"No club with slug '{club_slug}'.")
    club_id = club_data["id"]

    tournaments = (
        supabase.table("tournaments").select("id, name").eq("club_id", club_id).execute().data or []
    )

    tournament_round_reports = []
    for tournament in tournaments:
        tournament_rounds = (
            supabase
            .table("tournament_rounds")
            .select("id, round_number, round_date")
            .eq("tournament_id", tournament["id"])
            .order("round_number")
            .execute()
            .data
            or []
        )
        for tournament_round in tournament_rounds:
            current_slot_ids = {
                row["id"]
                for row in (
                    supabase.table("tournament_tee_times")
                    .select("id")
                    .eq("tournament_round_id", tournament_round["id"])
                    .execute()
                    .data
                    or []
                )
            }

            played_rounds = (
                supabase
                .table("rounds")
                .select("id, status, tee_time_id, completed_at")
                .eq("tournament_round_id", tournament_round["id"])
                .execute()
                .data
                or []
            )
            if not played_rounds:
                continue

            round_reports = []
            for round_row in played_rounds:
                round_player_rows = (
                    supabase
                    .table("round_players")
                    .select("player_id, status, signed_off_at, players(first_name, surname, nickname)")
                    .eq("round_id", round_row["id"])
                    .execute()
                    .data
                    or []
                )
                players_out = []
                for rp in round_player_rows:
                    player_info = rp.get("players") or {}
                    name = player_info.get("nickname") or (
                        f"{player_info.get('first_name', '')} {player_info.get('surname', '')}".strip()
                    )
                    players_out.append({
                        "player_id": rp["player_id"],
                        "name": name or rp["player_id"],
                        "membership_status": rp["status"],
                        "signed_off": bool(rp.get("signed_off_at")),
                    })

                round_reports.append({
                    "round_id": round_row["id"],
                    "status": round_row["status"],
                    "completed_at": round_row.get("completed_at"),
                    "tee_time_id": round_row.get("tee_time_id"),
                    "orphaned": round_row.get("tee_time_id") not in current_slot_ids,
                    "players": players_out,
                })

            tournament_round_reports.append({
                "tournament_id": tournament["id"],
                "tournament_name": tournament.get("name"),
                "tournament_round_id": tournament_round["id"],
                "round_number": tournament_round.get("round_number"),
                "round_date": tournament_round.get("round_date"),
                "current_slot_count": len(current_slot_ids),
                "rounds": round_reports,
            })

    return {"club_slug": club_slug, "tournament_rounds": tournament_round_reports}

    return counts