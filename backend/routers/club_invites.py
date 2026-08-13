# target path: backend/routers/club_invites.py (new file)
from fastapi import APIRouter, HTTPException, status

from backend.models.club_invite import ClubInviteCreate
from backend.services.club_invites import (
    AlreadyInvitedError,
    AlreadyMemberError,
    ClubNotFoundError,
    NotClubAdminError,
    NotInviteRecipientError,
    NotInviteSenderError,
    PlayerNotFoundError,
    cancel_club_invite,
    list_pending_invites_for_club,
    list_pending_invites_for_player,
    respond_to_club_invite,
    send_club_invite,
)

router = APIRouter(prefix="/club-invites", tags=["club invites"])


@router.post("/", status_code=status.HTTP_201_CREATED)
def send_club_invite_route(payload: ClubInviteCreate):
    try:
        return send_club_invite(str(payload.club_id), str(payload.inviter_id), str(payload.invitee_id))
    except ClubNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except NotClubAdminError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except PlayerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except (AlreadyMemberError, AlreadyInvitedError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/player/{player_id}")
def list_pending_invites_for_player_route(player_id: str):
    return list_pending_invites_for_player(player_id)


@router.get("/club/{club_id}")
def list_pending_invites_for_club_route(club_id: str):
    return list_pending_invites_for_club(club_id)


@router.post("/{invite_id}/accept")
def accept_club_invite_route(invite_id: str, player_id: str):
    try:
        updated = respond_to_club_invite(invite_id, player_id, accept=True)
    except NotInviteRecipientError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club invite not found")
    return updated


@router.post("/{invite_id}/decline")
def decline_club_invite_route(invite_id: str, player_id: str):
    try:
        updated = respond_to_club_invite(invite_id, player_id, accept=False)
    except NotInviteRecipientError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club invite not found")
    return updated


@router.delete("/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_club_invite_route(invite_id: str, admin_id: str):
    try:
        cancelled = cancel_club_invite(invite_id, admin_id)
    except NotInviteSenderError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if not cancelled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club invite not found")