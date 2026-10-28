"""
Riot Games API Client (Account-V1, Match-V5 & Timeline) sowie realistischer Demo-Datengenerator.
"""

import json
import math
import random
import time
from typing import Dict, Any, List, Optional, Set, Tuple
import urllib.parse
import requests

from database import (
    get_connection,
    insert_match,
    clear_demo_matches,
    upsert_player,
    update_player_rank,
    recompute_player_match_ranks,
    get_settings,
)


REGION_TO_ROUTING = {
    "EUW1": "europe",
    "EUN1": "europe",
    "TR1": "europe",
    "RU": "europe",
    "NA1": "americas",
    "BR1": "americas",
    "LA1": "americas",
    "LA2": "americas",
    "KR": "asia",
    "JP1": "asia",
}

# Vollständige Basis-Liste aller fertigen Legendary-/Haupt-Items (ohne Schuhe/Komponenten)
BASE_COMPLETED_ITEM_IDS: Set[int] = {
    2065, 2501, 2502, 2503, 2504, 2510, 2512, 2517, 2520, 2522, 2523, 2524, 2525, 2526, 2530,
    3001, 3002, 3003, 3004, 3011, 3026, 3031, 3032, 3033, 3036, 3039, 3040, 3041, 3042, 3046,
    3050, 3053, 3065, 3068, 3071, 3072, 3073, 3074, 3075, 3078, 3083, 3084, 3085, 3087, 3089,
    3091, 3094, 3095, 3100, 3102, 3107, 3109, 3110, 3115, 3116, 3118, 3119, 3121, 3124, 3128,
    3131, 3135, 3137, 3139, 3142, 3143, 3146, 3152, 3153, 3156, 3157, 3161, 3165, 3179, 3181,
    3190, 3193, 3222, 3302, 3504, 3508, 3742, 3748, 3814, 4004, 4005, 4401, 4402, 4403, 4628,
    4629, 4633, 4636, 4637, 4643, 4644, 4645, 4646, 6035, 6333, 6609, 6610, 6616, 6617, 6620,
    6621, 6630, 6631, 6632, 6653, 6655, 6656, 6657, 6662, 6664, 6665, 6667, 6671, 6672, 6673,
    6675, 6676, 6691, 6692, 6693, 6694, 6695, 6696, 6697, 6698, 6699, 6700, 6701, 8001, 8010,
    8020,
}

# Tear-Items, die sich bei 360 Stacks automatisch transformieren, werden auf ihr Basis-Hauptitem normiert
TEAR_TRANSFORM_MAP: Dict[int, int] = {
    3040: 3003,  # Seraph's Embrace -> Archangel's Staff
    3042: 3004,  # Muramana -> Manamune
    3121: 3119,  # Fimbulwinter -> Winter's Approach
    2530: 2526,  # Diadem of Songs -> Whispering Circlet
}

_CACHED_COMPLETED_ITEM_IDS: Optional[Set[int]] = None


def _get_completed_item_ids() -> Set[int]:
    """
    Liefert die Menge aller fertigen Haupt-Items (Legendary/Full Items, ohne Schuhe & Komponenten).
    Reichert die eingebaute Liste einmalig dynamisch über Riot Data Dragon an, falls online verfügbar.
    """
    global _CACHED_COMPLETED_ITEM_IDS
    if _CACHED_COMPLETED_ITEM_IDS is not None:
        return _CACHED_COMPLETED_ITEM_IDS

    ids = set(BASE_COMPLETED_ITEM_IDS)
    try:
        v_resp = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=4)
        if v_resp.status_code == 200:
            latest_ver = v_resp.json()[0]
            i_resp = requests.get(
                f"https://ddragon.leagueoflegends.com/cdn/{latest_ver}/data/en_US/item.json",
                timeout=5,
            )
            if i_resp.status_code == 200:
                items_data = i_resp.json().get("data", {})
                for k, val in items_data.items():
                    if not k.isdigit():
                        continue
                    total_gold = val.get("gold", {}).get("total", 0)
                    tags = val.get("tags", []) or []
                    if "Boots" in tags or "Consumable" in tags:
                        continue
                    has_from = bool(val.get("from"))
                    into_list = val.get("into") or []
                    has_purchasable_into = any(
                        items_data.get(str(nxt), {}).get("gold", {}).get("purchasable", False)
                        for nxt in into_list
                    )
                    if has_from and not has_purchasable_into and total_gold >= 1900:
                        ids.add(int(k))
    except Exception:
        pass

    _CACHED_COMPLETED_ITEM_IDS = ids
    return ids


def _canonical_completed_item_id(raw_item_id: int, completed_ids: Set[int]) -> Optional[int]:
    """Prüft, ob eine Item-ID ein fertiges Haupt-Item ist, und gibt die kanonische Item-ID zurück."""
    if raw_item_id <= 0:
        return None
    base_id = raw_item_id % 10000 if 320000 <= raw_item_id < 330000 else raw_item_id
    base_id = TEAR_TRANSFORM_MAP.get(base_id, base_id)
    if base_id in completed_ids or raw_item_id in completed_ids:
        return base_id
    return None


