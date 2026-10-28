"""
SQLite-Datenbankverwaltung und Zeitreihen-Aggregations-Engine für LeagueTracker.
"""

import json
import math
import os
import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from stats_config import STATS_CONFIG, STATS_BY_ID, DEFAULT_FAVORITES

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "leaguetracker.db")

TIER_BASE_LP: Dict[str, int] = {
    "IRON": 0,
    "BRONZE": 400,
    "SILVER": 800,
    "GOLD": 1200,
    "PLATINUM": 1600,
    "EMERALD": 2000,
    "DIAMOND": 2400,
    "MASTER": 2800,
    "GRANDMASTER": 2800,
    "CHALLENGER": 2800,
}

DIVISION_OFFSET_LP: Dict[str, int] = {
    "IV": 0,
    "III": 100,
    "II": 200,
    "I": 300,
}

TIER_DISPLAY_NAMES = ["Iron", "Bronze", "Silver", "Gold", "Platin", "Emerald", "Diamond"]
DIVISION_DISPLAY_NAMES = ["IV", "III", "II", "I"]


def tier_to_total_lp(tier: str, division: str, lp: int) -> int:
    t_up = (tier or "").strip().upper()
    d_up = (division or "IV").strip().upper()
    if t_up not in TIER_BASE_LP:
        return 1200
    base = TIER_BASE_LP[t_up]
    if t_up in ("MASTER", "GRANDMASTER", "CHALLENGER"):
        return base + max(0, int(lp))
    return base + DIVISION_OFFSET_LP.get(d_up, 0) + max(0, min(99, int(lp)))


