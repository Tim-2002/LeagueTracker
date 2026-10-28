/**
 * LeagueTracker Frontend Application
 * Rendert die Zeitverlaufs-Graphen (1, 5, 10, 20 Spiele), Favoriten-Seite & Filter.
 */

function getTodayDateStr() {
    const now = new Date();
    const yyyy = now.getFullYear();
    const mm = String(now.getMonth() + 1).padStart(2, "0");
    const dd = String(now.getDate()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd}`;
}

const state = {
    categories: [],
    statsConfig: [],
    statsById: {},
    favorites: [],
    filters: { players: [], champions: [], roles: [], queues: [], counts: { total: 0, demo: 0, real: 0 }, date_bounds: {} },
    settings: {},
    activeTab: "favorites", // 'favorites', 'winloss', 'scores', 'combat', 'damage', 'farming', 'vision', 'objectives', 'all'
    selectedPuuid: "", // PUUID des aktuell ausgewählten Spielers
    selectedChampion: "ALL",
    selectedRole: "ALL",
    selectedQueue: "ALL",
    rangeMode: "count", // 'count' (Anzahl Spiele) oder 'date' (Datum Von-Bis)
    limitGames: null, // null = Alle passenden Spiele, oder Zahl > 0 (z.B. 100)
    dateFrom: "", // 'YYYY-MM-DD'
    dateTo: getTodayDateStr(), // Standardmäßig bis zur Gegenwart (Heute)
    groupSize: 5,
    groupMode: "blocks", // 'blocks' oder 'rolling'
    xAxisMode: "games", // 'games' (Spiel-korrekt) oder 'time' (Zeit-korrekt)
    wlSortMode: "impact", // 'impact', 'swing', 'pct'
    wlCategoryFilter: "ALL",
    comparePuuidA: "",
    comparePuuidB: "",
    compareData: null,
    compareSortMode: "diff", // 'diff', 'b_ahead', 'a_ahead', 'pct'
    compareCategoryFilter: "ALL",
    timeseriesData: null,
    chartInstances: {},
};

const DEFAULT_FAVORITES_LIST = [
    "rank_lp",
    "winrate",
    "kda",
    "cs_per_min",
    "dpm",
    "gold_diff_15",
    "deaths_per_game",
    "kill_participation",
    "vision_score_per_min",
    "score_fighting",
    "score_farming",
];

document.addEventListener("DOMContentLoaded", async () => {
    bindUIEvents();
    await loadMeta();
    await loadTimeseries();
});

function bindUIEvents() {
    // Spieler-Auswahl Dropdown
    const playerSelect = document.getElementById("filter-player");
    if (playerSelect) {
        playerSelect.addEventListener("change", async (e) => {
            const val = e.target.value;
            if (val === "__ADD_NEW__") {
                playerSelect.value = state.selectedPuuid || "ALL";
                document.getElementById("sync-modal").classList.remove("hidden");
                const nameInp = document.getElementById("sync-gamename");
                const tagInp = document.getElementById("sync-tagline");
                if (nameInp && tagInp) {
                    nameInp.value = "";
                    tagInp.value = "";
                    nameInp.focus();
                }
                return;
            }
            state.selectedPuuid = val;
            state.selectedChampion = "ALL";
            await loadTimeseries();
        });
    }

    // Filter-Dropdowns
    document.getElementById("filter-champion").addEventListener("change", async (e) => {
        state.selectedChampion = e.target.value;
        await loadTimeseries();
    });

    document.getElementById("filter-role").addEventListener("change", async (e) => {
        state.selectedRole = e.target.value;
        await loadTimeseries();
    });

    document.getElementById("filter-queue").addEventListener("change", async (e) => {
        state.selectedQueue = e.target.value;
        await loadTimeseries();
    });

    // Umfang-Modus (Anzahl Spiele vs Datum Von-Bis)
    const countPanel = document.getElementById("range-count-panel");
    const datePanel = document.getElementById("range-date-panel");
    const dateFromInput = document.getElementById("filter-date-from");
    const dateToInput = document.getElementById("filter-date-to");
    const limitInput = document.getElementById("filter-limit-games");

    if (dateToInput && !dateToInput.value) {
        dateToInput.value = state.dateTo;
    }

    document.querySelectorAll("#range-mode-control .mini-seg-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            document.querySelectorAll("#range-mode-control .mini-seg-btn").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            state.rangeMode = btn.getAttribute("data-rmode");

            if (state.rangeMode === "date") {
                countPanel.classList.add("hidden");
                datePanel.classList.remove("hidden");
                if (!state.dateTo) {
                    state.dateTo = getTodayDateStr();
                    dateToInput.value = state.dateTo;
                }
                if (!state.dateFrom) {
                    const fallbackMin =
                        (state.timeseriesData && state.timeseriesData.pool_min_date) ||
                        (state.filters.date_bounds && state.filters.date_bounds.min_date) ||
                        "";
                    state.dateFrom = fallbackMin;
                    dateFromInput.value = fallbackMin;
                }
            } else {
                datePanel.classList.add("hidden");
                countPanel.classList.remove("hidden");
            }
            await loadTimeseries();
        });
    });

    // Schnellauswahl-Buttons für Spielanzahl (Alle, 25, 50, 100)
    document.querySelectorAll("#limit-preset-control .seg-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            document.querySelectorAll("#limit-preset-control .seg-btn").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            const raw = btn.getAttribute("data-limit");
            if (raw && parseInt(raw, 10) > 0) {
                state.limitGames = parseInt(raw, 10);
                limitInput.value = String(state.limitGames);
            } else {
                state.limitGames = null;
                limitInput.value = "";
            }
            await loadTimeseries();
        });
    });

    // Freie Eingabe der Spielanzahl (passend zur aktuellen Filter-Auswahl)
    let limitDebounceTimer = null;
    limitInput.addEventListener("input", () => {
        clearTimeout(limitDebounceTimer);
        limitDebounceTimer = setTimeout(async () => {
            const val = parseInt(limitInput.value, 10);
            if (!Number.isNaN(val) && val > 0) {
                state.limitGames = val;
            } else {
                state.limitGames = null;
            }
            const activeStr = state.limitGames ? String(state.limitGames) : "";
            document.querySelectorAll("#limit-preset-control .seg-btn").forEach((b) => {
                b.classList.toggle("active", b.getAttribute("data-limit") === activeStr);
            });
            await loadTimeseries();
        }, 250);
    });

    // Datums-Filter (Von - Bis, Standard: bis Gegenwart)
    dateFromInput.addEventListener("change", async (e) => {
        state.dateFrom = e.target.value || "";
        await loadTimeseries();
    });

    dateToInput.addEventListener("change", async (e) => {
        state.dateTo = e.target.value || "";
        await loadTimeseries();
    });

    document.getElementById("btn-date-reset").addEventListener("click", async () => {
        const minDate =
            (state.timeseriesData && state.timeseriesData.pool_min_date) ||
            (state.filters.date_bounds && state.filters.date_bounds.min_date) ||
            "";
        state.dateFrom = minDate;
        state.dateTo = getTodayDateStr();
        dateFromInput.value = state.dateFrom;
        dateToInput.value = state.dateTo;
        await loadTimeseries();
    });

    // Gruppierungs-Buttons (1, 5, 10, 20)
    document.querySelectorAll("#group-size-control .seg-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            document.querySelectorAll("#group-size-control .seg-btn").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            state.groupSize = parseInt(btn.getAttribute("data-size"), 10);
            await loadTimeseries();
        });
    });

    // Aggregations-Modus (Feste Blöcke vs Gleitender Schnitt)
    document.querySelectorAll("#group-mode-control .seg-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
            document.querySelectorAll("#group-mode-control .seg-btn").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            state.groupMode = btn.getAttribute("data-mode");
            await loadTimeseries();
        });
    });

    // X-Achsen-Modus (Spiel-korrekt vs Zeit-korrekt)
    document.querySelectorAll("#xaxis-mode-control .seg-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll("#xaxis-mode-control .seg-btn").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            state.xAxisMode = btn.getAttribute("data-xaxis");
            renderInsightsBanner();
            renderChartsGrid();
        });
    });

    // Favoriten-Picker Modal
    const favModal = document.getElementById("fav-picker-modal");
    document.getElementById("btn-open-fav-picker").addEventListener("click", () => {
        renderFavPickerModal();
        favModal.classList.remove("hidden");
    });
    document.getElementById("btn-close-fav-picker").addEventListener("click", () => {
        favModal.classList.add("hidden");
    });
    document.getElementById("btn-save-fav-picker").addEventListener("click", () => {
        favModal.classList.add("hidden");
    });

    document.getElementById("btn-fav-select-default").addEventListener("click", async () => {
        await updateFavoritesList(DEFAULT_FAVORITES_LIST);
        renderFavPickerModal();
    });
    document.getElementById("btn-fav-select-all").addEventListener("click", async () => {
        const allIds = state.statsConfig.map((s) => s.id);
        await updateFavoritesList(allIds);
        renderFavPickerModal();
    });
    document.getElementById("btn-fav-select-none").addEventListener("click", async () => {
        await updateFavoritesList([]);
        renderFavPickerModal();
    });

    // Sync / Demo Modal & Compare Button
    const syncModal = document.getElementById("sync-modal");
    document.getElementById("btn-open-sync").addEventListener("click", () => {
        syncModal.classList.remove("hidden");
    });
    const cmpBtn = document.getElementById("btn-open-compare");
    if (cmpBtn) {
        cmpBtn.addEventListener("click", async () => {
            state.activeTab = "compare";
            renderCategoryTabs();
            await loadCompareData();
            renderChartsGrid();
        });
    }
    document.getElementById("btn-close-sync").addEventListener("click", () => {
        syncModal.classList.add("hidden");
    });

    document.getElementById("sync-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        await handleRiotSync();
    });

    document.getElementById("btn-reload-demo").addEventListener("click", async () => {
        const msgEl = document.getElementById("sync-status-msg");
        msgEl.style.color = "#60a5fa";
        msgEl.textContent = "Erzeuge 80 Demo-Spiele...";
        const res = await fetch("/api/demo", { method: "POST" });
        if (res.ok) {
            msgEl.style.color = "#34d399";
            msgEl.textContent = "✓ 80 realistische Demo-Spiele wurden geladen!";
            await loadMeta();
            await loadTimeseries();
        }
    });

    document.getElementById("btn-clear-demo").addEventListener("click", async () => {
        const msgEl = document.getElementById("sync-status-msg");
        const res = await fetch("/api/demo", { method: "DELETE" });
        if (res.ok) {
            msgEl.style.color = "#fbbf24";
            msgEl.textContent = "Demo-Spiele wurden entfernt.";
            await loadMeta();
            await loadTimeseries();
        }
    });
}

async function loadMeta() {
    const res = await fetch("/api/meta");
    const data = await res.json();

    state.categories = data.categories || [];
    state.statsConfig = data.stats_config || [];
    state.statsById = {};
    state.statsConfig.forEach((s) => {
        state.statsById[s.id] = s;
    });
    state.favorites = data.favorites || [];
    state.filters = data.filters || { players: [], champions: [], roles: [], queues: [], counts: { total: 0 } };
    state.settings = data.settings || {};

    populatePlayerDropdown();
    populateFilterDropdowns();
    populateSettingsForm();
    renderCategoryTabs();
}

function populatePlayerDropdown() {
    const playerSelect = document.getElementById("filter-player");
    if (!playerSelect) return;

    const players = state.filters.players || [];
    playerSelect.innerHTML = "";

    if (players.length === 0) {
        playerSelect.innerHTML = `<option value="ALL">Keine Spieler geladen</option>`;
        state.selectedPuuid = "ALL";
        return;
    }

    // Falls noch kein Spieler ausgewählt ist oder der gewählte nicht mehr existiert -> nimm den 1. Spieler
    if (!state.selectedPuuid || (!players.some((p) => p.puuid === state.selectedPuuid) && state.selectedPuuid !== "ALL")) {
        state.selectedPuuid = players[0].puuid;
    }

    if (!state.comparePuuidA || !players.some((p) => p.puuid === state.comparePuuidA)) {
        state.comparePuuidA = players[0].puuid;
    }
    if (!state.comparePuuidB || !players.some((p) => p.puuid === state.comparePuuidB)) {
        state.comparePuuidB = players.length > 1 ? players[1].puuid : players[0].puuid;
    }

    players.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.puuid;
        const rankSuffix = p.rank_label ? ` • ${p.rank_label}` : "";
        opt.textContent = `${p.label}${rankSuffix} (${p.games} Sp. • ${p.winrate}% WR)`;
        playerSelect.appendChild(opt);
    });

    if (players.length > 1) {
        const allOpt = document.createElement("option");
        allOpt.value = "ALL";
        const totalGames = state.filters.counts ? state.filters.counts.total : 0;
        allOpt.textContent = `👥 Alle Spieler kombiniert (${totalGames} Spiele)`;
        playerSelect.appendChild(allOpt);
    }

    const addOpt = document.createElement("option");
    addOpt.value = "__ADD_NEW__";
    addOpt.textContent = "➕ Weiteren Spieler hinzufügen / Sync...";
    playerSelect.appendChild(addOpt);

    playerSelect.value = state.selectedPuuid;
}

function populateFilterDropdowns(customChampions = null) {
    const champSelect = document.getElementById("filter-champion");
    const currentChamp = state.selectedChampion;
    const champList = customChampions || state.filters.champions || [];
    const totalGames = champList.reduce((acc, c) => acc + (c.games || 0), 0);

    champSelect.innerHTML = `<option value="ALL">Alle Champions (${totalGames} Spiele)</option>`;
    champList.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.name;
        opt.textContent = `${c.name} (${c.games} Sp. • ${c.winrate}% WR)`;
        champSelect.appendChild(opt);
    });
    if ([...champSelect.options].some((o) => o.value === currentChamp)) {
        champSelect.value = currentChamp;
    } else {
        state.selectedChampion = "ALL";
    }
}

function populateSettingsForm() {
    if (state.settings.game_name) {
        document.getElementById("sync-gamename").value = state.settings.game_name;
    }
    if (state.settings.tag_line) {
        document.getElementById("sync-tagline").value = state.settings.tag_line;
    }
    if (state.settings.region) {
        document.getElementById("sync-region").value = state.settings.region;
    }
    if (state.settings.api_key) {
        document.getElementById("sync-apikey").value = state.settings.api_key;
    }
    if (state.settings.sync_count) {
        document.getElementById("sync-count").value = state.settings.sync_count;
    }
    if (state.settings.sync_queue) {
        document.getElementById("sync-queue").value = state.settings.sync_queue;
    }

    // Gespeicherte Spieler + Pro-Presets als Schnellauswahl-Chips im Sync-Modal anzeigen
    const bar = document.getElementById("saved-players-bar");
    if (bar) {
        const realPlayers = (state.filters.players || []).filter((p) => !p.is_demo && p.game_name);
        const chipsHtml = realPlayers
            .map(
                (p) =>
                    `<button type="button" class="saved-player-chip" data-gname="${p.game_name}" data-gtag="${p.tag_line}" data-greg="${p.region}" title="Klicken zum Ausfüllen (${p.games} gespeicherte Spiele)">👤 ${p.label} (${p.games})</button>`
            )
            .join("");

        const sineriasExists = realPlayers.some((p) => p.game_name.toLowerCase() === "sinerias");
        const presetHtml = !sineriasExists
            ? `<button type="button" class="saved-player-chip" style="border-color:rgba(245,158,11,0.55);color:#fde68a;background:rgba(245,158,11,0.15);" data-gname="Sinerias" data-gtag="EUW" data-greg="EUW1" title="Challenger Master Yi OTP (Sinerias#EUW) zum Vergleich eintragen">⭐ Pro-Preset: Sinerias#EUW</button>`
            : "";

        bar.innerHTML =
            `<span style="font-size:12px;color:var(--text-muted);font-weight:600;">Schnellauswahl:</span>` +
            chipsHtml +
            presetHtml;

        bar.querySelectorAll(".saved-player-chip").forEach((chip) => {
            chip.addEventListener("click", () => {
                document.getElementById("sync-gamename").value = chip.getAttribute("data-gname") || "";
                document.getElementById("sync-tagline").value = chip.getAttribute("data-gtag") || "";
                document.getElementById("sync-region").value = chip.getAttribute("data-greg") || "EUW1";
            });
        });
    }
}

