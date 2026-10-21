"""
Riot Games API Client (Account-V1, Match-V5 & Timeline) sowie realistischer Demo-Datengenerator.
"""

import math
import random
import time
from typing import Dict, Any, List, Optional
import urllib.parse
import requests

from database import get_connection, insert_match, clear_demo_matches, upsert_player


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
    Extrahiert aus dem offiziellen Riot Match-V5 JSON und Timeline JSON alle 39 normierten Metriken.
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

    # Suche direkten Lane-Gegner (gleiche teamPosition im Gegnerteam)
    opponent = next(
        (
            p
            for p in participants
            if p.get("teamId") != my_team_id and p.get("teamPosition") == role
        ),
        None,
    )
    opp_part_id = opponent.get("participantId") if opponent else None

    # Laning-Differenzen @15 Min aus der Timeline berechnen
    gold_diff_15 = 0.0
    cs_diff_15 = 0.0
    xp_diff_15 = 0.0

    if timeline_json and opp_part_id:
        frames = timeline_json.get("info", {}).get("frames", [])
        if len(frames) > 15:
            p_frames = frames[15].get("participantFrames", {})
            my_f = p_frames.get(str(my_part_id), {})
            opp_f = p_frames.get(str(opp_part_id), {})
            if my_f and opp_f:
                gold_diff_15 = float(my_f.get("totalGold", 0) - opp_f.get("totalGold", 0))
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

    metrics: Dict[str, float] = {
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
        "gold_diff_15": round(gold_diff_15, 0),
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

    # Prüfe, welche Matches für DIESEN Spieler (puuid) bereits in der Datenbank vorhanden sind
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT match_id FROM matches WHERE puuid = ? AND is_demo = 0", (puuid,))
    existing_ids = {r["match_id"] for r in cur.fetchall()}
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

        # Laning @15 Differenzen (verbessern sich von leicht negativ auf positiv)
        gold_diff_15 = round(rng.gauss(-280.0 + 760.0 * progress + (260.0 if win else -220.0), 480.0), 0)
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

        metrics: Dict[str, float] = {
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
            "gold_diff_15": gold_diff_15,
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
    return num_matches
