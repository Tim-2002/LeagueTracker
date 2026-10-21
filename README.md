# 📈 LeagueTracker – Persönlicher Fortschritts- & Zeitverlaufs-Tracker

**LeagueTracker** ist eine lokale Web-App zur Analyse deiner persönlichen Entwicklung in **League of Legends** über die Zeit. Statt nur statische Durchschnittswerte anzuzeigen, bereitet das Tool **39 spielzeitnormierte Metriken** als interaktive Zeitverlaufs-Kurven, Sieg-vs.-Niederlage-Analysen und Direktvergleiche mit Freunden oder Pro-Playern auf.

---

## ✨ Features im Überblick

- **📊 39 Normierte Trend-Statistiken in 6 Kategorien:**
  - 🏆 **Gesamt-Scores & Erfolg:** 5 normierte Index-Scores (0–100) für *Fighting, Farming, Vision, Aggressiveness & Objectives* sowie Winrate und Spieldauer.
  - ⚔️ **Kampf, KDA & Kills:** KDA, Kill Participation %, Kills, Tode (invertiert: weniger = besser), Assists, Solo-Kills & First-Blood-Rate.
  - 💥 **Schaden, Tankiness & Utility:** DPM, Team-Schadensanteil %, Schaden pro Gold, erlittener/abgewehrter Schaden pro Minute, Heilung/Schilde & CC-Score/Min.
  - 🪙 **Farm, Gold & Laning (@10 / @15):** CS/Min, CS@10, Gold/Min (GPM), echte Laning-Differenzen bei Minute 15 (*Gold-Diff@15, CS-Diff@15, XP-Diff@15* gegen deinen direkten Lane-Gegner aus der Match-Timeline), Turret Plates & Counter-Jungling.
  - 👁️ **Sicht (Vision & Warding):** Vision Score/Min, Vision-Vorteil vs. Lane-Gegner, platzierte Wards/10m, zerstörte Wards vor Min. 20 & Control Wards.
  - 🐉 **Objectives & Map-Kontrolle:** Turmschaden/Min, Objective-Schaden/Min, First-Tower-Rate & Epische Monster-Beteiligungen.
- **🔎 Flexible Filter & Gruppierungen:**
  - Filtere nach **Spieler (Account)**, **Champion**, **Rolle/Lane**, **Queue** (Solo/Duo, Flex, Normal Draft, ARAM) sowie **Anzahl der letzten passenden Spiele** (z. B. letzte 25, 50, 100 Spiele) oder **Datumszeitraum (Von–Bis)**.
  - Gruppiere Datenpunkte wahlweise in **1, 5, 10 oder 20 Spiele** (als *Feste Blöcke* oder *Gleitender Schnitt*).
  - Schalte die X-Achse zwischen **Spiel-korrekt** (gleicher Abstand pro Spiel/Block) und **Zeit-korrekt** (echte Kalender-Zeitachse) um.