function renderCategoryTabs() {
    const nav = document.getElementById("category-tabs");
    nav.innerHTML = "";

    const activeCount =
        state.timeseriesData && state.timeseriesData.total_matches !== undefined
            ? `${state.timeseriesData.total_matches} Sp.`
            : "Analyse";

    const tabs = [
        {
            id: "favorites",
            name: "Meine Favoriten",
            icon: "⭐",
            count: state.favorites.length,
            isFav: true,
        },
        {
            id: "winloss",
            name: "Sieg vs. Niederlage",
            icon: "🏆",
            count: activeCount,
            isWinLoss: true,
        },
        {
            id: "compare",
            name: "2 Spieler vergleichen",
            icon: "⚔️",
            count: "Pro / Kumpel",
            isCompare: true,
        },
        ...state.categories.map((c) => ({
            id: c.id,
            name: c.name,
            icon: c.icon,
            count: state.statsConfig.filter((s) => s.category === c.id).length,
            isFav: false,
        })),
        {
            id: "all",
            name: `Alle ${state.statsConfig.length} Stats`,
            icon: "📋",
            count: state.statsConfig.length,
            isFav: false,
        },
    ];

    tabs.forEach((t) => {
        const btn = document.createElement("button");
        btn.type = "button";
        const specialClass = t.isFav ? "fav-tab" : t.isWinLoss ? "winloss-tab" : t.isCompare ? "compare-tab" : "";
        btn.className = `tab-btn ${specialClass} ${state.activeTab === t.id ? "active" : ""}`;
        btn.innerHTML = `
            <span>${t.icon}</span>
            <span>${t.name}</span>
            <span class="tab-count">${t.count}</span>
        `;
        btn.addEventListener("click", async () => {
            state.activeTab = t.id;
            renderCategoryTabs();
            if (state.activeTab === "compare") {
                await loadCompareData();
            }
            renderChartsGrid();
        });
        nav.appendChild(btn);
    });

    const favBadge = document.getElementById("fav-count-badge");
    if (favBadge) favBadge.textContent = state.favorites.length;

    const favToolbar = document.getElementById("favorites-toolbar");
    if (favToolbar) {
        favToolbar.classList.toggle("hidden", state.activeTab !== "favorites");
    }
}

async function loadTimeseries() {
    const params = new URLSearchParams({
        puuid: state.selectedPuuid || "ALL",
        champion: state.selectedChampion,
        role: state.selectedRole,
        queue: state.selectedQueue,
        group_size: state.groupSize,
        mode: state.groupMode,
        range_mode: state.rangeMode,
    });

    if (state.rangeMode === "count" && state.limitGames && state.limitGames > 0) {
        params.set("limit_games", String(state.limitGames));
    } else if (state.rangeMode === "date") {
        if (state.dateFrom) params.set("date_from", state.dateFrom);
        if (state.dateTo) params.set("date_to", state.dateTo);
    }

    const res = await fetch(`/api/timeseries?${params.toString()}`);
    state.timeseriesData = await res.json();

    if (state.timeseriesData && state.timeseriesData.player_filters) {
        populateFilterDropdowns(state.timeseriesData.player_filters.champions);
    }

    if (state.activeTab === "compare") {
        await loadCompareData();
    }

    renderCategoryTabs();
    renderInsightsBanner();
    renderChartsGrid();
}