def _extract_item_purchase_times(
    frames: List[Dict[str, Any]], participant_id: int
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Ermittelt aus den Timeline-Events die Kaufzeitpunkte (in Minuten, auf Sekunden genau)
    für das 1., 2. und 3. vollständige Item des Spielers (inkl. Berücksichtigung von ITEM_UNDO).
    """
    completed_ids = _get_completed_item_ids()
    purchases: List[Tuple[int, int]] = []  # (canonical_item_id, timestamp_ms)

    for frame in frames:
        for ev in frame.get("events", []):
            if ev.get("participantId") != participant_id:
                continue
            etype = ev.get("type")
            if etype == "ITEM_PURCHASED":
                raw_id = int(ev.get("itemId", 0))
                canon_id = _canonical_completed_item_id(raw_id, completed_ids)
                if canon_id is not None:
                    if not any(cid == canon_id for cid, _ in purchases):
                        purchases.append((canon_id, int(ev.get("timestamp", 0))))
            elif etype == "ITEM_UNDO":
                before_id = int(ev.get("beforeId", 0))
                canon_before = _canonical_completed_item_id(before_id, completed_ids)
                if canon_before is not None:
                    for idx in range(len(purchases) - 1, -1, -1):
                        if purchases[idx][0] == canon_before:
                            purchases.pop(idx)
                            break

    t1 = round(purchases[0][1] / 60000.0, 2) if len(purchases) >= 1 else None
    t2 = round(purchases[1][1] / 60000.0, 2) if len(purchases) >= 2 else None
    t3 = round(purchases[2][1] / 60000.0, 2) if len(purchases) >= 3 else None
    return t1, t2, t3


def _clamp(val: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, val))


def compute_composite_scores(m: Dict[str, float], role: str = "MIDDLE") -> Dict[str, float]:
    """
    Berechnet die 5 normierten Index-Scores (0–100 Punkte) analog zu den 5 Kategorien
    von League of Graphs (Fighting, Farming, Vision, Aggressiveness, Objectives),
    jedoch als zeitreihenfähige Punktwerte für jedes Spiel.
    """
    is_support = role == "UTILITY"
    is_jungle = role == "JUNGLE"

    # 1. Fighting Score (0-100)
    kda_norm = _clamp((m["kda"] / 5.5) * 100.0)
    kp_norm = _clamp((m["kill_participation"] / 75.0) * 100.0)
    dpm_target = 450.0 if is_support else 850.0
    dpm_norm = _clamp((m["dpm"] / dpm_target) * 100.0)
    solo_norm = _clamp((m["solo_kills"] / 2.5) * 100.0)
    score_fighting = round(
        0.35 * kda_norm + 0.30 * kp_norm + 0.25 * dpm_norm + 0.10 * solo_norm, 1
    )

    # 2. Farming Score (0-100)
    if is_support:
        gpm_norm = _clamp((m["gpm"] / 360.0) * 100.0)
        gd15_norm = _clamp(50.0 + (m["gold_diff_15"] / 1500.0) * 50.0)
        score_farming = round(0.60 * gpm_norm + 0.40 * gd15_norm, 1)
    else:
        cs_min_norm = _clamp((m["cs_per_min"] / 8.8) * 100.0)
        cs10_norm = _clamp((m["cs_at_10"] / 82.0) * 100.0)
        gpm_norm = _clamp((m["gpm"] / 480.0) * 100.0)
        gd15_norm = _clamp(50.0 + (m["gold_diff_15"] / 2000.0) * 50.0)
        score_farming = round(
            0.35 * cs_min_norm + 0.25 * cs10_norm + 0.25 * gpm_norm + 0.15 * gd15_norm, 1
        )

    # 3. Vision Score (0-100)
    vs_target = 2.4 if is_support else 1.25
    vs_norm = _clamp((m["vision_score_per_min"] / vs_target) * 100.0)
    cw_target = 6.0 if is_support else 3.0
    cw_norm = _clamp((m["control_wards_bought"] / cw_target) * 100.0)
    wc_target = 6.0 if is_support else 3.0
    wc_norm = _clamp((m["wards_cleared_before_20"] / wc_target) * 100.0)
    score_vision = round(0.50 * vs_norm + 0.25 * cw_norm + 0.25 * wc_norm, 1)

    # 4. Aggressiveness Score (0-100)
    fb_norm = m["first_blood_rate"]
    plates_norm = _clamp((m["turret_plates"] / 3.5) * 100.0)
    xp15_norm = _clamp(50.0 + (m["xp_diff_15"] / 1500.0) * 50.0)
    gd15_agg = _clamp(50.0 + (m["gold_diff_15"] / 2000.0) * 50.0)
    score_aggressiveness = round(
        0.25 * solo_norm + 0.20 * fb_norm + 0.25 * plates_norm + 0.15 * xp15_norm + 0.15 * gd15_agg,
        1,
    )

    # 5. Objectives Score (0-100)
    obj_target = 1100.0 if is_jungle else 550.0
    obj_dpm_norm = _clamp((m["objective_damage_per_min"] / obj_target) * 100.0)
    turret_dpm_norm = _clamp((m["turret_damage_per_min"] / 220.0) * 100.0)
    epic_target = 5.0 if is_jungle else 3.0
    epic_norm = _clamp((m["epic_monster_takedowns"] / epic_target) * 100.0)
    ft_norm = m["first_tower_rate"]
    score_objectives = round(
        0.35 * obj_dpm_norm + 0.30 * turret_dpm_norm + 0.25 * epic_norm + 0.10 * ft_norm, 1
    )

    return {
        "score_fighting": score_fighting,
        "score_farming": score_farming,
        "score_vision": score_vision,
        "score_aggressiveness": score_aggressiveness,
        "score_objectives": score_objectives,
    }


def parse_riot_match(
    match_json: Dict[str, Any],
    timeline_json: Optional[Dict[str, Any]],
    puuid: str,
    player_label: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Extrahiert aus dem offiziellen Riot Match-V5 JSON und Timeline JSON alle 45 normierten Metriken.
    """
    info = match_json.get("info", {})
    metadata = match_json.get("metadata", {})
    match_id = metadata.get("matchId")
    game_duration = int(info.get("gameDuration", 0))

    # Überspringe Remakes (< 5 Minuten)
    if not match_id or game_duration < 300:
        return None

    participants = info.get("participants", [])
    me = next((p for p in participants if p.get("puuid") == puuid), None)
    if not me:
        return None

    game_minutes = max(1.0, game_duration / 60.0)
    role = me.get("teamPosition") or me.get("individualPosition") or "MIDDLE"
    if role == "Invalid" or not role:
        role = "MIDDLE"

    my_team_id = me.get("teamId", 100)
    my_part_id = me.get("participantId", 1)

    # Suche direkten Lane-Gegner (gleiche teamPosition im Gegnerteam, mit Slot-Fallback)
    opponent = next(
        (
            p
            for p in participants
            if p.get("teamId") != my_team_id and p.get("teamPosition") == role
        ),
        None,
    )
    if opponent is None:
        fallback_part_id = my_part_id + 5 if my_part_id <= 5 else my_part_id - 5
        opponent = next(
            (p for p in participants if p.get("participantId") == fallback_part_id),
            None,
        )
    opp_part_id = opponent.get("participantId") if opponent else None

    # Laning- & Midgame-Differenzen (@10, @15, @20, @25 Min) sowie Item-Kaufzeitpunkte aus der Timeline berechnen
    gold_diff_10: Optional[float] = None
    gold_diff_15: float = 0.0
    gold_diff_20: Optional[float] = None
    gold_diff_25: Optional[float] = None
    cs_diff_15: float = 0.0
    xp_diff_15: float = 0.0
    item_1_time: Optional[float] = None
    item_2_time: Optional[float] = None
    item_3_time: Optional[float] = None

    if timeline_json:
        frames = timeline_json.get("info", {}).get("frames", [])
        item_1_time, item_2_time, item_3_time = _extract_item_purchase_times(frames, my_part_id)

        if opp_part_id:
            def _gold_diff_at(minute: int) -> Optional[float]:
                if len(frames) <= minute or game_duration < minute * 60 - 15:
                    return None
                fr = frames[minute]
                if int(fr.get("timestamp", minute * 60000)) < minute * 60000 - 15000:
                    return None
                p_frames = fr.get("participantFrames", {})
                my_f = p_frames.get(str(my_part_id), {})
                opp_f = p_frames.get(str(opp_part_id), {})
                if my_f and opp_f:
                    return round(float(my_f.get("totalGold", 0) - opp_f.get("totalGold", 0)), 0)
                return None

            gold_diff_10 = _gold_diff_at(10)
            gd15_val = _gold_diff_at(15)
            if gd15_val is not None:
                gold_diff_15 = gd15_val
            gold_diff_20 = _gold_diff_at(20)
            gold_diff_25 = _gold_diff_at(25)

            if len(frames) > 15:
                p_frames = frames[15].get("participantFrames", {})
                my_f = p_frames.get(str(my_part_id), {})
                opp_f = p_frames.get(str(opp_part_id), {})
                if my_f and opp_f:
                    my_cs_15 = my_f.get("minionsKilled", 0) + my_f.get("jungleMinionsKilled", 0)
                    opp_cs_15 = opp_f.get("minionsKilled", 0) + opp_f.get("jungleMinionsKilled", 0)
                    cs_diff_15 = float(my_cs_15 - opp_cs_15)
                    xp_diff_15 = float(my_f.get("xp", 0) - opp_f.get("xp", 0))

    ch = me.get("challenges", {}) or {}
    kills = int(me.get("kills", 0))
    deaths = int(me.get("deaths", 0))
    assists = int(me.get("assists", 0))
    win = bool(me.get("win", False))

    total_cs = int(me.get("totalMinionsKilled", 0)) + int(me.get("neutralMinionsKilled", 0))
    gold_earned = max(1, int(me.get("goldEarned", 1)))
    total_dmg_champs = float(me.get("totalDamageDealtToChampions", 0))

    # Berechne Team-Kills als Fallback für Kill Participation
    team_kills = sum(
        int(p.get("kills", 0)) for p in participants if p.get("teamId") == my_team_id
    )
    kp_pct = (
        float(ch["killParticipation"]) * 100.0
        if "killParticipation" in ch
        else ((kills + assists) / max(1, team_kills)) * 100.0
    )

    first_blood = bool(me.get("firstBloodKill", False) or me.get("firstBloodAssist", False))
    first_tower = bool(me.get("firstTowerKill", False) or me.get("firstTowerAssist", False))

    epic_monsters = (
        float(ch.get("dragonTakedowns", me.get("dragonKills", 0)))
        + float(ch.get("riftHeraldTakedowns", 0))
        + float(ch.get("voidMonsterKill", 0))
        + float(ch.get("baronTakedowns", me.get("baronKills", 0)))
    )

    metrics: Dict[str, Any] = {
        "winrate": 100.0 if win else 0.0,
        "game_duration_min": round(game_minutes, 1),
        "kda": round((kills + assists) / max(1, deaths), 2),
        "kill_participation": round(kp_pct, 1),
        "kills_per_game": float(kills),
        "deaths_per_game": float(deaths),
        "assists_per_game": float(assists),
        "solo_kills": float(ch.get("soloKills", 0)),
        "first_blood_rate": 100.0 if first_blood else 0.0,
        "dpm": round(float(ch.get("damagePerMinute", total_dmg_champs / game_minutes)), 0),
        "damage_share": round(float(ch.get("teamDamagePercentage", 0.0)) * 100.0, 1),
        "damage_per_gold": round(total_dmg_champs / gold_earned, 2),
        "damage_taken_per_min": round(float(me.get("totalDamageTaken", 0)) / game_minutes, 0),
        "damage_taken_share": round(float(ch.get("damageTakenOnTeamPercentage", 0.0)) * 100.0, 1),
        "self_mitigated_per_min": round(float(me.get("damageSelfMitigated", 0)) / game_minutes, 0),
        "ally_heal_shield_per_min": round(
            (
                float(me.get("totalHealsOnTeammates", 0))
                + float(me.get("totalDamageShieldedOnTeammates", 0))
            )
            / game_minutes,
            0,
        ),
        "cc_per_min": round(float(me.get("timeCCingOthers", 0)) / game_minutes, 2),
        "cs_per_min": round(total_cs / game_minutes, 2),
        "cs_at_10": round(
            float(ch.get("laneMinionsFirst10Minutes", 0))
            + float(ch.get("jungleCsBefore10Minutes", 0)),
            1,
        ),
        "gpm": round(float(ch.get("goldPerMinute", gold_earned / game_minutes)), 0),
        "gold_diff_10": gold_diff_10,
        "gold_diff_15": round(gold_diff_15, 0),
        "gold_diff_20": gold_diff_20,
        "gold_diff_25": gold_diff_25,
        "item_1_time": item_1_time,
        "item_2_time": item_2_time,
        "item_3_time": item_3_time,
        "cs_diff_15": round(cs_diff_15, 1),
        "xp_diff_15": round(xp_diff_15, 0),
        "turret_plates": float(ch.get("turretPlatesTaken", 0)),
        "counter_jungle_cs": float(ch.get("enemyJungleMonsterKills", 0)),
        "vision_score_per_min": round(
            float(ch.get("visionScorePerMinute", float(me.get("visionScore", 0)) / game_minutes)),
            2,
        ),
        "vision_diff_opponent": round(
            float(ch.get("visionScoreAdvantageLaneOpponent", 0.0)) * 100.0, 1
        ),
        "wards_placed_per_10m": round((float(me.get("wardsPlaced", 0)) / game_minutes) * 10.0, 1),
        "wards_cleared_before_20": float(ch.get("wardTakedownsBefore20M", me.get("wardsKilled", 0))),
        "control_wards_bought": float(me.get("visionWardsBoughtInGame", 0)),
        "turret_damage_per_min": round(float(me.get("damageDealtToTurrets", 0)) / game_minutes, 0),
        "objective_damage_per_min": round(
            float(me.get("damageDealtToObjectives", 0)) / game_minutes, 0
        ),
        "first_tower_rate": 100.0 if first_tower else 0.0,
        "epic_monster_takedowns": epic_monsters,
    }

    scores = compute_composite_scores(metrics, role)
    metrics.update(scores)

    r_name = me.get("riotIdGameName") or me.get("summonerName") or ""
    r_tag = me.get("riotIdTagline") or ""
    resolved_label = player_label or (f"{r_name}#{r_tag}" if r_name and r_tag else r_name)

    return {
        "match_id": match_id,
        "puuid": puuid,
        "player_name": resolved_label,
        "game_creation": int(info.get("gameCreation", int(time.time() * 1000))),
        "game_duration": game_duration,
        "champion_name": me.get("championName", "Unknown"),
        "team_position": role,
        "queue_id": int(info.get("queueId", 420)),
        "win": win,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "is_demo": False,
        "metrics": metrics,
    }


def _riot_get(url: str, headers: Dict[str, str], timeout: int = 12) -> requests.Response:
    """
    Führt einen GET-Request gegen die Riot API aus und wartet bei Rate-Limits (HTTP 429)
    automatisch gemäß dem 'Retry-After'-Header, damit auch große Abfragen (>100 Spiele)
    zuverlässig durchlaufen.
    """
    for _ in range(6):
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            time.sleep(max(1, retry_after) + 0.5)
            continue
        return resp
    return resp


def fetch_and_save_player_rank(puuid: str, region: str, api_key: str) -> Optional[int]:
    """
    Ruft den aktuellen Rang (Tier, Division, LP) über Riot League-V4 (by-puuid) ab,
    speichert ihn in der `players`-Tabelle und aktualisiert die Rang-Kurve ('rank_lp') aller Spiele.
    """
    if not puuid or puuid == "DEMO_PUUID":
        return None

    platform = (region or "EUW1").strip().lower()
    headers = {"X-Riot-Token": api_key.strip()}
    url = f"https://{platform}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"

    solo_tier, solo_rank, solo_lp = "", "", 0
    flex_tier, flex_rank, flex_lp = "", "", 0

    try:
        resp = _riot_get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            entries = resp.json()
            for entry in entries:
                q_type = entry.get("queueType", "")
                if q_type == "RANKED_SOLO_5x5":
                    solo_tier = entry.get("tier", "")
                    solo_rank = entry.get("rank", "IV")
                    solo_lp = int(entry.get("leaguePoints", 0))
                elif q_type == "RANKED_FLEX_SR":
                    flex_tier = entry.get("tier", "")
                    flex_rank = entry.get("rank", "IV")
                    flex_lp = int(entry.get("leaguePoints", 0))
    except Exception:
        pass

    if solo_tier or flex_tier:
        return update_player_rank(
            puuid=puuid,
            solo_tier=solo_tier,
            solo_rank=solo_rank,
            solo_lp=solo_lp,
            flex_tier=flex_tier,
            flex_rank=flex_rank,
            flex_lp=flex_lp,
        )

    recompute_player_match_ranks(puuid)
    return None


def ensure_all_player_ranks() -> None:
    """
    Stellt beim Start sicher, dass alle gespeicherten Spieler ihren aktuellen Rang
    und alle Spiele in der Datenbank das 'rank_lp'-Metrik-Feld besitzen.
    """
    settings = get_settings()
    api_key = settings.get("api_key", "").strip()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT puuid, region, solo_total_lp, flex_total_lp FROM players")
    player_rows = cur.fetchall()
    conn.close()

    for p in player_rows:
        puuid = p["puuid"]
        region = p["region"] or "EUW1"
        has_rank = p["solo_total_lp"] is not None or p["flex_total_lp"] is not None

        if puuid == "DEMO_PUUID":
            if not has_rank:
                update_player_rank("DEMO_PUUID", solo_tier="PLATINUM", solo_rank="II", solo_lp=45)
            continue

        if not has_rank and api_key:
            fetch_and_save_player_rank(puuid, region, api_key)
        else:
            # Prüfe, ob irgendein Spiel dieses Spielers noch kein 'rank_lp' hat
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT metrics_json FROM matches WHERE puuid = ? ORDER BY game_creation DESC LIMIT 1",
                (puuid,),
            )
            m_row = cur.fetchone()
            conn.close()
            if m_row:
                m_dict = json.loads(m_row["metrics_json"] or "{}")
                if "rank_lp" not in m_dict:
                    recompute_player_match_ranks(puuid)


