from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class HandicapCreate(BaseModel):
    player_id: UUID
    handicap: float
    valid_from: date | None = None
    # Not accepted from the client -- POST /handicaps/ is only ever called
    # from the My Account manual-entry form (see frontend/src/pages/
    # my_account.py's handle_add_handicap), so the router hardcodes
    # source="manual" itself rather than trusting a client-supplied value
    # for something with real scoring implications (tournament min/max
    # gating, Net score math). The WHS-calculated path writes its own
    # source="t3g" rows directly via recalculate_and_store_handicap,
    # entirely separate from this model.


class HandicapResponse(BaseModel):
    id: UUID
    player_id: UUID
    handicap: float
    valid_from: date
    created_at: datetime | None = None
    source: str = "t3g"