function renderInsightsBanner() {
    const data = state.timeseriesData;
    const matchesEl = document.getElementById("summary-matches-text");
    const modeEl = document.getElementById("summary-mode-text");
    const impEl = document.getElementById("top-improvements");
    const regEl = document.getElementById("top-regressions");
    const selBadge = document.getElementById("active-selection-badge");

    if (!data || data.total_matches === 0) {
        matchesEl.textContent = "0 Spiele gefunden";
        modeEl.textContent = "Bitte Filter/Zeitraum anpassen oder Spiele synchronisieren";
        impEl.innerHTML = `<span class="insight-sub">Keine Daten</span>`;
        regEl.innerHTML = `<span class="insight-sub">Keine Daten</span>`;
        if (selBadge) {
            const pCount = data && data.matching_pool_count ? data.matching_pool_count : 0;
            selBadge.textContent = `0 / ${pCount} Spiele`;
        }
        return;
    }

    const demoNote =
        state.filters.counts && state.filters.counts.demo > 0 && state.filters.counts.real === 0
            ? " (Demo-Daten)"
            : "";

    const poolCount = data.matching_pool_count ?? data.total_matches;
    const winsCnt = data.wins_count ?? 0;
    const lossesCnt = data.losses_count ?? 0;
    const ovWr = data.overall_winrate ?? 0;

    if (selBadge) {
        if (poolCount > data.total_matches) {
            selBadge.textContent = `🎯 ${data.total_matches} / ${poolCount} Sp. (${winsCnt}S–${lossesCnt}N • ${ovWr}% WR)`;
        } else {
            selBadge.textContent = `🎯 ${data.total_matches} Spiele (${winsCnt}S–${lossesCnt}N • ${ovWr}% WR)`;
        }
    }

    if (poolCount > data.total_matches) {
        matchesEl.textContent = `${data.total_matches} (von ${poolCount}) Spielen → ${data.bucket_count} Punkte${demoNote}`;
    } else {
        matchesEl.textContent = `${data.total_matches} Spiele (${winsCnt}S / ${lossesCnt}N) → ${data.bucket_count} Punkte${demoNote}`;
    }

    let rangeNote = "";
    if (state.rangeMode === "count" && state.limitGames) {
        rangeNote = `Letzte ${ data.total_matches } passende Sp. • `;
    } else if (state.rangeMode === "date") {
        const fromStr = state.dateFrom ? state.dateFrom.split("-").reverse().join(".") : "Anfang";
        const toStr = state.dateTo === getTodayDateStr() ? "Heute" : state.dateTo ? state.dateTo.split("-").reverse().join(".") : "Heute";
        rangeNote = `${fromStr} – ${toStr} • `;
    }

    if (state.groupSize === 1) {
        modeEl.textContent = `${rangeNote}Jedes einzelne Spiel als eigener Punkt`;
    } else if (state.groupMode === "blocks") {
        modeEl.textContent = `${rangeNote}Feste Blöcke à ${state.groupSize} Spiele`;
    } else {
        modeEl.textContent = `${rangeNote}Gleitender Schnitt über je ${state.groupSize} Spiele`;
    }

    // Berechne Top-Verbesserungen und Top-Baustellen über alle bewertbaren Stats
    const evaluated = [];
    for (const cfg of state.statsConfig) {
        if (cfg.higher_is_better === null) continue;
        const st = data.stats[cfg.id];
        if (!st || st.is_improving === null) continue;

        // Normierte Verbesserungsrate (positiv = besser, negativ = schlechter)
        const effectivePct = cfg.higher_is_better ? st.delta_pct : -st.delta_pct;
        evaluated.push({
            cfg,
            st,
            effectivePct,
        });
    }

    const improvements = evaluated
        .filter((x) => x.effectivePct > 1.0)
        .sort((a, b) => b.effectivePct - a.effectivePct)
        .slice(0, 4);

    const regressions = evaluated
        .filter((x) => x.effectivePct < -1.0)
        .sort((a, b) => a.effectivePct - b.effectivePct)
        .slice(0, 4);

    impEl.innerHTML =
        improvements.length > 0
            ? improvements
                  .map((item) => {
                      const sign = item.st.delta_pct > 0 ? "+" : "";
                      return `<span class="insight-chip pos" data-stat="${item.cfg.id}" title="Klicken zum Anzeigen">${item.cfg.name}: ${sign}${item.st.delta_pct}%</span>`;
                  })
                  .join("")
            : `<span class="insight-sub">Noch keine signifikanten Anstiege im gewählten Zeitraum.</span>`;

    regEl.innerHTML =
        regressions.length > 0
            ? regressions
                  .map((item) => {
                      const sign = item.st.delta_pct > 0 ? "+" : "";
                      return `<span class="insight-chip neg" data-stat="${item.cfg.id}" title="Klicken zum Anzeigen">${item.cfg.name}: ${sign}${item.st.delta_pct}%</span>`;
                  })
                  .join("")
            : `<span class="insight-sub">Stark! Aktuell keine deutlichen Verschlechterungen erkennbar.</span>`;

    // Klick auf Insight-Chip springt zum Stat oder fügt ihn zu Ansicht hinzu
    document.querySelectorAll(".insight-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
            const sid = chip.getAttribute("data-stat");
            const cfg = state.statsById[sid];
            if (!cfg) return;
            const existingCard = document.getElementById(`card-${sid}`);
            if (existingCard) {
                existingCard.scrollIntoView({ behavior: "smooth", block: "center" });
            } else {
                state.activeTab = cfg.category;
                renderCategoryTabs();
                renderChartsGrid();
                setTimeout(() => {
                    const card = document.getElementById(`card-${sid}`);
                    if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
                }, 60);
            }
        });
    });
}

function getVisibleStatConfigs() {
    if (state.activeTab === "favorites") {
        return state.favorites.map((sid) => state.statsById[sid]).filter(Boolean);
    }
    if (state.activeTab === "all") {
        return state.statsConfig;
    }
    return state.statsConfig.filter((s) => s.category === state.activeTab);
}

function destroyAllCharts() {
    Object.values(state.chartInstances).forEach((ch) => {
        try {
            ch.destroy();
        } catch (_) {}
    });
    state.chartInstances = {};
}

const TIER_NAMES_DE = ["Iron", "Bronze", "Silver", "Gold", "Platin", "Emerald", "Diamond"];
const DIV_NAMES_DE = ["IV", "III", "II", "I"];

function formatRankLp(val, isDiff = false, short = false) {
    if (val === undefined || val === null || Number.isNaN(Number(val))) return "-";
    const num = Math.round(Number(val));
    if (isDiff) {
        return `${num} LP`;
    }
    if (num >= 2800) {
        const apexLp = num - 2800;
        if (apexLp >= 1000) return short ? `Chall. ${apexLp} LP` : `Challenger (${apexLp} LP)`;
        if (apexLp >= 500) return short ? `GM ${apexLp} LP` : `Grandmaster (${apexLp} LP)`;
        return short ? `Master ${apexLp} LP` : `Master (${apexLp} LP)`;
    }
    const clamped = Math.max(0, num);
    const tierIdx = Math.min(6, Math.floor(clamped / 400));
    const rem = clamped % 400;
    const divIdx = Math.min(3, Math.floor(rem / 100));
    const lp = rem % 100;
    const tName = TIER_NAMES_DE[tierIdx];
    const dName = DIV_NAMES_DE[divIdx];
    return short ? `${tName} ${dName} ${lp} LP` : `${tName} ${dName} (${lp} LP)`;
}

function formatMmSs(val, unit = "", isDiff = false) {
    if (val === undefined || val === null || Number.isNaN(Number(val))) return "-";
    const num = Number(val);
    if (!isDiff && Math.abs(num) < 0.01) return "-";
    const sign = num < -1e-6 ? "-" : "";
    const totalSecs = Math.round(Math.abs(num) * 60);
    const mins = Math.floor(totalSecs / 60);
    const secs = totalSecs % 60;
    return `${sign}${mins}:${String(secs).padStart(2, "0")}${unit}`;
}

function formatStatValue(val, cfg, isDiff = false) {
    if (val === undefined || val === null || Number.isNaN(Number(val))) return "-";
    if (cfg && cfg.format === "mm_ss") {
        return formatMmSs(val, cfg.unit || "", isDiff);
    }
    if (cfg && cfg.format === "rank_lp") {
        return formatRankLp(val, isDiff, false);
    }
    const formatted = Number(val).toFixed(cfg.decimals);
    return `${formatted}${cfg.unit}`;
}