def sync_riot_matches(
    game_name: str,
    tag_line: str,
    region: str,
    api_key: str,
    count: int = 50,
    queue_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Synchronisiert die letzten `count` Spiele eines Riot-Accounts in die lokale SQLite-DB.
    - Unterstützt beliebige Anzahlen (auch >100 Spiele) durch automatische 100er-Pagination.
    - Unterstützt direktes Vorfiltern nach Queue-ID (z. B. 420 = Ranked Solo/Duo) direkt bei Riot,
      wodurch nur relevante Spiele geladen werden und enorm viel Zeit gespart wird.
    - Bereits gespeicherte Match-IDs werden übersprungen.
    """
    routing = REGION_TO_ROUTING.get(region.upper(), "europe")
    headers = {"X-Riot-Token": api_key.strip()}

    safe_name = urllib.parse.quote(game_name.strip())
    safe_tag = urllib.parse.quote(tag_line.strip().lstrip("#"))

    # 1. Account PUUID abrufen
    acc_url = f"https://{routing}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{safe_name}/{safe_tag}"
    resp = _riot_get(acc_url, headers=headers, timeout=10)
    if resp.status_code == 401 or resp.status_code == 403:
        raise ValueError("Riot API Key ist ungültig oder abgelaufen (HTTP 401/403).")
    if resp.status_code == 404:
        raise ValueError(f"Riot ID '{game_name}#{tag_line}' wurde nicht gefunden.")
    resp.raise_for_status()

    acc_data = resp.json()
    puuid = acc_data["puuid"]
    canonical_name = acc_data.get("gameName", game_name.strip())
    canonical_tag = acc_data.get("tagLine", tag_line.strip().lstrip("#"))
    player_label = f"{canonical_name}#{canonical_tag}"

    upsert_player(puuid, canonical_name, canonical_tag, region.upper())

    # 2. Match-IDs in 100er-Seiten abrufen (inkl. optionalem Queue-Filter direkt bei Riot)
    target_count = max(1, int(count))
    match_ids: List[str] = []
    start_offset = 0

    while len(match_ids) < target_count:
        batch_size = min(100, target_count - len(match_ids))
        ids_url = (
            f"https://{routing}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"
            f"?start={start_offset}&count={batch_size}"
        )
        if queue_id is not None and int(queue_id) > 0:
            ids_url += f"&queue={int(queue_id)}"

        ids_resp = _riot_get(ids_url, headers=headers, timeout=10)
        ids_resp.raise_for_status()
        batch_ids: List[str] = ids_resp.json()

        if not batch_ids:
            break

        match_ids.extend(batch_ids)
        start_offset += len(batch_ids)

        if len(batch_ids) < batch_size:
            # Keine älteren Spiele mehr in dieser Queue bei Riot verfügbar
            break

    # Prüfe, welche Matches für DIESEN Spieler (puuid) bereits vollständig (inkl. neuer Timeline-Stats) vorhanden sind
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT match_id, metrics_json FROM matches WHERE puuid = ? AND is_demo = 0", (puuid,))
    existing_ids = set()
    for r in cur.fetchall():
        try:
            m_json = json.loads(r["metrics_json"] or "{}")
            if "gold_diff_10" in m_json and "item_1_time" in m_json:
                existing_ids.add(r["match_id"])
        except Exception:
            pass
    conn.close()

    imported = 0
    skipped = 0

    for mid in match_ids:
        if mid in existing_ids:
            skipped += 1
            continue

        m_url = f"https://{routing}.api.riotgames.com/lol/match/v5/matches/{mid}"
        t_url = f"https://{routing}.api.riotgames.com/lol/match/v5/matches/{mid}/timeline"

        m_resp = _riot_get(m_url, headers=headers, timeout=12)
        if m_resp.status_code != 200:
            continue

        t_resp = _riot_get(t_url, headers=headers, timeout=12)
        t_json = t_resp.json() if t_resp.status_code == 200 else None

        parsed = parse_riot_match(m_resp.json(), t_json, puuid, player_label=player_label)
        if parsed:
            insert_match(parsed)
            imported += 1

    # Aktualisiere Live-Rang über League-V4 und berechne die Rang-LP-Kurve über alle Spiele
    fetch_and_save_player_rank(puuid, region.upper(), api_key)

    return {
        "puuid": puuid,
        "player_label": player_label,
        "fetched_ids": len(match_ids),
        "imported": imported,
        "skipped": skipped,
        "queue_id": queue_id,
    }


def generate_demo_matches(num_matches: int = 80) -> int:
    """
    Erzeugt 80 realistische Demo-Matches über die letzten 60 Tage mit natürlicher
    Spiel-zu-Spiel-Varianz UND einer realistischen Lern-/Verbesserungskurve über die Zeit.
    """
    clear_demo_matches()
    rng = random.Random(42)

    pool = [
        {"champ": "Ahri", "role": "MIDDLE", "weight": 0.42},
        {"champ": "Orianna", "role": "MIDDLE", "weight": 0.25},
        {"champ": "Jinx", "role": "BOTTOM", "weight": 0.20},
        {"champ": "Viego", "role": "JUNGLE", "weight": 0.13},
    ]

    now_ms = int(time.time() * 1000)
    # Erzeuge realistische Zeitstempel in Sessions (mal 4-6 Spiele an einem Abend, mal 3-9 Tage Pause)
    timestamps: List[int] = []
    cur_ts = now_ms - (75 * 24 * 3600 * 1000)
    games_left_in_session = rng.randint(2, 5)
    for _ in range(num_matches):
        timestamps.append(cur_ts)
        games_left_in_session -= 1
        if games_left_in_session <= 0:
            # Pause zwischen Spiel-Tagen/Wochen (1 bis 8 Tage Pause)
            pause_hours = rng.choice([18, 24, 42, 72, 120, 168, 216])
            cur_ts += pause_hours * 3600 * 1000 + rng.randint(0, 4 * 3600 * 1000)
            games_left_in_session = rng.randint(2, 6)
        else:
            # Nächstes Spiel in derselben Session (35 bis 55 Minuten später)
            cur_ts += rng.randint(35 * 60 * 1000, 55 * 60 * 1000)

    # Skaliere Zeitstempel so, dass das letzte Spiel heute endet
    if timestamps[-1] > now_ms:
        shift = timestamps[-1] - now_ms + 3600_000
        timestamps = [t - shift for t in timestamps]

    for i in range(num_matches):
        # Fortschrittsfaktor 0.0 (ältestes Spiel) -> 1.0 (neuestes Spiel)
        progress = i / max(1, num_matches - 1)

        # Wähle Champion & Rolle
        r_val = rng.random()
        cum = 0.0
        picked = pool[0]
        for item in pool:
            cum += item["weight"]
            if r_val <= cum:
                picked = item
                break

        champ = picked["champ"]
        role = picked["role"]
        queue_id = 420 if rng.random() < 0.85 else 440

        game_duration = rng.randint(1320, 2460)  # 22 bis 41 Minuten
        game_minutes = game_duration / 60.0

        # Winrate steigt im Verlauf leicht von ~46% auf ~62%
        win_prob = 0.45 + 0.18 * progress + rng.uniform(-0.08, 0.08)
        win = rng.random() < win_prob
        win_bonus = 1.12 if win else 0.88

        # Kills, Deaths, Assists (Deaths sinken im Verlauf von ~6.2 auf ~4.1!)
        kills = max(0, int(round(rng.gauss(5.8 + 2.4 * progress, 2.6) * win_bonus)))
        deaths = max(0, int(round(rng.gauss(6.3 - 2.3 * progress, 2.1) * (0.85 if win else 1.18))))
        assists = max(0, int(round(rng.gauss(6.8 + 2.1 * progress, 3.0) * win_bonus)))

        kda = round((kills + assists) / max(1, deaths), 2)
        kp = _clamp(round(rng.gauss(52.0 + 13.0 * progress, 9.5) * (1.05 if win else 0.95), 1), 18.0, 92.0)
        solo_kills = max(0, int(round(rng.gauss(0.8 + 1.1 * progress, 0.9))))
        first_blood = 100.0 if rng.random() < (0.18 + 0.14 * progress) else 0.0

        # Farm & Laning (CS/Min verbessert sich von ~6.6 auf ~8.1)
        base_cs_min = 6.5 + 1.65 * progress + rng.gauss(0, 0.65)
        cs_per_min = round(_clamp(base_cs_min, 4.2, 10.4), 2)
        cs_at_10 = round(_clamp(cs_per_min * 10.0 * rng.uniform(0.88, 1.04), 38.0, 98.0), 1)
        gpm = round(_clamp(365.0 + 75.0 * progress + (45.0 if win else -25.0) + rng.gauss(0, 32.0), 260.0, 620.0), 0)

        # Laning & Midgame Differenzen (@10, @15, @20, @25) sowie Item-Kaufzeitpunkte
        gold_diff_10 = round(rng.gauss(-180.0 + 480.0 * progress + (160.0 if win else -140.0), 320.0), 0)
        gold_diff_15 = round(gold_diff_10 * 1.15 + rng.gauss(-80.0 + 340.0 * progress + (140.0 if win else -120.0), 340.0), 0)
        gold_diff_20: Optional[float] = (
            round(gold_diff_15 * 1.25 + rng.gauss(-60.0 + 280.0 * progress + (220.0 if win else -190.0), 420.0), 0)
            if game_minutes >= 20.0
            else None
        )
        gold_diff_25: Optional[float] = (
            round(
                (gold_diff_20 if gold_diff_20 is not None else gold_diff_15) * 1.20
                + rng.gauss(-50.0 + 260.0 * progress + (280.0 if win else -250.0), 520.0),
                0,
            )
            if game_minutes >= 25.0
            else None
        )

        # Item-Kaufzeitpunkte (1., 2., 3. Item in Minuten, sinken bei Fortschritt & höheren GPM)
        item_1_raw = 12.6 - 1.5 * progress - (0.45 if win else -0.35) + rng.gauss(0, 0.75)
        item_1_time: Optional[float] = round(_clamp(item_1_raw, 8.5, min(game_minutes - 0.5, 16.5)), 2)

        item_2_raw = item_1_time + 7.4 - 0.9 * progress - (0.45 if win else -0.35) + rng.gauss(0, 0.95)
        item_2_time: Optional[float] = (
            round(_clamp(item_2_raw, item_1_time + 4.2, 26.5), 2)
            if game_minutes >= item_2_raw + 0.3
            else None
        )

        item_3_raw = (item_2_time or 19.5) + 6.9 - 0.8 * progress - (0.50 if win else -0.40) + rng.gauss(0, 1.1)
        item_3_time: Optional[float] = (
            round(_clamp(item_3_raw, (item_2_time or 19.5) + 4.0, 35.0), 2)
            if item_2_time is not None and game_minutes >= item_3_raw + 0.3
            else None
        )

        cs_diff_15 = round(rng.gauss(-5.0 + 16.0 * progress + (5.0 if win else -4.0), 9.0), 1)
        xp_diff_15 = round(rng.gauss(-190.0 + 580.0 * progress + (190.0 if win else -160.0), 390.0), 0)
        turret_plates = float(max(0, int(round(rng.gauss(1.1 + 1.5 * progress, 1.1)))))
        counter_jungle = round(max(0.0, rng.gauss((4.5 if role == "JUNGLE" else 1.2) + 2.0 * progress, 1.5)), 1)

        # Schaden & Utility
        dpm = round(_clamp(rng.gauss(560.0 + 210.0 * progress, 125.0) * win_bonus, 250.0, 1350.0), 0)
        damage_share = round(_clamp(rng.gauss(21.5 + 5.5 * progress, 4.2), 10.0, 44.0), 1)
        damage_per_gold = round(_clamp(dpm / max(200.0, gpm), 0.75, 2.85), 2)
        damage_taken_per_min = round(_clamp(rng.gauss(680.0 - 40.0 * progress, 140.0), 320.0, 1400.0), 0)
        damage_taken_share = round(_clamp(rng.gauss(19.5, 4.0), 9.0, 38.0), 1)
        self_mitigated_per_min = round(_clamp(rng.gauss(420.0 + 80.0 * progress, 110.0), 150.0, 1200.0), 0)
        ally_heal_shield_per_min = round(
            max(0.0, rng.gauss(95.0 if champ == "Orianna" else 25.0, 22.0)), 0
        )
        cc_per_min = round(_clamp(rng.gauss(0.65 + 0.25 * progress, 0.22), 0.1, 2.2), 2)

        # Vision (Vision Score/Min steigt von ~0.95 auf ~1.48)
        vision_score_per_min = round(_clamp(rng.gauss(0.95 + 0.52 * progress, 0.22), 0.4, 2.6), 2)
        vision_diff_opp = round(rng.gauss(-8.0 + 32.0 * progress, 18.0), 1)
        wards_placed_10m = round(_clamp(rng.gauss(3.8 + 1.6 * progress, 0.8), 1.5, 8.5), 1)
        wards_cleared_20 = float(max(0, int(round(rng.gauss(1.6 + 2.1 * progress, 1.2)))))
        control_wards = float(max(0, int(round(rng.gauss(1.2 + 2.2 * progress, 1.1)))))

        # Objectives
        turret_dpm = round(_clamp(rng.gauss(115.0 + 85.0 * progress, 55.0) * win_bonus, 15.0, 480.0), 0)
        obj_dpm = round(
            _clamp(
                rng.gauss((680.0 if role == "JUNGLE" else 340.0) + 190.0 * progress, 130.0) * win_bonus,
                60.0,
                1600.0,
            ),
            0,
        )
        first_tower = 100.0 if rng.random() < (0.20 + 0.18 * progress) else 0.0
        epic_takedowns = float(max(0, int(round(rng.gauss(1.8 + 1.9 * progress, 1.2) * win_bonus))))

        metrics: Dict[str, Any] = {
            "winrate": 100.0 if win else 0.0,
            "game_duration_min": round(game_minutes, 1),
            "kda": kda,
            "kill_participation": kp,
            "kills_per_game": float(kills),
            "deaths_per_game": float(deaths),
            "assists_per_game": float(assists),
            "solo_kills": float(solo_kills),
            "first_blood_rate": first_blood,
            "dpm": dpm,
            "damage_share": damage_share,
            "damage_per_gold": damage_per_gold,
            "damage_taken_per_min": damage_taken_per_min,
            "damage_taken_share": damage_taken_share,
            "self_mitigated_per_min": self_mitigated_per_min,
            "ally_heal_shield_per_min": ally_heal_shield_per_min,
            "cc_per_min": cc_per_min,
            "cs_per_min": cs_per_min,
            "cs_at_10": cs_at_10,
            "gpm": gpm,
            "gold_diff_10": gold_diff_10,
            "gold_diff_15": gold_diff_15,
            "gold_diff_20": gold_diff_20,
            "gold_diff_25": gold_diff_25,
            "item_1_time": item_1_time,
            "item_2_time": item_2_time,
            "item_3_time": item_3_time,
            "cs_diff_15": cs_diff_15,
            "xp_diff_15": xp_diff_15,
            "turret_plates": turret_plates,
            "counter_jungle_cs": counter_jungle,
            "vision_score_per_min": vision_score_per_min,
            "vision_diff_opponent": vision_diff_opp,
            "wards_placed_per_10m": wards_placed_10m,
            "wards_cleared_before_20": wards_cleared_20,
            "control_wards_bought": control_wards,
            "turret_damage_per_min": turret_dpm,
            "objective_damage_per_min": obj_dpm,
            "first_tower_rate": first_tower,
            "epic_monster_takedowns": epic_takedowns,
        }

        scores = compute_composite_scores(metrics, role)
        metrics.update(scores)

        match_record = {
            "match_id": f"DEMO_EUW1_{10000 + i}",
            "puuid": "DEMO_PUUID",
            "player_name": "DemoPlayer#EUW",
            "game_creation": timestamps[i],
            "game_duration": game_duration,
            "champion_name": champ,
            "team_position": role,
            "queue_id": queue_id,
            "win": win,
            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "is_demo": True,
            "metrics": metrics,
        }
        insert_match(match_record)

    upsert_player("DEMO_PUUID", "DemoPlayer", "EUW", "EUW1")
    update_player_rank("DEMO_PUUID", solo_tier="PLATINUM", solo_rank="II", solo_lp=45)
    return num_matches
