from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.routers import players, groups, group_players

app = FastAPI(title="The Gang Goes Golfing API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(players.router)
app.include_router(groups.router)
app.include_router(group_players.router)


@app.get("/")
def root():
    return {"message": "The Gang Goes Golfing API is running"}