function renderChartsGrid() {
    destroyAllCharts();
    const grid = document.getElementById("charts-grid");
    const wlView = document.getElementById("winloss-view");
    const cmpView = document.getElementById("compare-view");
    grid.innerHTML = "";

    if (state.activeTab === "winloss") {
        grid.classList.add("hidden");
        if (cmpView) cmpView.classList.add("hidden");
        if (wlView) {
            wlView.classList.remove("hidden");
            renderWinLossView(wlView);
        }
        return;
    } else if (state.activeTab === "compare") {
        grid.classList.add("hidden");
        if (wlView) wlView.classList.add("hidden");
        if (cmpView) {
            cmpView.classList.remove("hidden");
            renderCompareView(cmpView);
        }
        return;
    } else {
        grid.classList.remove("hidden");
        if (wlView) wlView.classList.add("hidden");
        if (cmpView) cmpView.classList.add("hidden");
    }

    const data = state.timeseriesData;
    if (!data || data.total_matches === 0) {
        grid.innerHTML = `
            <div class="empty-state">
                <h3>Keine Spiele für diese Filter-Auswahl vorhanden</h3>
                <p>Wähle oben „Alle Champions / Alle Rollen“ oder lade Spiele über den Button „Riot Sync / Daten“.</p>
            </div>
        `;
        return;
    }

    const visibleConfigs = getVisibleStatConfigs();
    if (visibleConfigs.length === 0 && state.activeTab === "favorites") {
        grid.innerHTML = `
            <div class="empty-state">
                <h3>Deine Favoriten-Seite ist noch leer</h3>
                <p>Klicke oben rechts auf <strong>„➕ Stats auswählen / verwalten“</strong> oder markiere beliebige Statistiken in den anderen Tabs mit dem Stern <strong>★</strong>.</p>
            </div>
        `;
        return;
    }

    visibleConfigs.forEach((cfg, idx) => {
        const st = data.stats[cfg.id];
        if (!st) return;

        const isFav = state.favorites.includes(cfg.id);
        const card = document.createElement("article");
        card.className = "stat-card";
        card.id = `card-${cfg.id}`;

        // Trend-Badge Formatierung
        let badgeClass = "neutral";
        let badgeText = "±0.0% Stabil";
        if (cfg.format === "rank_lp") {
            const lpDiff = Math.round(st.delta_abs || 0);
            const sign = lpDiff > 0 ? "+" : "";
            if (st.is_improving === true) {
                badgeClass = "positive";
                badgeText = `▲ ${sign}${lpDiff} LP (Aufgestiegen)`;
            } else if (st.is_improving === false) {
                badgeClass = "negative";
                badgeText = `▼ ${sign}${lpDiff} LP (Abgestiegen)`;
            } else {
                badgeText = `±0 LP Stabil`;
            }
        } else if (st.is_improving === true) {
            badgeClass = "positive";
            const arrow = st.delta_abs >= 0 ? "▲" : "▼";
            const sign = st.delta_pct > 0 ? "+" : "";
            badgeText = `${arrow} ${sign}${st.delta_pct}% (Verbessert)`;
        } else if (st.is_improving === false) {
            badgeClass = "negative";
            const arrow = st.delta_abs >= 0 ? "▲" : "▼";
            const sign = st.delta_pct > 0 ? "+" : "";
            badgeText = `${arrow} ${sign}${st.delta_pct}% (Schlechter)`;
        } else if (Math.abs(st.delta_pct) > 0.1) {
            const sign = st.delta_pct > 0 ? "+" : "";
            badgeText = `${sign}${st.delta_pct}%`;
        }

        const recentLabel =
            state.groupSize === 1
                ? "Letztes Spiel"
                : `Aktuell (Letzte ${state.groupSize} Sp.)`;

        // Sortier-Buttons nur auf der Favoriten-Seite
        const reorderButtonsHtml =
            state.activeTab === "favorites"
                ? `
                <button type="button" class="icon-btn btn-move-left" title="Nach links verschieben" ${idx === 0 ? "disabled" : ""}>◀</button>
                <button type="button" class="icon-btn btn-move-right" title="Nach rechts verschieben" ${idx === visibleConfigs.length - 1 ? "disabled" : ""}>▶</button>
            `
                : "";

        card.innerHTML = `
            <div class="card-header">
                <div class="card-title-wrap">
                    <div class="card-title">
                        <span>${cfg.name}</span>
                    </div>
                    <div class="card-desc" title="Riot API Quelle: ${cfg.riot_source}">${cfg.description}</div>
                </div>
                <div class="card-actions">
                    ${reorderButtonsHtml}
                    <button type="button" class="icon-btn btn-toggle-fav ${isFav ? "fav-active" : ""}" title="${isFav ? "Aus Favoriten entfernen" : "Zu Favoriten hinzufügen"}">
                        ★
                    </button>
                </div>
            </div>

            <div class="card-kpis">
                <div class="kpi-item">
                    <span class="kpi-label">${recentLabel}</span>
                    <span class="kpi-value">${formatStatValue(st.recent_val, cfg)}</span>
                </div>
                <div class="kpi-item">
                    <span class="kpi-label">Gesamt-Ø (${data.total_matches} Sp.)</span>
                    <span class="kpi-value secondary">${formatStatValue(st.overall_avg, cfg)}</span>
                </div>
                <div class="kpi-item">
                    <span class="kpi-label">Entwicklung / Trend</span>
                    <span class="trend-badge ${badgeClass}">${badgeText}</span>
                </div>
            </div>

            <div class="chart-container">
                <canvas id="canvas-${cfg.id}"></canvas>
            </div>
        `;

        // Event: Favorit umschalten
        card.querySelector(".btn-toggle-fav").addEventListener("click", async () => {
            await handleToggleFavorite(cfg.id);
        });

        // Events: Favoriten-Reihenfolge ändern
        if (state.activeTab === "favorites") {
            const leftBtn = card.querySelector(".btn-move-left");
            const rightBtn = card.querySelector(".btn-move-right");
            if (leftBtn) {
                leftBtn.addEventListener("click", async () => {
                    await moveFavorite(cfg.id, -1);
                });
            }
            if (rightBtn) {
                rightBtn.addEventListener("click", async () => {
                    await moveFavorite(cfg.id, 1);
                });
            }
        }

        grid.appendChild(card);
        createChartInstance(cfg, st, data.buckets_meta);
    });
}

function hexToRgba(hex, alpha) {
    const clean = hex.replace("#", "");
    const bigint = parseInt(clean, 16);
    const r = (bigint >> 16) & 255;
    const g = (bigint >> 8) & 255;
    const b = bigint & 255;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function computeTimeLinearRegression(xs, ys, decimals) {
    const n = xs.length;
    if (n <= 1) return ys.slice();
    const x0 = xs[0];
    const normX = xs.map((x) => (x - x0) / 3600000.0); // Stunden seit erstem Punkt
    let sumX = 0,
        sumY = 0,
        sumXY = 0,
        sumX2 = 0;
    for (let i = 0; i < n; i++) {
        sumX += normX[i];
        sumY += ys[i];
        sumXY += normX[i] * ys[i];
        sumX2 += normX[i] * normX[i];
    }
    const denom = n * sumX2 - sumX * sumX;
    if (Math.abs(denom) < 1e-9) return ys.slice();
    const slope = (n * sumXY - sumX * sumY) / denom;
    const intercept = (sumY - slope * sumX) / n;
    return normX.map((nx) => Number((slope * nx + intercept).toFixed(decimals)));
}

function createChartInstance(cfg, statSeries, bucketsMeta) {
    const canvas = document.getElementById(`canvas-${cfg.id}`);
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const isTimeAxis = state.xAxisMode === "time" && bucketsMeta.length > 0 && bucketsMeta[0].timestamp_ms;
    const labels = bucketsMeta.map((b) => b.label);
    const values = statSeries.values;

    const timestamps = bucketsMeta.map((b) => b.timestamp_ms);
    const trendValues = isTimeAxis
        ? computeTimeLinearRegression(timestamps, values, cfg.decimals)
        : statSeries.trend_line;
    const avgLine = values.map(() => statSeries.overall_avg);

    const mainData = isTimeAxis
        ? values.map((v, i) => ({ x: timestamps[i], y: v }))
        : values;
    const trendData = isTimeAxis
        ? trendValues.map((v, i) => ({ x: timestamps[i], y: v }))
        : trendValues;
    const avgData = isTimeAxis
        ? avgLine.map((v, i) => ({ x: timestamps[i], y: v }))
        : avgLine;

    const gradient = ctx.createLinearGradient(0, 0, 0, 210);
    gradient.addColorStop(0, hexToRgba(cfg.color, 0.35));
    gradient.addColorStop(1, hexToRgba(cfg.color, 0.01));

    const pointRadius = values.length > 35 ? 2.2 : values.length > 18 ? 3.5 : 4.5;

    const xScaleConfig = isTimeAxis
        ? {
              type: "linear",
              min: Math.min(...timestamps),
              max: Math.max(...timestamps),
              grid: {
                  color: "rgba(51, 65, 85, 0.22)",
              },
              ticks: {
                  color: "#94a3b8",
                  font: { size: 11 },
                  maxRotation: 0,
                  autoSkip: true,
                  maxTicksLimit: 7,
                  callback: (val) => {
                      const d = new Date(val);
                      return d.toLocaleDateString("de-DE", {
                          day: "2-digit",
                          month: "2-digit",
                          year: "2-digit",
                      });
                  },
              },
          }
        : {
              grid: {
                  color: "rgba(51, 65, 85, 0.22)",
              },
              ticks: {
                  color: "#94a3b8",
                  font: { size: 11 },
                  maxRotation: 0,
                  autoSkip: true,
                  maxTicksLimit: 8,
              },
          };

    const chart = new Chart(ctx, {
        type: "line",
        data: {
            labels: isTimeAxis ? undefined : labels,
            datasets: [
                {
                    label: cfg.name,
                    data: mainData,
                    borderColor: cfg.color,
                    backgroundColor: gradient,
                    borderWidth: 2.5,
                    pointBackgroundColor: cfg.color,
                    pointBorderColor: "#0b0f19",
                    pointBorderWidth: 1.5,
                    pointRadius,
                    pointHoverRadius: 6,
                    tension: isTimeAxis ? 0.12 : 0.28,
                    fill: true,
                    order: 1,
                },
                {
                    label: "Trendlinie",
                    data: trendData,
                    borderColor: statSeries.is_improving === true
                        ? "rgba(52, 211, 153, 0.85)"
                        : statSeries.is_improving === false
                        ? "rgba(251, 113, 133, 0.85)"
                        : "rgba(226, 232, 240, 0.55)",
                    borderWidth: 1.8,
                    borderDash: [6, 5],
                    pointRadius: 0,
                    pointHoverRadius: 0,
                    fill: false,
                    tension: 0,
                    order: 2,
                },
                {
                    label: "Gesamt-Ø",
                    data: avgData,
                    borderColor: "rgba(148, 163, 184, 0.32)",
                    borderWidth: 1.2,
                    borderDash: [2, 4],
                    pointRadius: 0,
                    pointHoverRadius: 0,
                    fill: false,
                    order: 3,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: "index",
                intersect: false,
            },
            plugins: {
                legend: {
                    display: false,
                },
                tooltip: {
                    backgroundColor: "rgba(15, 23, 42, 0.96)",
                    titleColor: "#f8fafc",
                    bodyColor: "#e2e8f0",
                    borderColor: "#334155",
                    borderWidth: 1,
                    padding: 10,
                    callbacks: {
                        title: (items) => {
                            if (!items.length) return "";
                            const idx = items[0].dataIndex;
                            const meta = bucketsMeta[idx];
                            return meta ? meta.range_label : "";
                        },
                        label: (item) => {
                            const yVal = item.parsed ? item.parsed.y : item.raw;
                            if (item.datasetIndex === 0) {
                                return ` ${cfg.name}: ${formatStatValue(yVal, cfg)}`;
                            }
                            if (item.datasetIndex === 1) {
                                return ` Langzeit-Trend: ${formatStatValue(yVal, cfg)}`;
                            }
                            return ` Gesamt-Ø: ${formatStatValue(yVal, cfg)}`;
                        },
                        afterBody: (items) => {
                            if (!items.length) return [];
                            const idx = items[0].dataIndex;
                            const meta = bucketsMeta[idx];
                            if (!meta) return [];
                            return [
                                "",
                                `Spiele im Punkt: ${meta.games_count} (${meta.winrate}% Winrate)`,
                                `Champions: ${meta.champions}`,
                            ];
                        },
                    },
                },
            },
            scales: {
                x: xScaleConfig,
                y: {
                    grid: {
                        color: "rgba(51, 65, 85, 0.28)",
                    },
                    ticks: {
                        color: "#94a3b8",
                        font: { size: 11 },
                        callback: (val) => {
                            if (cfg.format === "mm_ss") {
                                return formatMmSs(val, "", true);
                            }
                            if (cfg.format === "rank_lp") {
                                return formatRankLp(val, false, true);
                            }
                            return val;
                        },
                    },
                },
            },
        },
    });

    state.chartInstances[cfg.id] = chart;
}

async function handleToggleFavorite(statId) {
    const res = await fetch("/api/favorites/toggle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stat_id: statId }),
    });
    if (res.ok) {
        const data = await res.json();
        state.favorites = data.favorites || [];
        renderCategoryTabs();
        renderChartsGrid();
    }
}

async function updateFavoritesList(statIds) {
    const res = await fetch("/api/favorites", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stat_ids: statIds }),
    });
    if (res.ok) {
        const data = await res.json();
        state.favorites = data.favorites || [];
        renderCategoryTabs();
        renderChartsGrid();
    }
}

