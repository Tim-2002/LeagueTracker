"""
FastAPI Backend-Server für die LeagueTracker Web-App.
Startet unter http://127.0.0.1:8000
"""

import os
import sys
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

# Füge backend-Verzeichnis zum Python-Pfad hinzu
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(BACKEND_DIR)
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from stats_config import CATEGORIES, STATS_CONFIG
from database import (
    init_db,
    get_favorites,
    set_favorites,
    toggle_favorite,
    get_settings,
    save_settings,
    get_available_filters,
    get_match_count,
    compute_timeseries,
    compare_players,
    clear_demo_matches,
)
from riot_client import sync_riot_matches, generate_demo_matches

app = FastAPI(title="LeagueTracker - Zeitverlaufs- & Fortschritts-Tracker")


class ToggleFavoriteRequest(BaseModel):
    stat_id: str


class UpdateFavoritesRequest(BaseModel):
    stat_ids: List[str]


class SyncRequest(BaseModel):
    game_name: str
    tag_line: str
    region: str = "EUW1"
    api_key: str
    count: int = 50
    queue: str = "420"
    remove_demo: bool = True


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    counts = get_match_count()
    if counts["total"] == 0:
        generate_demo_matches(80)


@app.get("/api/meta")
def api_get_meta():
    return {
        "categories": CATEGORIES,
        "stats_config": STATS_CONFIG,
        "favorites": get_favorites(),
        "filters": get_available_filters(),
        "settings": get_settings(),
    }


@app.get("/api/timeseries")
def api_get_timeseries(
    puuid: str = Query("ALL"),
    champion: str = Query("ALL"),
    role: str = Query("ALL"),
    queue: str = Query("ALL"),
    group_size: int = Query(5, ge=1, le=100),
    mode: str = Query("blocks"),
    range_mode: str = Query("count"),
    limit_games: Optional[int] = Query(None, ge=1, le=10000),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    q_id: Optional[int] = None
    if queue and queue != "ALL":
        try:
            q_id = int(queue)
        except ValueError:
            q_id = None

    return compute_timeseries(
        puuid=puuid,
        champion=champion,
        role=role,
        queue_id=q_id,
        group_size=group_size,
        mode=mode,
        range_mode=range_mode,
        limit_games=limit_games,
        date_from=date_from,
        date_to=date_to,
    )


@app.get("/api/compare")
def api_compare_players(
    puuid_a: str = Query(...),
    puuid_b: str = Query(...),
    champion: str = Query("ALL"),
    role: str = Query("ALL"),
    queue: str = Query("ALL"),
    group_size: int = Query(5, ge=1, le=100),
    mode: str = Query("blocks"),
    range_mode: str = Query("count"),
    limit_games: Optional[int] = Query(None, ge=1, le=10000),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    q_id: Optional[int] = None
    if queue and queue != "ALL":
        try:
            q_id = int(queue)
        except ValueError:
            q_id = None

    return compare_players(
        puuid_a=puuid_a,
        puuid_b=puuid_b,
        champion=champion,
        role=role,
        queue_id=q_id,
        group_size=group_size,
        mode=mode,
        range_mode=range_mode,
        limit_games=limit_games,
        date_from=date_from,
        date_to=date_to,
    )


@app.post("/api/favorites/toggle")
def api_toggle_favorite(req: ToggleFavoriteRequest):
    try:
        return toggle_favorite(req.stat_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/favorites")
def api_update_favorites(req: UpdateFavoritesRequest):
    updated = set_favorites(req.stat_ids)
    return {"favorites": updated}


@app.post("/api/demo")
def api_load_demo():
    num = generate_demo_matches(80)
    return {
        "status": "ok",
        "generated": num,
        "filters": get_available_filters(),
    }


@app.delete("/api/demo")
def api_clear_demo():
    clear_demo_matches()
    return {
        "status": "ok",
        "filters": get_available_filters(),
    }


@app.post("/api/sync")
def api_sync_riot(req: SyncRequest):
    save_settings(
        {
            "game_name": req.game_name.strip(),
            "tag_line": req.tag_line.strip().lstrip("#"),
            "region": req.region.strip().upper(),
            "api_key": req.api_key.strip(),
            "sync_count": str(req.count),
            "sync_queue": str(req.queue),
        }
    )
    q_id: Optional[int] = None
    if req.queue and req.queue != "ALL":
        try:
            q_id = int(req.queue)
        except ValueError:
            q_id = None

    try:
        res = sync_riot_matches(
            game_name=req.game_name,
            tag_line=req.tag_line,
            region=req.region,
            api_key=req.api_key,
            count=req.count,
            queue_id=q_id,
        )
        if req.remove_demo and res["imported"] > 0:
            clear_demo_matches()
        res["filters"] = get_available_filters()
        return res
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Fehler beim Riot-API-Abruf: {ex}")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


if __name__ == "__main__":
    init_db()
    if get_match_count()["total"] == 0:
        generate_demo_matches(80)
    uvicorn.run(app, host="127.0.0.1", port=8000)
