# target path: backend/main.py (full replacement)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routers import players, clubs, club_players, club_invites, handicaps, player_accounts, courses, rounds, friends, tournaments

app = FastAPI(title="The Gang Goes Golfing API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(players.router)
app.include_router(clubs.router)
app.include_router(club_players.router)
app.include_router(club_invites.router)
app.include_router(handicaps.router)
app.include_router(player_accounts.router)
app.include_router(courses.router)
app.include_router(rounds.router)
app.include_router(friends.router)
app.include_router(tournaments.router)


@app.get("/")
def root():
    return {"message": "The Gang Goes Golfing API is running"}