async function moveFavorite(statId, direction) {
    const idx = state.favorites.indexOf(statId);
    if (idx === -1) return;
    const targetIdx = idx + direction;
    if (targetIdx < 0 || targetIdx >= state.favorites.length) return;

    const newFavs = [...state.favorites];
    const [removed] = newFavs.splice(idx, 1);
    newFavs.splice(targetIdx, 0, removed);
    await updateFavoritesList(newFavs);
}

function renderFavPickerModal() {
    const container = document.getElementById("fav-picker-categories");
    container.innerHTML = "";

    state.categories.forEach((cat) => {
        const box = document.createElement("div");
        box.className = "fav-cat-box";
        box.innerHTML = `<h4>${cat.icon} ${cat.name}</h4>`;

        const catStats = state.statsConfig.filter((s) => s.category === cat.id);
        catStats.forEach((st) => {
            const checked = state.favorites.includes(st.id);
            const label = document.createElement("label");
            label.className = "fav-check-item";
            label.innerHTML = `
                <input type="checkbox" value="${st.id}" ${checked ? "checked" : ""} />
                <span>${st.name}</span>
            `;
            label.querySelector("input").addEventListener("change", async () => {
                await handleToggleFavorite(st.id);
            });
            box.appendChild(label);
        });

        container.appendChild(box);
    });
}

async function handleRiotSync() {
    const gameName = document.getElementById("sync-gamename").value.trim();
    const tagLine = document.getElementById("sync-tagline").value.trim();
    const region = document.getElementById("sync-region").value;
    const apiKey = document.getElementById("sync-apikey").value.trim();
    const count = Math.max(1, parseInt(document.getElementById("sync-count").value, 10) || 50);
    const queue = document.getElementById("sync-queue").value;
    const removeDemo = document.getElementById("sync-remove-demo").checked;

    const msgEl = document.getElementById("sync-status-msg");
    const submitBtn = document.getElementById("btn-submit-sync");

    msgEl.style.color = "#60a5fa";
    msgEl.textContent = `Verbinde mit Riot API und lade bis zu ${count} Spiele (bei >50 Spielen wird das Rate-Limit automatisch abgewartet)...`;
    submitBtn.disabled = true;

    try {
        const res = await fetch("/api/sync", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                game_name: gameName,
                tag_line: tagLine,
                region,
                api_key: apiKey,
                count,
                queue,
                remove_demo: removeDemo,
            }),
        });

        const data = await res.json();
        if (!res.ok) {
            msgEl.style.color = "#fb7185";
            msgEl.textContent = `Fehler: ${data.detail || "Synchronisation fehlgeschlagen"}`;
        } else {
            msgEl.style.color = "#34d399";
            const labelInfo = data.player_label ? ` für ${data.player_label}` : "";
            msgEl.textContent = `✓ Erfolgreich${labelInfo}! ${data.imported} neue Spiele importiert (${data.skipped} waren bereits für diesen Spieler gespeichert).`;
            if (data.puuid) {
                if (state.activeTab === "compare") {
                    state.comparePuuidB = data.puuid;
                } else {
                    state.selectedPuuid = data.puuid;
                    state.selectedChampion = "ALL";
                }
            }
            await loadMeta();
            await loadTimeseries();
        }
    } catch (err) {
        msgEl.style.color = "#fb7185";
        msgEl.textContent = `Netzwerkfehler: ${err.message}`;
    } finally {
        submitBtn.disabled = false;
    }
}