- **⭐ Persönliche Favoriten-Seite:** Stelle dir dein eigenes Dashboard aus deinen wichtigsten Metriken zusammen und sortiere die Karten frei per Klick.
- **🏆 Sieg vs. Niederlage Analyse:** Findet über statistische Effektstärke (*Cohen's d*) heraus, welche Werte deine Siege wirklich entscheiden, und berechnet für jeden Stat deinen persönlichen **Winrate-Kipppunkt (Sweetspot)**.
- **⚔️ 2-Spieler-Vergleich (Kumpel / Pro-Benchmark):** Vergleiche zwei beliebige gespeicherte Accounts (z. B. dich selbst mit einem Kumpel oder einem Challenger-OTP wie `Sinerias#EUW`) unter exakt denselben Filterbedingungen.
- **🎲 Sofort startklar dank Demo-Modus:** Beim ersten Start ohne Datenbank werden automatisch 80 realistische Demo-Spiele generiert, sodass alle Funktionen sofort ausprobiert werden können.

---

## 🚀 Schnellstart (Für Kumpels & neue Nutzer)

### 1. Voraussetzung: Python installieren
Stelle sicher, dass **Python 3.9 oder neuer** auf deinem PC installiert ist:
- Download unter: [https://www.python.org/downloads/](https://www.python.org/downloads/)
- ⚠️ **Wichtig bei der Installation unter Windows:** Setze unten im Installer unbedingt den Haken bei **„Add Python to PATH“**!

### 2. Projekt herunterladen
- **Option A (Ohne Git – als ZIP):** Klicke oben auf dieser GitHub-Seite auf den grünen Button **„<> Code“** → **„Download ZIP“** und entpacke den Ordner an einen beliebigen Ort auf deinem PC.
- **Option B (Mit Git):**
  ```bash
  git clone https://github.com/Tim-2002/LeagueTracker.git
  cd LeagueTracker
  ```

### 3. App starten (`start.bat`)
Doppelklicke einfach auf **`start.bat`** im Projektordner!
- Beim allerersten Start installiert das Skript automatisch die benötigten Python-Pakete (`fastapi`, `uvicorn`, `requests`, `pydantic`).
- Danach startet der lokale Server und öffnet automatisch deinen Browser unter **[http://127.0.0.1:8000](http://127.0.0.1:8000)**.

*(Alternativ manuell über das Terminal starten:)*
```bash
pip install -r requirements.txt
python backend/app.py
```

---

## 🔑 Eigene LoL-Spiele & Freunde über die Riot API laden

Jeder Nutzer lädt seine eigenen Spieldaten selbst herunter. Dafür benötigst du einen kostenlosen **Riot Games Developer API Key**:

1. Öffne das offizielle Riot Developer Portal: **[https://developer.riotgames.com/](https://developer.riotgames.com/)**
2. Melde dich oben rechts mit deinem normalen **Riot / League of Legends Account** an.
3. Direkt auf der Startseite findest du unter **„Development API Key“** deinen persönlichen Schlüssel (beginnt mit `RGAPI-...`).
   - Falls er abgelaufen ist (Development Keys sind 24 Stunden gültig), klicke auf **„Regenerate API Key“** (Haken beim Captcha setzen) und kopiere den Key.
4. Klicke in **LeagueTracker** oben rechts auf den blauen Button **„🔄 Riot Sync / Daten“**:
   - Gib deinen **Riot Namen** (z. B. `Faker`), deine **Tagline** (z. B. `EUW` ohne `#`) und die **Region** (`EUW`) ein.
   - Füge deinen **Riot API Key (`RGAPI-...`)** ein.
   - Wähle die gewünschte **Queue** (z. B. *Nur Ranked Solo/Duo* – das spart enorm viel Ladezeit!) und die **Anzahl der Spiele** (z. B. `50` oder `100`).
   - Klicke auf **„🚀 Jetzt Matches von Riot synchronisieren“**.

> 💡 **Tipp für den 2-Spieler-Vergleich:** Du kannst über dasselbe Sync-Fenster nacheinander auch die Riot-IDs deiner Kumpels oder von Pro-Playern eingeben und deren Spiele herunterladen. Alle Accounts werden sauber getrennt in deiner lokalen Datenbank (`data/leaguetracker.db`) gespeichert und können anschließend oben im Spieler-Dropdown oder im Tab **„⚔️ 2 Spieler vergleichen“** ausgewählt werden!

---

## 📁 Projektstruktur

```text
LeagueTracker/
├── backend/
│   ├── app.py             # FastAPI Webserver & REST-Endpunkte
│   ├── database.py        # SQLite-Verwaltung, Zeitreihen-Aggregation & Win/Loss-Statistik
│   ├── riot_client.py     # Riot API Client (Account-V1, Match-V5, Timeline) & Demo-Generator
│   └── stats_config.py    # Definition aller 39 normierten Statistiken & 6 Kategorien
├── frontend/
│   ├── index.html         # Hauptoberfläche & Modals
│   ├── styles.css         # Dark-Mode Gaming UI Styling
│   └── app.js             # Chart.js Visualisierung, Filter-Logik & Vergleichs-Ansichten
├── data/                  # Lokaler Datenbank-Ordner (leaguetracker.db wird lokal erzeugt & von Git ignoriert)
├── requirements.txt       # Python-Abhängigkeiten
├── start.bat              # 1-Klick Starter für Windows (inkl. Auto-Setup)
└── stats_liste.txt        # Übersicht aller 39 berechneten Metriken
```

## 🔒 Datenschutz & Lokale Speicherung
Deine heruntergeladenen Matches sowie dein in den Einstellungen gespeicherter Riot API Key liegen **ausschließlich lokal auf deinem PC** in der Datei `data/leaguetracker.db`. Durch die `.gitignore` wird diese Datenbankdatei niemals zu GitHub hochgeladen.