def format_rank_lp(total_lp: float, short: bool = False) -> str:
    val = int(round(float(total_lp)))
    if val >= 2800:
        apex_lp = val - 2800
        if apex_lp >= 1000:
            return f"Chall. {apex_lp} LP" if short else f"Challenger ({apex_lp} LP)"
        if apex_lp >= 500:
            return f"GM {apex_lp} LP" if short else f"Grandmaster ({apex_lp} LP)"
        return f"Master {apex_lp} LP" if short else f"Master ({apex_lp} LP)"

    clamped = max(0, val)
    tier_idx = min(6, clamped // 400)
    rem = clamped % 400
    div_idx = min(3, rem // 100)
    lp = rem % 100
    t_name = TIER_DISPLAY_NAMES[tier_idx]
    d_name = DIVISION_DISPLAY_NAMES[div_idx]
    return f"{t_name} {d_name} {lp} LP" if short else f"{t_name} {d_name} ({lp} LP)"


def get_connection() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS players (
            puuid TEXT PRIMARY KEY,
            game_name TEXT NOT NULL,
            tag_line TEXT NOT NULL,
            region TEXT NOT NULL,
            last_synced INTEGER DEFAULT 0,
            solo_tier TEXT DEFAULT '',
            solo_rank TEXT DEFAULT '',
            solo_lp INTEGER DEFAULT 0,
            solo_total_lp INTEGER DEFAULT NULL,
            flex_tier TEXT DEFAULT '',
            flex_rank TEXT DEFAULT '',
            flex_lp INTEGER DEFAULT 0,
            flex_total_lp INTEGER DEFAULT NULL,
            rank_label TEXT DEFAULT ''
        )
        """
    )

    cur.execute("PRAGMA table_info(players)")
    player_cols = {r["name"] for r in cur.fetchall()}
    for col_name, col_def in [
        ("solo_tier", "TEXT DEFAULT ''"),
        ("solo_rank", "TEXT DEFAULT ''"),
        ("solo_lp", "INTEGER DEFAULT 0"),
        ("solo_total_lp", "INTEGER DEFAULT NULL"),
        ("flex_tier", "TEXT DEFAULT ''"),
        ("flex_rank", "TEXT DEFAULT ''"),
        ("flex_lp", "INTEGER DEFAULT 0"),
        ("flex_total_lp", "INTEGER DEFAULT NULL"),
        ("rank_label", "TEXT DEFAULT ''"),
    ]:
        if col_name not in player_cols:
            cur.execute(f"ALTER TABLE players ADD COLUMN {col_name} {col_def}")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS matches (
            match_id TEXT NOT NULL,
            puuid TEXT NOT NULL,
            player_name TEXT DEFAULT '',
            game_creation INTEGER NOT NULL,
            game_duration INTEGER NOT NULL,
            champion_name TEXT NOT NULL,
            team_position TEXT NOT NULL,
            queue_id INTEGER NOT NULL,
            win INTEGER NOT NULL,
            kills INTEGER NOT NULL,
            deaths INTEGER NOT NULL,
            assists INTEGER NOT NULL,
            is_demo INTEGER DEFAULT 0,
            metrics_json TEXT NOT NULL,
            PRIMARY KEY (match_id, puuid)
        )
        """
    )

    # Prüfe, ob eine ältere Tabelle ohne 'player_name' bzw. ohne Composite-Primary-Key vorliegt, und migriere verlustfrei
    cur.execute("PRAGMA table_info(matches)")
    cols = [r["name"] for r in cur.fetchall()]
    if "player_name" not in cols:
        cur.execute(
            """
            CREATE TABLE matches_migrated (
                match_id TEXT NOT NULL,
                puuid TEXT NOT NULL,
                player_name TEXT DEFAULT '',
                game_creation INTEGER NOT NULL,
                game_duration INTEGER NOT NULL,
                champion_name TEXT NOT NULL,
                team_position TEXT NOT NULL,
                queue_id INTEGER NOT NULL,
                win INTEGER NOT NULL,
                kills INTEGER NOT NULL,
                deaths INTEGER NOT NULL,
                assists INTEGER NOT NULL,
                is_demo INTEGER DEFAULT 0,
                metrics_json TEXT NOT NULL,
                PRIMARY KEY (match_id, puuid)
            )
            """
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO matches_migrated (
                match_id, puuid, player_name, game_creation, game_duration,
                champion_name, team_position, queue_id, win,
                kills, deaths, assists, is_demo, metrics_json
            )
            SELECT
                match_id, COALESCE(puuid, 'UNKNOWN'), '', game_creation, game_duration,
                champion_name, team_position, queue_id, win,
                kills, deaths, assists, is_demo, metrics_json
            FROM matches
            """
        )
        cur.execute("DROP TABLE matches")
        cur.execute("ALTER TABLE matches_migrated RENAME TO matches")

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_matches_creation ON matches(game_creation ASC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_matches_puuid ON matches(puuid)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_matches_champ ON matches(champion_name)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_matches_role ON matches(team_position)"
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            stat_id TEXT PRIMARY KEY,
            sort_order INTEGER NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    # Automatische Verknüpfung bereits vorhandener Spiele mit dem gespeicherten Spieler-Account aus settings
    cur.execute("SELECT key, value FROM settings")
    saved_settings = {r["key"]: r["value"] for r in cur.fetchall()}
    s_name = saved_settings.get("game_name", "").strip()
    s_tag = saved_settings.get("tag_line", "").strip()
    s_reg = saved_settings.get("region", "EUW1").strip()

    cur.execute("SELECT DISTINCT puuid FROM matches WHERE is_demo = 0 AND puuid != ''")
    distinct_real_puuids = [r["puuid"] for r in cur.fetchall()]
    if len(distinct_real_puuids) == 1 and s_name and s_tag:
        only_puuid = distinct_real_puuids[0]
        label = f"{s_name}#{s_tag}"
        cur.execute(
            """
            INSERT OR IGNORE INTO players (puuid, game_name, tag_line, region, last_synced)
            VALUES (?, ?, ?, ?, strftime('%s','now'))
            """,
            (only_puuid, s_name, s_tag, s_reg),
        )
        cur.execute(
            "UPDATE matches SET player_name = ? WHERE puuid = ? AND (player_name IS NULL OR player_name = '')",
            (label, only_puuid),
        )

    # Falls Demo-Matches existieren, stelle sicher, dass ihr player_name gesetzt ist
    cur.execute(
        "UPDATE matches SET player_name = 'DemoPlayer#EUW' WHERE is_demo = 1 AND (player_name IS NULL OR player_name = '')"
    )

    # Initialisiere Standard-Favoriten beim ersten Start
    cur.execute("SELECT COUNT(*) as cnt FROM favorites")
    if cur.fetchone()["cnt"] == 0:
        for idx, stat_id in enumerate(DEFAULT_FAVORITES):
            if stat_id in STATS_BY_ID:
                cur.execute(
                    "INSERT OR IGNORE INTO favorites (stat_id, sort_order) VALUES (?, ?)",
                    (stat_id, idx),
                )
    else:
        # Stelle sicher, dass der neue Rang-Stat ('rank_lp') ebenfalls in bestehenden Favoriten-Listen erscheint
        cur.execute("SELECT stat_id FROM favorites WHERE stat_id = 'rank_lp'")
        if cur.fetchone() is None and "rank_lp" in STATS_BY_ID:
            cur.execute("UPDATE favorites SET sort_order = sort_order + 1")
            cur.execute("INSERT INTO favorites (stat_id, sort_order) VALUES ('rank_lp', 0)")

    conn.commit()
    conn.close()


def get_favorites() -> List[str]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT stat_id FROM favorites ORDER BY sort_order ASC")
    rows = [r["stat_id"] for r in cur.fetchall() if r["stat_id"] in STATS_BY_ID]
    conn.close()
    return rows


def set_favorites(stat_ids: List[str]) -> List[str]:
    valid_ids = []
    seen = set()
    for sid in stat_ids:
        if sid in STATS_BY_ID and sid not in seen:
            valid_ids.append(sid)
            seen.add(sid)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM favorites")
    for idx, sid in enumerate(valid_ids):
        cur.execute(
            "INSERT INTO favorites (stat_id, sort_order) VALUES (?, ?)",
            (sid, idx),
        )
    conn.commit()
    conn.close()
    return valid_ids


def toggle_favorite(stat_id: str) -> Dict[str, Any]:
    if stat_id not in STATS_BY_ID:
        raise ValueError(f"Unbekannte Stat-ID: {stat_id}")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT stat_id FROM favorites WHERE stat_id = ?", (stat_id,))
    exists = cur.fetchone() is not None

    if exists:
        cur.execute("DELETE FROM favorites WHERE stat_id = ?", (stat_id,))
        is_fav = False
    else:
        cur.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS next_order FROM favorites")
        next_order = cur.fetchone()["next_order"]
        cur.execute(
            "INSERT INTO favorites (stat_id, sort_order) VALUES (?, ?)",
            (stat_id, next_order),
        )
        is_fav = True

    conn.commit()
    conn.close()
    return {"stat_id": stat_id, "is_favorite": is_fav, "favorites": get_favorites()}


def get_settings() -> Dict[str, str]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM settings")
    res = {r["key"]: r["value"] for r in cur.fetchall()}
    conn.close()
    return res


def save_settings(new_settings: Dict[str, str]) -> None:
    conn = get_connection()
    cur = conn.cursor()
    for k, v in new_settings.items():
        if v is not None:
            cur.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (k, str(v)),
            )
    conn.commit()
    conn.close()


def upsert_player(puuid: str, game_name: str, tag_line: str, region: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    label = f"{game_name.strip()}#{tag_line.strip().lstrip('#')}"
    cur.execute(
        """
        INSERT INTO players (puuid, game_name, tag_line, region, last_synced)
        VALUES (?, ?, ?, ?, strftime('%s','now'))
        ON CONFLICT(puuid) DO UPDATE SET
            game_name = excluded.game_name,
            tag_line = excluded.tag_line,
            region = excluded.region,
            last_synced = excluded.last_synced
        """,
        (puuid, game_name.strip(), tag_line.strip().lstrip("#"), region.strip().upper()),
    )
    cur.execute(
        "UPDATE matches SET player_name = ? WHERE puuid = ? AND (player_name IS NULL OR player_name = '')",
        (label, puuid),
    )
    conn.commit()
    conn.close()


def recompute_player_match_ranks(puuid: str, current_total_lp: Optional[int] = None) -> None:
    """
    Berechnet und speichert für alle Spiele eines Spielers den Rang-LP-Wert ('rank_lp')
    in chronologischer Reihenfolge rückwärts ausgehend vom aktuellen Live-Rang (League-V4).
    """
    conn = get_connection()
    cur = conn.cursor()

    if current_total_lp is None:
        cur.execute("SELECT solo_total_lp, flex_total_lp FROM players WHERE puuid = ?", (puuid,))
        p_row = cur.fetchone()
        if p_row:
            current_total_lp = (
                p_row["solo_total_lp"]
                if p_row["solo_total_lp"] is not None
                else p_row["flex_total_lp"]
            )
    if current_total_lp is None:
        current_total_lp = 1600  # Fallback Platin IV 0 LP

    cur.execute(
        "SELECT match_id, queue_id, win, metrics_json FROM matches WHERE puuid = ? ORDER BY game_creation ASC",
        (puuid,),
    )
    rows = cur.fetchall()
    if not rows:
        conn.close()
        return

    lp = int(current_total_lp)
    updates = []
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        m_dict = json.loads(r["metrics_json"] or "{}")
        m_dict["rank_lp"] = float(lp)
        updates.append((json.dumps(m_dict), r["match_id"], puuid))

        qid = int(r["queue_id"])
        win = int(r["win"])
        if qid in (420, 440):
            sf = float(m_dict.get("score_fighting", 50.0))
            perf = max(-2, min(2, int(round((sf - 50.0) / 20.0))))
            delta = (21 + perf) if win == 1 else (-21 + perf)
            lp = max(0, lp - delta)

    cur.executemany(
        "UPDATE matches SET metrics_json = ? WHERE match_id = ? AND puuid = ?",
        updates,
    )
    conn.commit()
    conn.close()


def update_player_rank(
    puuid: str,
    solo_tier: str = "",
    solo_rank: str = "",
    solo_lp: int = 0,
    flex_tier: str = "",
    flex_rank: str = "",
    flex_lp: int = 0,
) -> Optional[int]:
    conn = get_connection()
    cur = conn.cursor()

    solo_total: Optional[int] = (
        tier_to_total_lp(solo_tier, solo_rank, solo_lp) if solo_tier else None
    )
    flex_total: Optional[int] = (
        tier_to_total_lp(flex_tier, flex_rank, flex_lp) if flex_tier else None
    )
    active_total = solo_total if solo_total is not None else flex_total
    label = format_rank_lp(active_total) if active_total is not None else ""

    cur.execute(
        """
        UPDATE players
        SET solo_tier = ?, solo_rank = ?, solo_lp = ?, solo_total_lp = ?,
            flex_tier = ?, flex_rank = ?, flex_lp = ?, flex_total_lp = ?,
            rank_label = ?
        WHERE puuid = ?
        """,
        (
            solo_tier,
            solo_rank,
            int(solo_lp),
            solo_total,
            flex_tier,
            flex_rank,
            int(flex_lp),
            flex_total,
            label,
            puuid,
        ),
    )
    conn.commit()
    conn.close()

    if active_total is not None:
        recompute_player_match_ranks(puuid, current_total_lp=active_total)
    return active_total


def insert_match(match_record: Dict[str, Any]) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR REPLACE INTO matches (
            match_id, puuid, player_name, game_creation, game_duration,
            champion_name, team_position, queue_id, win,
            kills, deaths, assists, is_demo, metrics_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            match_record["match_id"],
            match_record.get("puuid", ""),
            match_record.get("player_name", ""),
            match_record["game_creation"],
            match_record["game_duration"],
            match_record["champion_name"],
            match_record["team_position"],
            match_record["queue_id"],
            1 if match_record["win"] else 0,
            match_record["kills"],
            match_record["deaths"],
            match_record["assists"],
            1 if match_record.get("is_demo", False) else 0,
            json.dumps(match_record["metrics"]),
        ),
    )
    conn.commit()
    conn.close()


def clear_demo_matches() -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM matches WHERE is_demo = 1")
    cur.execute("DELETE FROM players WHERE puuid = 'DEMO_PUUID'")
    conn.commit()
    conn.close()


def get_match_count() -> Dict[str, int]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as total, SUM(CASE WHEN is_demo = 1 THEN 1 ELSE 0 END) as demo FROM matches")
    row = cur.fetchone()
    conn.close()
    total = row["total"] or 0
    demo = row["demo"] or 0
    return {"total": total, "demo": demo, "real": total - demo}


def get_available_filters(puuid: Optional[str] = None) -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()

    # Liste aller gespeicherten Spieler (aus matches + players gruppiert)
    cur.execute(
        """
        SELECT
            m.puuid,
            MAX(m.player_name) as m_player_name,
            p.game_name,
            p.tag_line,
            p.region,
            p.rank_label,
            p.solo_total_lp,
            COUNT(*) as games,
            SUM(m.win) as wins,
            MAX(m.is_demo) as is_demo
        FROM matches m
        LEFT JOIN players p ON m.puuid = p.puuid
        GROUP BY m.puuid
        ORDER BY is_demo ASC, games DESC
        """
    )
    players = []
    for r in cur.fetchall():
        p_id = r["puuid"] or "UNKNOWN"
        if r["game_name"] and r["tag_line"]:
            label = f"{r['game_name']}#{r['tag_line']}"
            g_name = r["game_name"]
            t_line = r["tag_line"]
            reg = r["region"] or "EUW1"
        elif r["m_player_name"]:
            label = r["m_player_name"]
            parts = label.split("#", 1)
            g_name = parts[0]
            t_line = parts[1] if len(parts) > 1 else "EUW"
            reg = "EUW1"
        elif p_id == "DEMO_PUUID":
            label = "DemoPlayer#EUW"
            g_name = "DemoPlayer"
            t_line = "EUW"
            reg = "EUW1"
        else:
            label = f"Spieler ({p_id[:8]}...)"
            g_name = label
            t_line = ""
            reg = "EUW1"

        games_cnt = r["games"] or 0
        wins_cnt = r["wins"] or 0
        wr = round((wins_cnt / games_cnt) * 100, 1) if games_cnt > 0 else 0.0
        players.append(
            {
                "puuid": p_id,
                "label": label,
                "game_name": g_name,
                "tag_line": t_line,
                "region": reg,
                "rank_label": r["rank_label"] or "",
                "solo_total_lp": r["solo_total_lp"],
                "games": games_cnt,
                "winrate": wr,
                "is_demo": bool(r["is_demo"]),
            }
        )

    where_clause = "WHERE 1=1"
    where_params: List[Any] = []
    if puuid and puuid != "ALL":
        where_clause += " AND puuid = ?"
        where_params.append(puuid)

    cur.execute(
        f"""
        SELECT champion_name, COUNT(*) as games,
               SUM(win) as wins
        FROM matches
        {where_clause}
        GROUP BY champion_name
        ORDER BY games DESC, champion_name ASC
        """,
        where_params,
    )
    champions = [
        {
            "name": r["champion_name"],
            "games": r["games"],
            "winrate": round((r["wins"] / r["games"]) * 100, 1) if r["games"] > 0 else 0,
        }
        for r in cur.fetchall()
    ]

    cur.execute(
        f"""
        SELECT team_position, COUNT(*) as games
        FROM matches
        {where_clause}
        GROUP BY team_position
        ORDER BY games DESC
        """,
        where_params,
    )
    roles = [{"role": r["team_position"], "games": r["games"]} for r in cur.fetchall()]

    cur.execute(
        f"""
        SELECT queue_id, COUNT(*) as games
        FROM matches
        {where_clause}
        GROUP BY queue_id
        ORDER BY games DESC
        """,
        where_params,
    )
    queues = [{"queue_id": r["queue_id"], "games": r["games"]} for r in cur.fetchall()]

    cur.execute(
        f"SELECT MIN(game_creation) as min_ts, MAX(game_creation) as max_ts FROM matches {where_clause}",
        where_params,
    )
    ts_row = cur.fetchone()
    min_date = (
        datetime.fromtimestamp(ts_row["min_ts"] / 1000.0).strftime("%Y-%m-%d")
        if ts_row and ts_row["min_ts"]
        else None
    )
    max_date = (
        datetime.fromtimestamp(ts_row["max_ts"] / 1000.0).strftime("%Y-%m-%d")
        if ts_row and ts_row["max_ts"]
        else None
    )

    conn.close()
    return {
        "players": players,
        "champions": champions,
        "roles": roles,
        "queues": queues,
        "counts": get_match_count(),
        "date_bounds": {"min_date": min_date, "max_date": max_date},
    }


def _compute_win_loss_analysis(parsed_matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analysiert über alle aktuell ausgewählten Spiele hinweg:
    - Welche Stats bei Siegen besonders hoch/gut sind vs. bei Niederlagen.
    - Statistische Trennschärfe (Cohen's d Effektstärke & Impact-Score 0..100).
    - Persönlicher Winrate-Schwellenwert (Sweetspot/Kipppunkt) für jeden Stat.
    """
    total = len(parsed_matches)
    win_matches = [m for m in parsed_matches if m["win"] == 1]
    loss_matches = [m for m in parsed_matches if m["win"] == 0]
    wins_count = len(win_matches)
    losses_count = len(loss_matches)
    overall_wr = round((wins_count / total) * 100, 1) if total > 0 else 0.0

    avg_dur_win = (
        round(sum(m["game_duration"] / 60.0 for m in win_matches) / wins_count, 1)
        if wins_count > 0
        else 0.0
    )
    avg_dur_loss = (
        round(sum(m["game_duration"] / 60.0 for m in loss_matches) / losses_count, 1)
        if losses_count > 0
        else 0.0
    )

    items: List[Dict[str, Any]] = []
    if wins_count == 0 or losses_count == 0:
        return {
            "summary": {
                "total_matches": total,
                "wins_count": wins_count,
                "losses_count": losses_count,
                "winrate": overall_wr,
                "avg_duration_win": avg_dur_win,
                "avg_duration_loss": avg_dur_loss,
            },
            "items": items,
        }

    for stat_cfg in STATS_CONFIG:
        sid = stat_cfg["id"]
        if sid == "winrate":
            continue

        decimals = stat_cfg["decimals"]
        higher_is_better = stat_cfg["higher_is_better"]

        win_vals = [float(m["metrics"][sid]) for m in win_matches if m["metrics"].get(sid) is not None]
        loss_vals = [float(m["metrics"][sid]) for m in loss_matches if m["metrics"].get(sid) is not None]
        all_vals = [float(m["metrics"][sid]) for m in parsed_matches if m["metrics"].get(sid) is not None]

        if sid == "kda":
            w_k = sum(m["kills"] for m in win_matches)
            w_d = sum(m["deaths"] for m in win_matches)
            w_a = sum(m["assists"] for m in win_matches)
            win_avg = (w_k + w_a) / max(1, w_d)

            l_k = sum(m["kills"] for m in loss_matches)
            l_d = sum(m["deaths"] for m in loss_matches)
            l_a = sum(m["assists"] for m in loss_matches)
            loss_avg = (l_k + l_a) / max(1, l_d)

            a_k = sum(m["kills"] for m in parsed_matches)
            a_d = sum(m["deaths"] for m in parsed_matches)
            a_a = sum(m["assists"] for m in parsed_matches)
            overall_avg = (a_k + a_a) / max(1, a_d)
        else:
            win_avg = sum(win_vals) / len(win_vals) if win_vals else 0.0
            loss_avg = sum(loss_vals) / len(loss_vals) if loss_vals else 0.0
            overall_avg = sum(all_vals) / len(all_vals) if all_vals else 0.0

        # Berechne gepoolte Standardabweichung & Cohen's d
        w_mean = sum(win_vals) / len(win_vals) if win_vals else 0.0
        l_mean = sum(loss_vals) / len(loss_vals) if loss_vals else 0.0
        w_var = sum((x - w_mean) ** 2 for x in win_vals)
        l_var = sum((x - l_mean) ** 2 for x in loss_vals)
        dof = max(1, len(win_vals) + len(loss_vals) - 2)
        s_pooled = math.sqrt((w_var + l_var) / dof)

        if s_pooled > 1e-9 and win_vals and loss_vals:
            d_raw = (w_mean - l_mean) / s_pooled
        else:
            d_raw = 0.0

        abs_d = abs(d_raw)
        impact_score = min(100, int(round(abs_d * 68)))

        if abs_d >= 0.80:
            impact_level = "very_high"
            impact_label = "🔥 Entscheidender Faktor"
        elif abs_d >= 0.48:
            impact_level = "high"
            impact_label = "⚡ Starker Einfluss"
        elif abs_d >= 0.22:
            impact_level = "medium"
            impact_label = "📊 Spürbarer Unterschied"
        else:
            impact_level = "low"
            impact_label = "➖ Kaum Unterschied"

        diff_abs = round(win_avg - loss_avg, decimals)
        is_diff_metric = sid in (
            "gold_diff_10",
            "gold_diff_15",
            "gold_diff_20",
            "gold_diff_25",
            "cs_diff_15",
            "xp_diff_15",
            "vision_diff_opponent",
        )

        if not is_diff_metric and abs(loss_avg) > 1e-6:
            diff_pct = round(((win_avg - loss_avg) / abs(loss_avg)) * 100, 1)
        elif abs(overall_avg) > 1e-6:
            diff_pct = round(((win_avg - loss_avg) / abs(overall_avg)) * 100, 1)
        else:
            diff_pct = 0.0

        if not is_diff_metric and abs(win_avg) > 1e-6:
            loss_vs_win_pct = round(((loss_avg - win_avg) / abs(win_avg)) * 100, 1)
        else:
            loss_vs_win_pct = -diff_pct

        # Schwellenwert / Sweetspot-Analyse (Winrate über vs. unter Durchschnitt/Median)
        sorted_all = sorted(all_vals)
        median_val = sorted_all[len(sorted_all) // 2] if sorted_all else 0.0
        raw_threshold = (overall_avg + median_val) / 2.0
        if stat_cfg.get("format") == "mm_ss":
            threshold = round(raw_threshold, decimals)
            total_secs = int(round(abs(threshold) * 60))
            thresh_str = f"{total_secs // 60}:{total_secs % 60:02d}{stat_cfg['unit']}"
        elif stat_cfg.get("format") == "rank_lp":
            threshold = float(round(raw_threshold))
            thresh_str = format_rank_lp(threshold, short=True)
        elif decimals == 0 or sid in (
            "deaths_per_game",
            "kills_per_game",
            "assists_per_game",
            "solo_kills",
            "turret_plates",
            "control_wards_bought",
            "epic_monster_takedowns",
        ):
            threshold = float(round(raw_threshold))
            thresh_str = f"{int(threshold)}{stat_cfg['unit']}"
        else:
            threshold = round(raw_threshold, decimals)
            thresh_str = f"{threshold:.{decimals}f}{stat_cfg['unit']}"

        valid_matches = [m for m in parsed_matches if m["metrics"].get(sid) is not None]
        if higher_is_better is False:
            # Weniger ist besser (z.B. Tode pro Spiel oder früherer Item-Kaufzeitpunkt)
            good_matches = [m for m in valid_matches if float(m["metrics"][sid]) <= threshold]
            bad_matches = [m for m in valid_matches if float(m["metrics"][sid]) > threshold]
            good_cond_label = f"≤ {thresh_str}"
            bad_cond_label = f"> {thresh_str}"
        else:
            # Mehr ist besser
            good_matches = [m for m in valid_matches if float(m["metrics"][sid]) >= threshold]
            bad_matches = [m for m in valid_matches if float(m["metrics"][sid]) < threshold]
            good_cond_label = f"≥ {thresh_str}"
            bad_cond_label = f"< {thresh_str}"

        good_games = len(good_matches)
        bad_games = len(bad_matches)
        good_wr = (
            round((sum(m["win"] for m in good_matches) / good_games) * 100, 1)
            if good_games > 0
            else overall_wr
        )
        bad_wr = (
            round((sum(m["win"] for m in bad_matches) / bad_games) * 100, 1)
            if bad_games > 0
            else overall_wr
        )
        wr_swing = round(good_wr - bad_wr, 1) if (good_games > 0 and bad_games > 0) else 0.0

        items.append(
            {
                "id": sid,
                "name": stat_cfg["name"],
                "category": stat_cfg["category"],
                "unit": stat_cfg["unit"],
                "decimals": decimals,
                "format": stat_cfg.get("format"),
                "higher_is_better": higher_is_better,
                "is_diff_metric": is_diff_metric,
                "color": stat_cfg["color"],
                "description": stat_cfg["description"],
                "win_avg": round(win_avg, decimals),
                "loss_avg": round(loss_avg, decimals),
                "overall_avg": round(overall_avg, decimals),
                "diff_abs": diff_abs,
                "diff_pct": diff_pct,
                "loss_vs_win_pct": loss_vs_win_pct,
                "effect_size_d": round(d_raw, 2),
                "abs_effect": round(abs_d, 2),
                "impact_score": impact_score,
                "impact_level": impact_level,
                "impact_label": impact_label,
                "threshold": threshold,
                "threshold_good_label": good_cond_label,
                "threshold_bad_label": bad_cond_label,
                "good_winrate": good_wr,
                "good_games": good_games,
                "bad_winrate": bad_wr,
                "bad_games": bad_games,
                "winrate_swing": wr_swing,
            }
        )

    items.sort(key=lambda x: x["abs_effect"], reverse=True)

    return {
        "summary": {
            "total_matches": total,
            "wins_count": wins_count,
            "losses_count": losses_count,
            "winrate": overall_wr,
            "avg_duration_win": avg_dur_win,
            "avg_duration_loss": avg_dur_loss,
        },
        "items": items,
    }


def _linear_regression(values: List[float]) -> List[float]:
    """Berechnet eine lineare Trendlinie (Kleinste-Quadrate-Methode) über die Datenpunkte."""
    n = len(values)
    if n <= 1:
        return list(values)
    sum_x = sum(range(n))
    sum_y = sum(values)
    sum_xy = sum(i * v for i, v in enumerate(values))
    sum_x2 = sum(i * i for i in range(n))

    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return list(values)

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return [slope * i + intercept for i in range(n)]


def compute_timeseries(
    puuid: Optional[str] = None,
    champion: Optional[str] = None,
    role: Optional[str] = None,
    queue_id: Optional[int] = None,
    group_size: int = 5,
    mode: str = "blocks",
    range_mode: str = "count",
    limit_games: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Aggregiert die gefilterten Spiele chronologisch zu Datenpunkten.
    - Zuerst wird nach Spieler (puuid), Champion, Rolle und Queue gefiltert.
    - Danach wird je nach `range_mode` eingegrenzt:
        'count' -> Die aktuellsten `limit_games` Spiele, die zur Auswahl (Spieler/Champion/Rolle/Queue) passen.
        'date'  -> Alle zur Auswahl passenden Spiele im Zeitraum [date_from, date_to] (Standard: bis Gegenwart).
    - group_size: 1, 5, 10, 20
    - mode:
        'blocks'  -> Feste disjunkte Blöcke à N Spiele (vom neuesten Spiel rückwärts gruppiert,
                     sodass der letzte Datenpunkt immer exakt die aktuellsten N Spiele abbildet).
        'rolling' -> Gleitender Durchschnitt (Rolling Window über je N Spiele).
    """
    group_size = max(1, int(group_size))
    conn = get_connection()
    cur = conn.cursor()

    query = "SELECT * FROM matches WHERE 1=1"
    params: List[Any] = []

    if puuid and puuid != "ALL":
        query += " AND puuid = ?"
        params.append(puuid)
    if champion and champion != "ALL":
        query += " AND champion_name = ?"
        params.append(champion)
    if role and role != "ALL":
        query += " AND team_position = ?"
        params.append(role)
    if queue_id is not None and str(queue_id) != "ALL" and int(queue_id) > 0:
        query += " AND queue_id = ?"
        params.append(int(queue_id))

    query += " ORDER BY game_creation ASC"
    cur.execute(query, params)
    all_matching_rows = cur.fetchall()
    conn.close()

    matching_pool_count = len(all_matching_rows)
    pool_min_date = (
        datetime.fromtimestamp(all_matching_rows[0]["game_creation"] / 1000.0).strftime("%Y-%m-%d")
        if matching_pool_count > 0
        else None
    )
    pool_max_date = (
        datetime.fromtimestamp(all_matching_rows[-1]["game_creation"] / 1000.0).strftime("%Y-%m-%d")
        if matching_pool_count > 0
        else None
    )

    # Wende nun den Zeitraum- oder Spielanzahl-Filter auf die bereits nach Champion/Rolle/Queue gefilterten Spiele an
    rows = list(all_matching_rows)
    if range_mode == "date":
        ts_from_ms: Optional[int] = None
        ts_to_ms: Optional[int] = None
        if date_from and date_from.strip():
            try:
                dt_from = datetime.strptime(date_from.strip(), "%Y-%m-%d").replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                ts_from_ms = int(dt_from.timestamp() * 1000)
            except ValueError:
                ts_from_ms = None

        if date_to and date_to.strip():
            try:
                dt_to = datetime.strptime(date_to.strip(), "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, microsecond=999999
                )
                ts_to_ms = int(dt_to.timestamp() * 1000)
            except ValueError:
                ts_to_ms = None

        if ts_from_ms is not None:
            rows = [r for r in rows if r["game_creation"] >= ts_from_ms]
        if ts_to_ms is not None:
            rows = [r for r in rows if r["game_creation"] <= ts_to_ms]
    else:
        # range_mode == 'count': Nimm die aktuellsten `limit_games` passenden Spiele
        if limit_games is not None and int(limit_games) > 0:
            lim = int(limit_games)
            if len(rows) > lim:
                rows = rows[-lim:]

    parsed_matches = []
    for r in rows:
        metrics = json.loads(r["metrics_json"])
        parsed_matches.append(
            {
                "match_id": r["match_id"],
                "game_creation": r["game_creation"],
                "game_duration": r["game_duration"],
                "champion_name": r["champion_name"],
                "team_position": r["team_position"],
                "queue_id": r["queue_id"],
                "win": r["win"],
                "kills": r["kills"],
                "deaths": r["deaths"],
                "assists": r["assists"],
                "metrics": metrics,
            }
        )

    total_matches = len(parsed_matches)
    if total_matches == 0:
        return {
            "total_matches": 0,
            "wins_count": 0,
            "losses_count": 0,
            "overall_winrate": 0.0,
            "matching_pool_count": matching_pool_count,
            "pool_min_date": pool_min_date,
            "pool_max_date": pool_max_date,
            "bucket_count": 0,
            "group_size": group_size,
            "mode": mode,
            "range_mode": range_mode,
            "limit_games": limit_games,
            "date_from": date_from,
            "date_to": date_to,
            "buckets_meta": [],
            "stats": {},
            "win_loss_analysis": {
                "summary": {
                    "total_matches": 0,
                    "wins_count": 0,
                    "losses_count": 0,
                    "winrate": 0.0,
                    "avg_duration_win": 0.0,
                    "avg_duration_loss": 0.0,
                },
                "items": [],
            },
            "player_filters": get_available_filters(puuid),
        }

    # Erzeuge Gruppen (Buckets) je nach Modus
    buckets: List[List[Dict[str, Any]]] = []
    if group_size == 1:
        buckets = [[m] for m in parsed_matches]
    elif mode == "rolling":
        if total_matches <= group_size:
            buckets = [parsed_matches]
        else:
            for end_idx in range(group_size, total_matches + 1):
                buckets.append(parsed_matches[end_idx - group_size : end_idx])
    else:
        # Feste Blöcke ('blocks'): Vom neuesten Spiel rückwärts in N-er Pakete teilen,
        # damit der letzte Punkt rechts immer die aktuellsten N Spiele sind.
        if total_matches <= group_size:
            buckets = [parsed_matches]
        else:
            temp_buckets = []
            idx = total_matches
            while idx >= group_size:
                temp_buckets.append(parsed_matches[idx - group_size : idx])
                idx -= group_size
            # Falls am Anfang noch ein Rest >= ceil(group_size / 2) übrig ist, optional mitnehmen
            # oder nur volle N-er Blöcke verwenden, damit die Varianz jedes Punkts identisch ist.
            temp_buckets.reverse()
            buckets = temp_buckets

    # Metadaten für jeden Datenpunkt auf der X-Achse (für Labels & Tooltips)
    buckets_meta = []
    for b_idx, bucket in enumerate(buckets):
        first_dt = datetime.fromtimestamp(bucket[0]["game_creation"] / 1000.0)
        last_dt = datetime.fromtimestamp(bucket[-1]["game_creation"] / 1000.0)
        wins = sum(m["win"] for m in bucket)
        winrate = round((wins / len(bucket)) * 100, 1)

        # Häufigste Champions im Block
        champ_counts: Dict[str, int] = {}
        for m in bucket:
            c = m["champion_name"]
            champ_counts[c] = champ_counts.get(c, 0) + 1
        top_champs = sorted(champ_counts.items(), key=lambda x: x[1], reverse=True)
        champ_summary = ", ".join(
            f"{c} ({cnt})" if len(bucket) > 1 else c for c, cnt in top_champs[:3]
        )

        if group_size == 1:
            label = f"#{b_idx + 1} ({first_dt.strftime('%d.%m.')})"
            range_label = f"Spiel #{b_idx + 1} am {first_dt.strftime('%d.%m.%Y %H:%M')}"
        elif mode == "rolling":
            start_game_num = b_idx + 1
            end_game_num = b_idx + len(bucket)
            label = f"Sp. {start_game_num}–{end_game_num}"
            range_label = (
                f"Spiele {start_game_num}–{end_game_num} "
                f"({first_dt.strftime('%d.%m.')} – {last_dt.strftime('%d.%m.%Y')})"
            )
        else:
            offset = total_matches - len(buckets) * group_size
            start_game_num = max(1, offset + b_idx * group_size + 1)
            end_game_num = start_game_num + len(bucket) - 1
            label = f"Block {b_idx + 1} ({last_dt.strftime('%d.%m.')})"
            range_label = (
                f"Block {b_idx + 1}: Spiele {start_game_num}–{end_game_num} "
                f"({first_dt.strftime('%d.%m.')} – {last_dt.strftime('%d.%m.%Y')})"
            )

        buckets_meta.append(
            {
                "index": b_idx,
                "label": label,
                "range_label": range_label,
                "games_count": len(bucket),
                "winrate": winrate,
                "champions": champ_summary,
                "date_end": last_dt.strftime("%d.%m.%Y"),
                "timestamp_ms": int(bucket[-1]["game_creation"]),
                "first_timestamp_ms": int(bucket[0]["game_creation"]),
            }
        )

    # Berechne Zeitreihen und Trend-Indikatoren für alle 45 Stats
    stats_result: Dict[str, Any] = {}
    for stat_cfg in STATS_CONFIG:
        sid = stat_cfg["id"]
        decimals = stat_cfg["decimals"]
        higher_is_better = stat_cfg["higher_is_better"]

        # Gesamtdurchschnitt über alle gefilterten Einzelspiele mit gültigem Messwert
        if sid == "kda":
            all_k = sum(m["kills"] for m in parsed_matches)
            all_d = sum(m["deaths"] for m in parsed_matches)
            all_a = sum(m["assists"] for m in parsed_matches)
            overall_avg = round((all_k + all_a) / max(1, all_d), decimals)
        else:
            all_vals = [float(m["metrics"][sid]) for m in parsed_matches if m["metrics"].get(sid) is not None]
            overall_avg = round(sum(all_vals) / len(all_vals), decimals) if all_vals else 0.0

        series_values: List[float] = []
        last_valid_val: float = overall_avg
        for bucket in buckets:
            if sid == "kda" and len(bucket) > 1:
                total_k = sum(m["kills"] for m in bucket)
                total_d = sum(m["deaths"] for m in bucket)
                total_a = sum(m["assists"] for m in bucket)
                val = (total_k + total_a) / max(1, total_d)
                last_valid_val = val
            elif sid == "rank_lp":
                vals = [float(m["metrics"][sid]) for m in bucket if m["metrics"].get(sid) is not None]
                if vals:
                    val = vals[-1]
                    last_valid_val = val
                else:
                    val = last_valid_val
            else:
                vals = [float(m["metrics"][sid]) for m in bucket if m["metrics"].get(sid) is not None]
                if vals:
                    val = sum(vals) / len(vals)
                    last_valid_val = val
                else:
                    val = last_valid_val
            series_values.append(round(val, decimals))

        # Trendberechnung: Vergleich der ersten Hälfte (oder bisherigen Spiele) mit dem aktuellen Ende
        trend_line = [round(v, decimals) for v in _linear_regression(series_values)]

        if len(series_values) >= 2:
            split_idx = max(1, len(series_values) // 2)
            early_vals = series_values[:split_idx]
            recent_vals = series_values[-max(1, len(series_values) // 3) :]
            early_avg = sum(early_vals) / len(early_vals)
            recent_avg = series_values[-1]  # Aktuellster Datenpunkt (letzte N Spiele)
            baseline = early_avg if early_avg != 0 else overall_avg
            delta_abs = round(recent_avg - early_avg, decimals)
            if abs(baseline) > 1e-6:
                delta_pct = round(((recent_avg - early_avg) / abs(baseline)) * 100, 1)
            else:
                delta_pct = 0.0
        else:
            early_avg = series_values[0] if series_values else 0.0
            recent_avg = series_values[-1] if series_values else 0.0
            delta_abs = 0.0
            delta_pct = 0.0

        is_improving: Optional[bool] = None
        if higher_is_better is not None and abs(delta_abs) > 1e-6:
            if higher_is_better:
                is_improving = delta_abs > 0
            else:
                is_improving = delta_abs < 0

        stats_result[sid] = {
            "id": sid,
            "values": series_values,
            "trend_line": trend_line,
            "overall_avg": overall_avg,
            "recent_val": recent_avg,
            "early_avg": round(early_avg, decimals),
            "delta_abs": delta_abs,
            "delta_pct": delta_pct,
            "is_improving": is_improving,
        }

    win_loss_analysis = _compute_win_loss_analysis(parsed_matches)

    return {
        "total_matches": total_matches,
        "wins_count": win_loss_analysis["summary"]["wins_count"],
        "losses_count": win_loss_analysis["summary"]["losses_count"],
        "overall_winrate": win_loss_analysis["summary"]["winrate"],
        "matching_pool_count": matching_pool_count,
        "pool_min_date": pool_min_date,
        "pool_max_date": pool_max_date,
        "bucket_count": len(buckets),
        "group_size": group_size,
        "mode": mode,
        "range_mode": range_mode,
        "limit_games": limit_games,
        "date_from": date_from,
        "date_to": date_to,
        "buckets_meta": buckets_meta,
        "stats": stats_result,
        "win_loss_analysis": win_loss_analysis,
        "player_filters": get_available_filters(puuid),
    }


def _load_filtered_matches(
    puuid: Optional[str],
    champion: Optional[str],
    role: Optional[str],
    queue_id: Optional[int],
    range_mode: str,
    limit_games: Optional[int],
    date_from: Optional[str],
    date_to: Optional[str],
) -> List[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()

    query = "SELECT * FROM matches WHERE 1=1"
    params: List[Any] = []

    if puuid and puuid != "ALL":
        query += " AND puuid = ?"
        params.append(puuid)
    if champion and champion != "ALL":
        query += " AND champion_name = ?"
        params.append(champion)
    if role and role != "ALL":
        query += " AND team_position = ?"
        params.append(role)
    if queue_id is not None and str(queue_id) != "ALL" and int(queue_id) > 0:
        query += " AND queue_id = ?"
        params.append(int(queue_id))

    query += " ORDER BY game_creation ASC"
    cur.execute(query, params)
    rows = list(cur.fetchall())
    conn.close()

    if range_mode == "date":
        if date_from and date_from.strip():
            try:
                dt_from = datetime.strptime(date_from.strip(), "%Y-%m-%d").replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                ts_from = int(dt_from.timestamp() * 1000)
                rows = [r for r in rows if r["game_creation"] >= ts_from]
            except ValueError:
                pass
        if date_to and date_to.strip():
            try:
                dt_to = datetime.strptime(date_to.strip(), "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59, microsecond=999999
                )
                ts_to = int(dt_to.timestamp() * 1000)
                rows = [r for r in rows if r["game_creation"] <= ts_to]
            except ValueError:
                pass
    else:
        if limit_games is not None and int(limit_games) > 0:
            lim = int(limit_games)
            if len(rows) > lim:
                rows = rows[-lim:]

    return [
        {
            "win": r["win"],
            "kills": r["kills"],
            "deaths": r["deaths"],
            "assists": r["assists"],
            "game_duration": r["game_duration"],
            "metrics": json.loads(r["metrics_json"]),
        }
        for r in rows
    ]


def compare_players(
    puuid_a: str,
    puuid_b: str,
    champion: Optional[str] = None,
    role: Optional[str] = None,
    queue_id: Optional[int] = None,
    group_size: int = 5,
    mode: str = "blocks",
    range_mode: str = "count",
    limit_games: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Vergleicht zwei Spieler (Spieler A = Fokus, Spieler B = Benchmark/Pro/Kumpel)
    unter exakt denselben Filterbedingungen (Champion, Rolle, Queue sowie Anzahl Spiele oder Zeitraum).
    Wenn z.B. 100 MasterYi Ranked Spiele eingestellt sind, werden die letzten 100 MasterYi Ranked Spiele
    von Spieler A mit den letzten 100 MasterYi Ranked Spielen von Spieler B verglichen.
    """
    ts_a = compute_timeseries(
        puuid=puuid_a,
        champion=champion,
        role=role,
        queue_id=queue_id,
        group_size=group_size,
        mode=mode,
        range_mode=range_mode,
        limit_games=limit_games,
        date_from=date_from,
        date_to=date_to,
    )
    ts_b = compute_timeseries(
        puuid=puuid_b,
        champion=champion,
        role=role,
        queue_id=queue_id,
        group_size=group_size,
        mode=mode,
        range_mode=range_mode,
        limit_games=limit_games,
        date_from=date_from,
        date_to=date_to,
    )

    matches_a = _load_filtered_matches(
        puuid_a, champion, role, queue_id, range_mode, limit_games, date_from, date_to
    )
    matches_b = _load_filtered_matches(
        puuid_b, champion, role, queue_id, range_mode, limit_games, date_from, date_to
    )

    all_players = {p["puuid"]: p for p in get_available_filters("ALL").get("players", [])}
    info_a = all_players.get(puuid_a, {"puuid": puuid_a, "label": "Spieler A"})
    info_b = all_players.get(puuid_b, {"puuid": puuid_b, "label": "Spieler B"})

    wl_map_a = {it["id"]: it for it in ts_a.get("win_loss_analysis", {}).get("items", [])}
    wl_map_b = {it["id"]: it for it in ts_b.get("win_loss_analysis", {}).get("items", [])}

    items: List[Dict[str, Any]] = []
    if len(matches_a) > 0 and len(matches_b) > 0:
        for stat_cfg in STATS_CONFIG:
            sid = stat_cfg["id"]
            decimals = stat_cfg["decimals"]
            higher_is_better = stat_cfg["higher_is_better"]
            is_diff_metric = sid in (
                "gold_diff_10",
                "gold_diff_15",
                "gold_diff_20",
                "gold_diff_25",
                "cs_diff_15",
                "xp_diff_15",
                "vision_diff_opponent",
            )

            st_a = ts_a["stats"].get(sid, {})
            st_b = ts_b["stats"].get(sid, {})
            avg_a = float(st_a.get("overall_avg", 0.0))
            avg_b = float(st_b.get("overall_avg", 0.0))

            vals_a = [float(m["metrics"][sid]) for m in matches_a if m["metrics"].get(sid) is not None]
            vals_b = [float(m["metrics"][sid]) for m in matches_b if m["metrics"].get(sid) is not None]
            mean_a = sum(vals_a) / len(vals_a) if vals_a else 0.0
            mean_b = sum(vals_b) / len(vals_b) if vals_b else 0.0
            var_a = sum((x - mean_a) ** 2 for x in vals_a)
            var_b = sum((x - mean_b) ** 2 for x in vals_b)
            dof = max(1, len(vals_a) + len(vals_b) - 2)
            s_pooled = math.sqrt((var_a + var_b) / dof)

            if s_pooled > 1e-9 and vals_a and vals_b:
                d_raw = (avg_a - avg_b) / s_pooled
            else:
                d_raw = 0.0

            abs_d = abs(d_raw)
            diff_abs = round(avg_a - avg_b, decimals)

            if not is_diff_metric and abs(avg_b) > 1e-6:
                diff_pct = round(((avg_a - avg_b) / abs(avg_b)) * 100, 1)
            elif abs(avg_a) > 1e-6:
                diff_pct = round(((avg_a - avg_b) / abs(avg_a)) * 100, 1)
            else:
                diff_pct = 0.0

            # Wer schneidet bei diesem Stat besser ab? ('A', 'B' oder 'EQUAL')
            better_player = "EQUAL"
            if abs_d >= 0.10 and abs(diff_abs) > 1e-6:
                if higher_is_better is True:
                    better_player = "A" if avg_a > avg_b else "B"
                elif higher_is_better is False:
                    better_player = "A" if avg_a < avg_b else "B"

            wl_a = wl_map_a.get(sid, {})
            wl_b = wl_map_b.get(sid, {})

            items.append(
                {
                    "id": sid,
                    "name": stat_cfg["name"],
                    "category": stat_cfg["category"],
                    "unit": stat_cfg["unit"],
                    "decimals": decimals,
                    "format": stat_cfg.get("format"),
                    "higher_is_better": higher_is_better,
                    "is_diff_metric": is_diff_metric,
                    "color": stat_cfg["color"],
                    "description": stat_cfg["description"],
                    "avg_a": round(avg_a, decimals),
                    "avg_b": round(avg_b, decimals),
                    "win_avg_a": wl_a.get("win_avg", round(avg_a, decimals)),
                    "loss_avg_a": wl_a.get("loss_avg", round(avg_a, decimals)),
                    "win_avg_b": wl_b.get("win_avg", round(avg_b, decimals)),
                    "loss_avg_b": wl_b.get("loss_avg", round(avg_b, decimals)),
                    "diff_abs": diff_abs,
                    "diff_pct": diff_pct,
                    "effect_size_d": round(d_raw, 2),
                    "abs_effect": round(abs_d, 2),
                    "better_player": better_player,
                    "values_a": st_a.get("values", []),
                    "values_b": st_b.get("values", []),
                }
            )

    items.sort(key=lambda x: x["abs_effect"], reverse=True)

    return {
        "player_a": {
            "puuid": puuid_a,
            "label": info_a.get("label", "Spieler A"),
            "rank_label": info_a.get("rank_label", ""),
            "total_matches": ts_a["total_matches"],
            "matching_pool_count": ts_a["matching_pool_count"],
            "wins_count": ts_a["wins_count"],
            "losses_count": ts_a["losses_count"],
            "winrate": ts_a["overall_winrate"],
            "bucket_count": ts_a["bucket_count"],
            "buckets_meta": ts_a["buckets_meta"],
            "avg_duration": ts_a["stats"].get("game_duration_min", {}).get("overall_avg", 0.0),
        },
        "player_b": {
            "puuid": puuid_b,
            "label": info_b.get("label", "Spieler B"),
            "rank_label": info_b.get("rank_label", ""),
            "total_matches": ts_b["total_matches"],
            "matching_pool_count": ts_b["matching_pool_count"],
            "wins_count": ts_b["wins_count"],
            "losses_count": ts_b["losses_count"],
            "winrate": ts_b["overall_winrate"],
            "bucket_count": ts_b["bucket_count"],
            "buckets_meta": ts_b["buckets_meta"],
            "avg_duration": ts_b["stats"].get("game_duration_min", {}).get("overall_avg", 0.0),
        },
        "items": items,
    }