function renderWinLossView(container) {
    container.innerHTML = "";
    const data = state.timeseriesData;
    const wl = data ? data.win_loss_analysis : null;

    if (!data || !wl || data.total_matches === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <h3>Keine Spiele für diese Filter-Auswahl vorhanden</h3>
                <p>Bitte passe oben Champion, Rolle, Queue oder den Zeitraum / die Spielanzahl an.</p>
            </div>
        `;
        return;
    }

    const sum = wl.summary;
    const items = wl.items || [];

    if (sum.wins_count === 0 || sum.losses_count === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <h3>Für den Sieg/Niederlage-Vergleich werden mindestens 1 Sieg und 1 Niederlage benötigt</h3>
                <p>Aktuelle Auswahl: ${sum.total_matches} Spiele (${sum.wins_count} Siege, ${sum.losses_count} Niederlagen). Bitte vergrößere oben die Spielanzahl oder den Zeitraum.</p>
            </div>
        `;
        return;
    }

    // 1. Top-Treiber bei Siegen (wo höhere Werte gut sind und bei Siegen stark ansteigen)
    const topWinDrivers = items
        .filter((it) => it.higher_is_better === true && it.effect_size_d > 0.25 && it.category !== "scores")
        .sort((a, b) => b.effect_size_d - a.effect_size_d)
        .slice(0, 5);

    // 2. Kritische Faktoren bei Niederlagen (z.B. Tode steigen stark an oder Schlüsselwerte brechen ein + hoher Winrate-Swing)
    const topLossFactors = items
        .filter((it) => it.higher_is_better !== null && it.category !== "scores" && it.winrate_swing > 8)
        .sort((a, b) => b.winrate_swing - a.winrate_swing)
        .slice(0, 5);

    // 3. Konstante Basis-Werte (wo zwischen Sieg und Niederlage kaum ein Unterschied besteht)
    const constantStats = items
        .filter((it) => it.category !== "scores" && it.id !== "ally_heal_shield_per_min" && (it.win_avg > 0 || it.loss_avg > 0))
        .sort((a, b) => a.abs_effect - b.abs_effect)
        .slice(0, 5);

    const poolInfo =
        data.matching_pool_count && data.matching_pool_count > sum.total_matches
            ? ` (aus ${data.matching_pool_count} passenden Spielen)`
            : "";

    // Sortierung und Kategorien-Filterung für die Detail-Karten
    let filteredItems = items.slice();
    if (state.wlCategoryFilter !== "ALL") {
        filteredItems = filteredItems.filter((it) => it.category === state.wlCategoryFilter);
    }

    if (state.wlSortMode === "swing") {
        filteredItems.sort((a, b) => Math.abs(b.winrate_swing) - Math.abs(a.winrate_swing));
    } else if (state.wlSortMode === "pct") {
        filteredItems.sort((a, b) => Math.abs(b.diff_pct) - Math.abs(a.diff_pct));
    } else {
        filteredItems.sort((a, b) => b.abs_effect - a.abs_effect);
    }

    const catOptionsHtml = [
        `<option value="ALL" ${state.wlCategoryFilter === "ALL" ? "selected" : ""}>Alle Kategorien (${items.length} Stats)</option>`,
        ...state.categories.map(
            (c) =>
                `<option value="${c.id}" ${state.wlCategoryFilter === c.id ? "selected" : ""}>${c.icon} ${c.name}</option>`
        ),
    ].join("");

    container.innerHTML = `
        <!-- Top Bilanz-Banner der aktuellen Auswahl -->
        <div class="wl-top-banner">
            <div class="wl-banner-left">
                <h2>🏆 Sieg vs. Niederlage – Was entscheidet deine Spiele?</h2>
                <p>
                    Dynamische Analyse über deine aktuell oben ausgewählten <strong>${sum.total_matches} Spiele</strong>${poolInfo}.
                    Zeigt dir, welche Werte bei Siegen herausstechen, was bei Niederlagen einbricht und ab welchem Schwellenwert deine Winrate kippt.
                </p>
            </div>
            <div class="wl-kpis-row">
                <div class="wl-kpi-pill">
                    <span class="pill-label">Ausgewählte Spiele</span>
                    <span class="pill-val">${sum.total_matches} (${sum.winrate}% WR)</span>
                </div>
                <div class="wl-kpi-pill win-pill">
                    <span class="pill-label">🟢 Siege (Wins)</span>
                    <span class="pill-val">${sum.wins_count} Spiele (Ø ${sum.avg_duration_win}m)</span>
                </div>
                <div class="wl-kpi-pill loss-pill">
                    <span class="pill-label">🔴 Niederlagen (Losses)</span>
                    <span class="pill-val">${sum.losses_count} Spiele (Ø ${sum.avg_duration_loss}m)</span>
                </div>
            </div>
        </div>

        <!-- 3 Executive Summary Boxen -->
        <div class="wl-highlights-grid">
            <div class="wl-highlight-card win-focus">
                <h3>🟢 Deine stärksten Sieg-Treiber</h3>
                <div class="wl-highlight-sub">Diese Werte sind besonders hoch, wenn du ein Spiel gewinnst:</div>
                <div class="wl-highlight-list">
                    ${topWinDrivers
                        .map((it) => {
                            const sign = it.diff_pct > 0 ? "+" : "";
                            const badgeStr = it.is_diff_metric
                                ? `Δ ${it.diff_abs > 0 ? "+" : ""}${formatStatValue(it.diff_abs, it)}`
                                : `${sign}${it.diff_pct}% bei Sieg`;
                            return `
                                <div class="wl-hl-item" data-wl-stat="${it.id}">
                                    <div>
                                        <div class="wl-hl-name">${it.name}</div>
                                        <div class="wl-hl-detail">🟢 Sieg: <strong>${formatStatValue(it.win_avg, it)}</strong> vs. 🔴 Loss: <strong>${formatStatValue(it.loss_avg, it)}</strong></div>
                                    </div>
                                    <span class="wl-hl-badge win">${badgeStr}</span>
                                </div>
                            `;
                        })
                        .join("")}
                </div>
            </div>

            <div class="wl-highlight-card loss-focus">
                <h3>💀 Kritische Kipppunkte (Sieg vs. Loss)</h3>
                <div class="wl-highlight-sub">Wenn du diesen Richtwert verfehlst, sinkt deine Winrate am stärksten:</div>
                <div class="wl-highlight-list">
                    ${topLossFactors
                        .map((it) => {
                            return `
                                <div class="wl-hl-item" data-wl-stat="${it.id}">
                                    <div>
                                        <div class="wl-hl-name">${it.name} (${it.threshold_good_label} vs. ${it.threshold_bad_label})</div>
                                        <div class="wl-hl-detail">🟢 Erreicht: <strong>${it.good_winrate}% WR</strong> • 🔴 Verfehlt: <strong>${it.bad_winrate}% WR</strong></div>
                                    </div>
                                    <span class="wl-hl-badge loss">-${it.winrate_swing}% WR</span>
                                </div>
                            `;
                        })
                        .join("")}
                </div>
            </div>

            <div class="wl-highlight-card neutral-focus">
                <h3>⚖️ Konstante Basis-Werte (Stabil)</h3>
                <div class="wl-highlight-sub">Diese Stats sind bei dir in Siegen und Niederlagen fast identisch:</div>
                <div class="wl-highlight-list">
                    ${constantStats
                        .map((it) => {
                            return `
                                <div class="wl-hl-item" data-wl-stat="${it.id}">
                                    <div>
                                        <div class="wl-hl-name">${it.name}</div>
                                        <div class="wl-hl-detail">🟢 Sieg: <strong>${formatStatValue(it.win_avg, it)}</strong> vs. 🔴 Loss: <strong>${formatStatValue(it.loss_avg, it)}</strong></div>
                                    </div>
                                    <span class="wl-hl-badge const">Konstant</span>
                                </div>
                            `;
                        })
                        .join("")}
                </div>
            </div>
        </div>

        <!-- Sortier- & Filterleiste für alle Detail-Karten -->
        <div class="wl-controls-row">
            <div class="wl-controls-left">
                <span>Sortieren nach:</span>
                <div class="segmented-control" id="wl-sort-control">
                    <button type="button" data-wlsort="impact" class="seg-btn ${state.wlSortMode === "impact" ? "active" : ""}">
                        🔥 Höchster Einfluss (Trennschärfe)
                    </button>
                    <button type="button" data-wlsort="swing" class="seg-btn ${state.wlSortMode === "swing" ? "active" : ""}">
                        🎯 Größter Winrate-Kipppunkt
                    </button>
                    <button type="button" data-wlsort="pct" class="seg-btn ${state.wlSortMode === "pct" ? "active" : ""}">
                        📈 Größter %-Unterschied
                    </button>
                </div>
            </div>
            <div class="wl-controls-right">
                <select id="wl-cat-filter" class="select-input">
                    ${catOptionsHtml}
                </select>
            </div>
        </div>

        <!-- Grid mit allen Sieg-vs-Niederlage Detail-Karten -->
        <div class="wl-cards-grid" id="wl-cards-grid"></div>
    `;

    // Bind Sort & Filter Events
    container.querySelectorAll("#wl-sort-control .seg-btn").forEach((btn) => {
        btn.addEventListener("click", () => {
            state.wlSortMode = btn.getAttribute("data-wlsort");
            renderWinLossView(container);
        });
    });

    container.querySelector("#wl-cat-filter").addEventListener("change", (e) => {
        state.wlCategoryFilter = e.target.value;
        renderWinLossView(container);
    });

    // Render Individual Stat Comparison Cards
    const cardsGrid = container.querySelector("#wl-cards-grid");
    filteredItems.forEach((it) => {
        const card = document.createElement("article");
        card.className = "wl-stat-card";
        card.id = `wl-card-${it.id}`;

        // Berechne Balkenbreiten (auch für Differenz-Werte, die negativ sein können)
        let winWidth = 50;
        let lossWidth = 50;
        if (it.win_avg < 0 || it.loss_avg < 0) {
            const minVal = Math.min(it.win_avg, it.loss_avg, 0);
            const maxVal = Math.max(it.win_avg, it.loss_avg, 1);
            const span = Math.max(1, maxVal - minVal);
            winWidth = Math.max(8, Math.min(100, Math.round(((it.win_avg - minVal) / span) * 90 + 10)));
            lossWidth = Math.max(8, Math.min(100, Math.round(((it.loss_avg - minVal) / span) * 90 + 10)));
        } else {
            const maxAbs = Math.max(it.win_avg, it.loss_avg, 0.001);
            winWidth = Math.max(6, Math.min(100, Math.round((it.win_avg / maxAbs) * 100)));
            lossWidth = Math.max(6, Math.min(100, Math.round((it.loss_avg / maxAbs) * 100)));
        }

        const diffSign = it.diff_abs > 0 ? "+" : "";
        const pctSign = it.diff_pct > 0 ? "+" : "";
        const diffSummary = it.is_diff_metric
            ? `${diffSign}${formatStatValue(it.diff_abs, it, true)} bei Sieg`
            : `${diffSign}${formatStatValue(it.diff_abs, it, true)} (${pctSign}${it.diff_pct}% bei Sieg)`;

        const swingSign = it.winrate_swing > 0 ? "+" : "";

        card.innerHTML = `
            <div class="card-header">
                <div class="card-title-wrap">
                    <div class="card-title">
                        <span>${it.name}</span>
                    </div>
                    <div class="card-desc">${it.description}</div>
                </div>
                <span class="wl-impact-badge ${it.impact_level}" title="Statistische Effektstärke |d| = ${it.abs_effect}">
                    ${it.impact_label}
                </span>
            </div>

            <div class="wl-bars-box">
                <div class="wl-bar-row">
                    <div class="wl-bar-header">
                        <span class="win-label">🟢 Ø bei Sieg (${sum.wins_count} Sp.)</span>
                        <span class="win-label">${formatStatValue(it.win_avg, it)}</span>
                    </div>
                    <div class="wl-bar-track">
                        <div class="wl-bar-fill win-fill" style="width: ${winWidth}%"></div>
                    </div>
                </div>

                <div class="wl-bar-row">
                    <div class="wl-bar-header">
                        <span class="loss-label">🔴 Ø bei Niederlage (${sum.losses_count} Sp.)</span>
                        <span class="loss-label">${formatStatValue(it.loss_avg, it)}</span>
                    </div>
                    <div class="wl-bar-track">
                        <div class="wl-bar-fill loss-fill" style="width: ${lossWidth}%"></div>
                    </div>
                </div>

                <div class="wl-diff-footer">
                    <span>Gesamt-Ø: <strong>${formatStatValue(it.overall_avg, it)}</strong></span>
                    <span>Unterschied: <strong>${diffSummary}</strong></span>
                </div>
            </div>

            <div class="wl-sweetspot-box">
                <div class="wl-sweetspot-title">
                    <span>🎯 Persönlicher Winrate-Kipppunkt (Richtwert: ${it.threshold_good_label})</span>
                    <span style="color: ${it.winrate_swing >= 0 ? "#34d399" : "#fb7185"}">${swingSign}${it.winrate_swing}% Winrate</span>
                </div>
                <div class="wl-sweetspot-grid">
                    <div class="wl-sweet-item">
                        <span class="wl-sweet-cond">Wenn ${it.threshold_good_label} (${it.good_games} Sp.)</span>
                        <span class="wl-sweet-wr good">${it.good_winrate}% Winrate</span>
                    </div>
                    <div class="wl-sweet-item">
                        <span class="wl-sweet-cond">Wenn ${it.threshold_bad_label} (${it.bad_games} Sp.)</span>
                        <span class="wl-sweet-wr bad">${it.bad_winrate}% Winrate</span>
                    </div>
                </div>
            </div>
        `;

        cardsGrid.appendChild(card);
    });

    // Klick auf Highlight-Eintrag oben scrollt direkt zur Detail-Karte
    container.querySelectorAll(".wl-hl-item").forEach((el) => {
        el.addEventListener("click", () => {
            const sid = el.getAttribute("data-wl-stat");
            const targetCard = document.getElementById(`wl-card-${sid}`);
            if (targetCard) {
                targetCard.scrollIntoView({ behavior: "smooth", block: "center" });
            } else {
                state.wlCategoryFilter = "ALL";
                renderWinLossView(container);
                setTimeout(() => {
                    const c = document.getElementById(`wl-card-${sid}`);
                    if (c) c.scrollIntoView({ behavior: "smooth", block: "center" });
                }, 50);
            }
        });
    });
}

async function loadCompareData() {
    const players = state.filters.players || [];
    if (players.length > 0) {
        if (!state.comparePuuidA || !players.some((p) => p.puuid === state.comparePuuidA)) {
            state.comparePuuidA = state.selectedPuuid && state.selectedPuuid !== "ALL" ? state.selectedPuuid : players[0].puuid;
        }
        if (!state.comparePuuidB || !players.some((p) => p.puuid === state.comparePuuidB)) {
            const second = players.find((p) => p.puuid !== state.comparePuuidA);
            state.comparePuuidB = second ? second.puuid : players[0].puuid;
        }
    }

    if (!state.comparePuuidA || !state.comparePuuidB) {
        state.compareData = null;
        return;
    }

    const params = new URLSearchParams({
        puuid_a: state.comparePuuidA,
        puuid_b: state.comparePuuidB,
        champion: state.selectedChampion,
        role: state.selectedRole,
        queue: state.selectedQueue,
        group_size: state.groupSize,
        mode: state.groupMode,
        range_mode: state.rangeMode,
    });

    if (state.rangeMode === "count" && state.limitGames && state.limitGames > 0) {
        params.set("limit_games", String(state.limitGames));
    } else if (state.rangeMode === "date") {
        if (state.dateFrom) params.set("date_from", state.dateFrom);
        if (state.dateTo) params.set("date_to", state.dateTo);
    }

    const res = await fetch(`/api/compare?${params.toString()}`);
    if (res.ok) {
        state.compareData = await res.json();
    }
}

