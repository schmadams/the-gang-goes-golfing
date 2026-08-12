# target path: backend/routers/friends.py (new file)
from fastapi import APIRouter, HTTPException, status

from backend.models.friend import FriendRequestCreate
from backend.services.friends import (
    AlreadyFriendsOrPendingError,
    NotRequestRecipientError,
    SelfFriendRequestError,
    list_friends,
    list_pending_requests,
    respond_to_friend_request,
    send_friend_request,
)

router = APIRouter(prefix="/friends", tags=["friends"])


@router.post("/requests", status_code=status.HTTP_201_CREATED)
def send_friend_request_route(payload: FriendRequestCreate):
    try:
        return send_friend_request(str(payload.requester_id), str(payload.recipient_id))
    except SelfFriendRequestError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except AlreadyFriendsOrPendingError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/requests/{player_id}")
def list_pending_requests_route(player_id: str):
    return list_pending_requests(player_id)


@router.post("/requests/{request_id}/accept")
def accept_friend_request_route(request_id: str, player_id: str):
    try:
        updated = respond_to_friend_request(request_id, player_id, accept=True)
    except NotRequestRecipientError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Friend request not found")
    return updated


@router.post("/requests/{request_id}/decline")
def decline_friend_request_route(request_id: str, player_id: str):
    try:
        updated = respond_to_friend_request(request_id, player_id, accept=False)
    except NotRequestRecipientError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Friend request not found")
    return updated


@router.get("/player/{player_id}")
def list_friends_route(player_id: str):
    return list_friends(player_id)