function renderCompareView(container) {
    container.innerHTML = "";
    const players = state.filters.players || [];
    const cmp = state.compareData;

    if (!cmp) {
        loadCompareData().then(() => {
            if (state.compareData) renderCompareView(container);
        });
        container.innerHTML = `<div class="empty-state"><h3>Lade Spieler-Vergleich...</h3></div>`;
        return;
    }

    const pA = cmp.player_a;
    const pB = cmp.player_b;
    const items = cmp.items || [];

    const optionsA = players
        .map(
            (p) =>
                `<option value="${p.puuid}" ${p.puuid === state.comparePuuidA ? "selected" : ""}>🔵 ${p.label} (${p.games} Sp. gesamt • ${p.winrate}% WR)</option>`
        )
        .join("");

    const optionsB = players
        .map(
            (p) =>
                `<option value="${p.puuid}" ${p.puuid === state.comparePuuidB ? "selected" : ""}>🟡 ${p.label} (${p.games} Sp. gesamt • ${p.winrate}% WR)</option>`
        )
        .join("");

    const activeFilterDesc = [
        state.selectedChampion !== "ALL" ? `Champion: <strong>${state.selectedChampion}</strong>` : "Alle Champions",
        state.selectedRole !== "ALL" ? `Rolle: <strong>${state.selectedRole}</strong>` : null,
        state.selectedQueue !== "ALL" ? `Queue: <strong>${state.selectedQueue}</strong>` : null,
        state.rangeMode === "count" && state.limitGames
            ? `Jeweils letzte <strong>${state.limitGames} passende Spiele</strong>`
            : state.rangeMode === "date"
            ? `Zeitraum: <strong>${state.dateFrom || "Anfang"} bis ${state.dateTo || "Heute"}</strong>`
            : "Alle passenden Spiele",
    ]
        .filter(Boolean)
        .join(" • ");

    // 1. Wo Spieler B (Benchmark / Pro) besser ist (Dein Trainingsfokus)
    const bAheadItems = items
        .filter((it) => it.better_player === "B" && it.category !== "scores" && it.id !== "winrate")
        .sort((a, b) => b.abs_effect - a.abs_effect)
        .slice(0, 5);

    // 2. Wo Spieler A (Du / Fokus) besser ist
    const aAheadItems = items
        .filter((it) => it.better_player === "A" && it.category !== "scores" && it.id !== "winrate")
        .sort((a, b) => b.abs_effect - a.abs_effect)
        .slice(0, 5);

    // 3. Wo beide Spieler fast identisch sind (Auf Augenhöhe)
    const equalItems = items
        .filter((it) => it.category !== "scores" && it.id !== "ally_heal_shield_per_min" && (it.avg_a > 0 || it.avg_b > 0))
        .sort((a, b) => a.abs_effect - b.abs_effect)
        .slice(0, 5);

    // Filter & Sortierung für die Detail-Karten
    let filteredItems = items.slice();
    if (state.compareCategoryFilter !== "ALL") {
        filteredItems = filteredItems.filter((it) => it.category === state.compareCategoryFilter);
    }

    if (state.compareSortMode === "b_ahead") {
        filteredItems.sort((a, b) => {
            const scoreA = a.better_player === "B" ? a.abs_effect + 10 : a.better_player === "EQUAL" ? 0 : -a.abs_effect;
            const scoreB = b.better_player === "B" ? b.abs_effect + 10 : b.better_player === "EQUAL" ? 0 : -b.abs_effect;
            return scoreB - scoreA;
        });
    } else if (state.compareSortMode === "a_ahead") {
        filteredItems.sort((a, b) => {
            const scoreA = a.better_player === "A" ? a.abs_effect + 10 : a.better_player === "EQUAL" ? 0 : -a.abs_effect;
            const scoreB = b.better_player === "A" ? b.abs_effect + 10 : b.better_player === "EQUAL" ? 0 : -b.abs_effect;
            return scoreB - scoreA;
        });
    } else if (state.compareSortMode === "pct") {
        filteredItems.sort((a, b) => Math.abs(b.diff_pct) - Math.abs(a.diff_pct));
    } else {
        filteredItems.sort((a, b) => b.abs_effect - a.abs_effect);
    }

    const catOptionsHtml = [
        `<option value="ALL" ${state.compareCategoryFilter === "ALL" ? "selected" : ""}>Alle Kategorien (${items.length} Stats)</option>`,
        ...state.categories.map(
            (c) =>
                `<option value="${c.id}" ${state.compareCategoryFilter === c.id ? "selected" : ""}>${c.icon} ${c.name}</option>`
        ),
    ].join("");

    container.innerHTML = `
        <!-- Spieler-Auswahl & Benchmark-Steuerung -->
        <div class="cmp-selector-banner">
            <div class="cmp-banner-top">
                <div>
                    <h2>⚔️ 2-Spieler-Vergleich & Pro-Benchmark</h2>
                    <p>Beide Spieler werden unter exakt denselben Filtern verglichen: ${activeFilterDesc}</p>
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                    <button type="button" id="btn-cmp-sync-sinerias" class="btn-secondary" title="Sinerias#EUW (Challenger Master Yi) im Sync-Fenster eintragen & laden">
                        ⭐ Sinerias#EUW / Weiteren Spieler laden
                    </button>
                </div>
            </div>

            <div class="cmp-players-row">
                <!-- Spieler A (Fokus / Du) -->
                <div class="cmp-player-card player-a">
                    <span class="cmp-role-tag">🔵 Spieler A (Dein Fokus-Account)</span>
                    <select id="cmp-select-a" class="select-input">${optionsA}</select>
                    <div class="cmp-player-stats-line">
                        ${pA.rank_label ? `<span>🏅 Rang: <strong>${pA.rank_label}</strong></span>` : ""}
                        <span>🎯 Ausgewählt: <strong>${pA.total_matches} Spiele</strong> (aus ${pA.matching_pool_count} passenden)</span>
                        <span>🏆 Winrate: <strong>${pA.winrate}%</strong> (${pA.wins_count}S / ${pA.losses_count}N)</span>
                        <span>⏱️ Ø Dauer: <strong>${pA.avg_duration}m</strong></span>
                    </div>
                </div>

                <!-- Tauschen Button -->
                <button type="button" id="btn-cmp-swap" class="cmp-swap-btn" title="Spieler A und Spieler B tauschen">
                    ⇄ Tauschen
                </button>

                <!-- Spieler B (Benchmark / Pro / Kumpel) -->
                <div class="cmp-player-card player-b">
                    <span class="cmp-role-tag">🟡 Spieler B (Benchmark / Pro / Kumpel)</span>
                    <select id="cmp-select-b" class="select-input">${optionsB}</select>
                    <div class="cmp-player-stats-line">
                        ${pB.rank_label ? `<span>🏅 Rang: <strong>${pB.rank_label}</strong></span>` : ""}
                        <span>🎯 Ausgewählt: <strong>${pB.total_matches} Spiele</strong> (aus ${pB.matching_pool_count} passenden)</span>
                        <span>🏆 Winrate: <strong>${pB.winrate}%</strong> (${pB.wins_count}S / ${pB.losses_count}N)</span>
                        <span>⏱️ Ø Dauer: <strong>${pB.avg_duration}m</strong></span>
                    </div>
                </div>
            </div>
        </div>

        ${
            pA.total_matches === 0 || pB.total_matches === 0
                ? `
            <div class="empty-state">
                <h3>Für mindestens einen der beiden Spieler sind keine passenden Spiele für diese Filter-Auswahl geladen</h3>
                <p>
                    <strong>🔵 ${pA.label}:</strong> ${pA.total_matches} passende Spiele &nbsp;|&nbsp;
                    <strong>🟡 ${pB.label}:</strong> ${pB.total_matches} passende Spiele.
                    <br/>Klicke oben auf <strong>„⭐ Sinerias#EUW / Weiteren Spieler laden“</strong> oder wähle oben im Champion-Filter „Alle Champions“.
                </p>
            </div>
        `
                : `
            <!-- 3 Executive Summary Boxen -->
            <div class="wl-highlights-grid">
                <div class="wl-highlight-card loss-focus" style="border-top-color:#f59e0b;">
                    <h3>🎯 Wo 🟡 ${pB.label} vorne liegt (Dein Aufholpotenzial)</h3>
                    <div class="wl-highlight-sub">In diesen Bereichen schneidet der Benchmark / Pro am deutlichsten besser ab:</div>
                    <div class="wl-highlight-list">
                        ${
                            bAheadItems.length > 0
                                ? bAheadItems
                                      .map((it) => {
                                          const diffStr = it.is_diff_metric
                                              ? `${it.diff_abs > 0 ? "+" : ""}${formatStatValue(it.diff_abs, it)}`
                                              : `${it.diff_pct > 0 ? "+" : ""}${it.diff_pct}%`;
                                          return `
                                            <div class="wl-hl-item" data-cmp-stat="${it.id}">
                                                <div>
                                                    <div class="wl-hl-name">${it.name}</div>
                                                    <div class="wl-hl-detail">🔵 ${pA.label}: <strong>${formatStatValue(it.avg_a, it)}</strong> vs. 🟡 ${pB.label}: <strong>${formatStatValue(it.avg_b, it)}</strong></div>
                                                </div>
                                                <span class="wl-hl-badge" style="background:rgba(245,158,11,0.16);color:#fbbf24;">${diffStr}</span>
                                            </div>
                                        `;
                                      })
                                      .join("")
                                : `<span class="insight-sub">Keine Rückstände gegenüber ${pB.label} gefunden!</span>`
                        }
                    </div>
                </div>

                <div class="wl-highlight-card win-focus" style="border-top-color:#3b82f6;">
                    <h3>💪 Wo 🔵 ${pA.label} vorne liegt (Deine Stärken)</h3>
                    <div class="wl-highlight-sub">In diesen Statistiken übertriffst du den Vergleichsspieler:</div>
                    <div class="wl-highlight-list">
                        ${
                            aAheadItems.length > 0
                                ? aAheadItems
                                      .map((it) => {
                                          const diffStr = it.is_diff_metric
                                              ? `${it.diff_abs > 0 ? "+" : ""}${formatStatValue(it.diff_abs, it)}`
                                              : `${it.diff_pct > 0 ? "+" : ""}${it.diff_pct}%`;
                                          return `
                                            <div class="wl-hl-item" data-cmp-stat="${it.id}">
                                                <div>
                                                    <div class="wl-hl-name">${it.name}</div>
                                                    <div class="wl-hl-detail">🔵 ${pA.label}: <strong>${formatStatValue(it.avg_a, it)}</strong> vs. 🟡 ${pB.label}: <strong>${formatStatValue(it.avg_b, it)}</strong></div>
                                                </div>
                                                <span class="wl-hl-badge win">${diffStr}</span>
                                            </div>
                                        `;
                                      })
                                      .join("")
                                : `<span class="insight-sub">Wähle zwei unterschiedliche Spieler zum Vergleichen.</span>`
                        }
                    </div>
                </div>

                <div class="wl-highlight-card neutral-focus">
                    <h3>🤝 Auf Augenhöhe (Ähnlichste Werte)</h3>
                    <div class="wl-highlight-sub">Hier seid ihr euch in den ausgewählten Spielen am ähnlichsten:</div>
                    <div class="wl-highlight-list">
                        ${equalItems
                            .map((it) => {
                                return `
                                    <div class="wl-hl-item" data-cmp-stat="${it.id}">
                                        <div>
                                            <div class="wl-hl-name">${it.name}</div>
                                            <div class="wl-hl-detail">🔵 ${formatStatValue(it.avg_a, it)} vs. 🟡 ${formatStatValue(it.avg_b, it)}</div>
                                        </div>
                                        <span class="wl-hl-badge const">Ähnlich</span>
                                    </div>
                                `;
                            })
                            .join("")}
                    </div>
                </div>
            </div>

            <!-- Sortier- & Filterleiste -->
            <div class="wl-controls-row">
                <div class="wl-controls-left">
                    <span>Sortieren nach:</span>
                    <div class="segmented-control" id="cmp-sort-control">
                        <button type="button" data-cmpsort="diff" class="seg-btn ${state.compareSortMode === "diff" ? "active" : ""}">
                            🔥 Größter Unterschied gesamt
                        </button>
                        <button type="button" data-cmpsort="b_ahead" class="seg-btn ${state.compareSortMode === "b_ahead" ? "active" : ""}">
                            🎯 Wo 🟡 ${pB.label} besser ist
                        </button>
                        <button type="button" data-cmpsort="a_ahead" class="seg-btn ${state.compareSortMode === "a_ahead" ? "active" : ""}">
                            💪 Wo 🔵 ${pA.label} besser ist
                        </button>
                        <button type="button" data-cmpsort="pct" class="seg-btn ${state.compareSortMode === "pct" ? "active" : ""}">
                            📈 %-Unterschied
                        </button>
                    </div>
                </div>
                <div class="wl-controls-right">
                    <select id="cmp-cat-filter" class="select-input">
                        ${catOptionsHtml}
                    </select>
                </div>
            </div>

            <!-- Grid mit allen Direktvergleichs-Karten + Dual-Chart -->
            <div class="wl-cards-grid" id="cmp-cards-grid"></div>
        `
        }
    `;

    // Events für Spieler A, Spieler B, Tauschen und Sync-Preset
    container.querySelector("#cmp-select-a").addEventListener("change", async (e) => {
        state.comparePuuidA = e.target.value;
        await loadCompareData();
        renderCompareView(container);
    });

    container.querySelector("#cmp-select-b").addEventListener("change", async (e) => {
        state.comparePuuidB = e.target.value;
        await loadCompareData();
        renderCompareView(container);
    });

    container.querySelector("#btn-cmp-swap").addEventListener("click", async () => {
        const tmp = state.comparePuuidA;
        state.comparePuuidA = state.comparePuuidB;
        state.comparePuuidB = tmp;
        await loadCompareData();
        renderCompareView(container);
    });

    container.querySelector("#btn-cmp-sync-sinerias").addEventListener("click", () => {
        document.getElementById("sync-modal").classList.remove("hidden");
        document.getElementById("sync-gamename").value = "Sinerias";
        document.getElementById("sync-tagline").value = "EUW";
        document.getElementById("sync-region").value = "EUW1";
    });

    const sortBtns = container.querySelectorAll("#cmp-sort-control .seg-btn");
    sortBtns.forEach((btn) => {
        btn.addEventListener("click", () => {
            state.compareSortMode = btn.getAttribute("data-cmpsort");
            renderCompareView(container);
        });
    });

    const catSelect = container.querySelector("#cmp-cat-filter");
    if (catSelect) {
        catSelect.addEventListener("change", (e) => {
            state.compareCategoryFilter = e.target.value;
            renderCompareView(container);
        });
    }

    const cardsGrid = container.querySelector("#cmp-cards-grid");
    if (!cardsGrid) return;

    filteredItems.forEach((it) => {
        const card = document.createElement("article");
        card.className = "wl-stat-card";
        card.id = `cmp-card-${it.id}`;

        let widthA = 50;
        let widthB = 50;
        if (it.avg_a < 0 || it.avg_b < 0) {
            const minVal = Math.min(it.avg_a, it.avg_b, 0);
            const maxVal = Math.max(it.avg_a, it.avg_b, 1);
            const span = Math.max(1, maxVal - minVal);
            widthA = Math.max(8, Math.min(100, Math.round(((it.avg_a - minVal) / span) * 90 + 10)));
            widthB = Math.max(8, Math.min(100, Math.round(((it.avg_b - minVal) / span) * 90 + 10)));
        } else {
            const maxAbs = Math.max(it.avg_a, it.avg_b, 0.001);
            widthA = Math.max(6, Math.min(100, Math.round((it.avg_a / maxAbs) * 100)));
            widthB = Math.max(6, Math.min(100, Math.round((it.avg_b / maxAbs) * 100)));
        }

        let badgeClass = "low";
        let badgeText = "⚖️ Auf Augenhöhe";
        if (it.better_player === "A") {
            badgeClass = "medium";
            badgeText = `🔵 ${pA.label} vorne`;
        } else if (it.better_player === "B") {
            badgeClass = "very_high";
            badgeText = `🟡 ${pB.label} vorne`;
        }

        const diffSign = it.diff_abs > 0 ? "+" : "";
        const pctSign = it.diff_pct > 0 ? "+" : "";
        const diffSummary = it.is_diff_metric
            ? `${diffSign}${formatStatValue(it.diff_abs, it, true)}`
            : `${diffSign}${formatStatValue(it.diff_abs, it, true)} (${pctSign}${it.diff_pct}%)`;

        card.innerHTML = `
            <div class="card-header">
                <div class="card-title-wrap">
                    <div class="card-title">
                        <span>${it.name}</span>
                    </div>
                    <div class="card-desc">${it.description}</div>
                </div>
                <span class="wl-impact-badge ${badgeClass}" title="Effektstärke |d| = ${it.abs_effect}">
                    ${badgeText}
                </span>
            </div>

            <div class="wl-bars-box">
                <div class="wl-bar-row">
                    <div class="wl-bar-header">
                        <span class="cmp-a-label">🔵 ${pA.label} (Sieg: ${formatStatValue(it.win_avg_a, it)} | Loss: ${formatStatValue(it.loss_avg_a, it)})</span>
                        <span class="cmp-a-label">${formatStatValue(it.avg_a, it)}</span>
                    </div>
                    <div class="wl-bar-track">
                        <div class="wl-bar-fill cmp-a-fill" style="width: ${widthA}%"></div>
                    </div>
                </div>

                <div class="wl-bar-row">
                    <div class="wl-bar-header">
                        <span class="cmp-b-label">🟡 ${pB.label} (Sieg: ${formatStatValue(it.win_avg_b, it)} | Loss: ${formatStatValue(it.loss_avg_b, it)})</span>
                        <span class="cmp-b-label">${formatStatValue(it.avg_b, it)}</span>
                    </div>
                    <div class="wl-bar-track">
                        <div class="wl-bar-fill cmp-b-fill" style="width: ${widthB}%"></div>
                    </div>
                </div>

                <div class="wl-diff-footer">
                    <span>Effektstärke: <strong>|d| = ${it.abs_effect}</strong></span>
                    <span>Abstand (🔵 vs 🟡): <strong>${diffSummary}</strong></span>
                </div>
            </div>

            <div class="cmp-chart-wrap">
                <canvas id="cmp-canvas-${it.id}"></canvas>
            </div>
        `;

        cardsGrid.appendChild(card);
        createCompareChartInstance(it, pA, pB);
    });

    container.querySelectorAll(".wl-hl-item").forEach((el) => {
        el.addEventListener("click", () => {
            const sid = el.getAttribute("data-cmp-stat");
            const targetCard = document.getElementById(`cmp-card-${sid}`);
            if (targetCard) {
                targetCard.scrollIntoView({ behavior: "smooth", block: "center" });
            } else {
                state.compareCategoryFilter = "ALL";
                renderCompareView(container);
                setTimeout(() => {
                    const c = document.getElementById(`cmp-card-${sid}`);
                    if (c) c.scrollIntoView({ behavior: "smooth", block: "center" });
                }, 50);
            }
        });
    });
}

function createCompareChartInstance(it, pA, pB) {
    const canvas = document.getElementById(`cmp-canvas-${it.id}`);
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const valsA = it.values_a || [];
    const valsB = it.values_b || [];
    const maxLen = Math.max(valsA.length, valsB.length);
    const labels = Array.from({ length: maxLen }, (_, i) => (state.groupSize === 1 ? `Sp. ${i + 1}` : `Block ${i + 1}`));

    const chart = new Chart(ctx, {
        type: "line",
        data: {
            labels,
            datasets: [
                {
                    label: `🔵 ${pA.label}`,
                    data: valsA,
                    borderColor: "#3b82f6",
                    backgroundColor: "rgba(59, 130, 246, 0.12)",
                    borderWidth: 2.3,
                    pointRadius: maxLen > 25 ? 2 : 3.5,
                    tension: 0.25,
                    fill: false,
                },
                {
                    label: `🟡 ${pB.label}`,
                    data: valsB,
                    borderColor: "#f59e0b",
                    backgroundColor: "rgba(245, 158, 11, 0.12)",
                    borderWidth: 2.3,
                    pointRadius: maxLen > 25 ? 2 : 3.5,
                    tension: 0.25,
                    fill: false,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: "index",
                intersect: false,
            },
            plugins: {
                legend: {
                    display: true,
                    labels: {
                        color: "#cbd5e1",
                        boxWidth: 12,
                        font: { size: 11 },
                    },
                },
                tooltip: {
                    backgroundColor: "rgba(15, 23, 42, 0.96)",
                    titleColor: "#f8fafc",
                    bodyColor: "#e2e8f0",
                    borderColor: "#334155",
                    borderWidth: 1,
                    callbacks: {
                        label: (item) => {
                            const yVal = item.parsed ? item.parsed.y : item.raw;
                            return ` ${item.dataset.label}: ${formatStatValue(yVal, it)}`;
                        },
                    },
                },
            },
            scales: {
                x: {
                    grid: { color: "rgba(51, 65, 85, 0.2)" },
                    ticks: { color: "#94a3b8", font: { size: 10.5 }, maxTicksLimit: 8 },
                },
                y: {
                    grid: { color: "rgba(51, 65, 85, 0.25)" },
                    ticks: {
                        color: "#94a3b8",
                        font: { size: 10.5 },
                        callback: (val) => {
                            if (it.format === "mm_ss") {
                                return formatMmSs(val, "", true);
                            }
                            if (it.format === "rank_lp") {
                                return formatRankLp(val, false, true);
                            }
                            return val;
                        },
                    },
                },
            },
        },
    });

    state.chartInstances[`cmp_${it.id}`] = chart;
}
