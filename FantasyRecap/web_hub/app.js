/* BFL League Hub + Interactive Playoff Machine (Exact BFL Tiebreaker & Live Monte Carlo Engine) */

let HUB = null;
const LOCKED_PICKS = {}; // key: `${week}|${away}|${home}` -> winnerOwner
const CUSTOM_SCORES = {}; // key: `${week}|${away}|${home}` -> { away: number, home: number }
let MODEL_MODE = "optimal"; // "optimal" (weekly optimal healthy lineup + FA fill) | "season_avg"
let EDIT_SCORES_OPEN = false;

const DIV_ORDER = ["North", "South", "East", "West"];

function gameKey(g) {
  return `${g.week}|${g.away}|${g.home}`;
}

function displayName(owner) {
  if (!owner) return "";
  if (owner.toLowerCase() === "rej hoxha") return "Rej Hoxha";
  return owner;
}

function shortName(owner) {
  if (!owner) return "";
  if (owner === "Shawn Lukose") return "Lukose";
  if (owner === "Shawn Ullenbrauck") return "Thor";
  if (owner === "Adam Olen") return "AMO";
  if (owner.toLowerCase() === "rej hoxha") return "Rej";
  return owner.split(" ")[0];
}

function normCdf(z) {
  // Abramowitz & Stegun fast normal CDF approximation for game win probability display
  const t = 1 / (1 + 0.2316419 * Math.abs(z));
  const d = 0.3989423 * Math.exp(-z * z / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return z > 0 ? 1 - p : p;
}

function getProjectedMeanForWeek(owner, week, powerMap) {
  const base = powerMap[owner].mean;
  if (MODEL_MODE === "optimal" && HUB.weekly_projections && HUB.weekly_projections[String(week)]) {
    const info = HUB.weekly_projections[String(week)][owner];
    if (info && info.proj > 0) {
      return 0.75 * info.proj + 0.25 * base;
    }
  }
  return base;
}

function getFaFillsForWeek(owner, week) {
  if (HUB.weekly_projections && HUB.weekly_projections[String(week)]) {
    const info = HUB.weekly_projections[String(week)][owner];
    if (info && Array.isArray(info.fa_fills)) return info.fa_fills;
  }
  return [];
}

function gaussianRand() {
  let u = 0, v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
}

/* ---------- EXACT BFL TIEBREAKER ENGINE (Matches ESPN `H2H_RECORD` + playoff_odds_simulator.py) ---------- */

function resolveDivisionTie(tied, records, h2h) {
  if (tied.length <= 1) return tied.slice();
  if (tied.length === 2) {
    const [a, b] = tied;
    const wa = h2h[`${a}|${b}`] || 0;
    const wb = h2h[`${b}|${a}`] || 0;
    if (wa > wb) return [a, b];
    if (wb > wa) return [b, a];
    return [a, b].sort((x, y) => records[y].pf - records[x].pf);
  }
  const subPct = {};
  for (const t of tied) {
    let sw = 0, sl = 0;
    for (const o of tied) {
      if (o === t) continue;
      sw += h2h[`${t}|${o}`] || 0;
      sl += h2h[`${o}|${t}`] || 0;
    }
    subPct[t] = (sw + sl) > 0 ? sw / (sw + sl) : 0.5;
  }
  const maxPct = Math.max(...tied.map(t => subPct[t]));
  const top = tied.filter(t => Math.abs(subPct[t] - maxPct) < 1e-6);
  if (top.length === 1) {
    const best = top[0];
    const rem = tied.filter(t => t !== best);
    return [best, ...resolveDivisionTie(rem, records, h2h)];
  }
  return tied.slice().sort((x, y) => (subPct[y] - subPct[x]) || (records[y].pf - records[x].pf));
}

function resolveWildcardTie(tied, records, h2h) {
  if (tied.length <= 1) return tied.slice();
  if (tied.length === 2) {
    const [a, b] = tied;
    const wa = h2h[`${a}|${b}`] || 0;
    const wb = h2h[`${b}|${a}`] || 0;
    if (wa > wb && wb === 0) return [a, b];
    if (wb > wa && wa === 0) return [b, a];
    return [a, b].sort((x, y) => records[y].pf - records[x].pf);
  }
  return tied.slice().sort((x, y) => records[y].pf - records[x].pf);
}

function computeStandingsAndSeeding(records, h2h, divisions) {
  const divTables = {};
  const divChamps = [];
  const wcPool = [];

  for (const div of DIV_ORDER) {
    const members = divisions[div] || [];
    const byWins = {};
    for (const m of members) {
      const w = records[m].wins;
      if (!byWins[w]) byWins[w] = [];
      byWins[w].push(m);
    }
    const ordered = [];
    for (const w of Object.keys(byWins).map(Number).sort((a, b) => b - a)) {
      ordered.push(...resolveDivisionTie(byWins[w], records, h2h));
    }
    divTables[div] = ordered;
    if (ordered.length) {
      divChamps.push(ordered[0]);
      wcPool.push(...ordered.slice(1));
    }
  }

  const rankGroup = (list) => {
    const byWins = {};
    for (const m of list) {
      const w = records[m].wins;
      if (!byWins[w]) byWins[w] = [];
      byWins[w].push(m);
    }
    const out = [];
    for (const w of Object.keys(byWins).map(Number).sort((a, b) => b - a)) {
      out.push(...resolveWildcardTie(byWins[w], records, h2h));
    }
    return out;
  };

  const sortedChamps = rankGroup(divChamps);
  const sortedWc = rankGroup(wcPool);
  const fullSeeding = [...sortedChamps, ...sortedWc];
  return { divTables, sortedChamps, sortedWc, fullSeeding };
}

function buildStateWithLockedPicks() {
  const records = {};
  const h2h = {};
  const divRec = {};
  const ownerDiv = {};
  const power = {};

  for (const t of HUB.teams) {
    records[t.owner] = { wins: 0, losses: 0, pf: 0.0, divWins: 0, divLosses: 0 };
    ownerDiv[t.owner] = t.division;
    power[t.owner] = { mean: t.power_mean, std: t.power_std };
  }

  const applyGame = (away, home, aPts, hPts) => {
    records[away].pf += aPts;
    records[home].pf += hPts;
    const isDiv = ownerDiv[away] === ownerDiv[home];
    if (aPts >= hPts) {
      records[away].wins += 1;
      records[home].losses += 1;
      h2h[`${away}|${home}`] = (h2h[`${away}|${home}`] || 0) + 1;
      if (isDiv) { records[away].divWins += 1; records[home].divLosses += 1; }
    } else {
      records[home].wins += 1;
      records[away].losses += 1;
      h2h[`${home}|${away}`] = (h2h[`${home}|${away}`] || 0) + 1;
      if (isDiv) { records[home].divWins += 1; records[away].divLosses += 1; }
    }
  };

  for (const g of HUB.completed_games) {
    applyGame(g.away, g.home, g.away_score, g.home_score);
  }

  const unlockedGames = [];
  for (const g of HUB.remaining_schedule) {
    const k = gameKey(g);
    const custom = CUSTOM_SCORES[k];
    const lockedWinner = LOCKED_PICKS[k];
    if (custom && Number.isFinite(custom.away) && Number.isFinite(custom.home)) {
      applyGame(g.away, g.home, custom.away, custom.home);
    } else if (lockedWinner) {
      // Use the team's actual Week-W projected mean (+small winner margin) for realistic PF tiebreakers
      const mA = getProjectedMeanForWeek(g.away, g.week, power);
      const mH = getProjectedMeanForWeek(g.home, g.week, power);
      const hi = Math.max(mA, mH) + 3.5;
      const lo = Math.min(mA, mH) - 3.5;
      if (lockedWinner === g.away) applyGame(g.away, g.home, hi, lo);
      else applyGame(g.away, g.home, lo, hi);
    } else {
      unlockedGames.push(g);
    }
  }

  return { records, h2h, ownerDiv, power, unlockedGames };
}

function runBrowserMonteCarlo(nSims = 2000) {
  const { records, h2h, power, unlockedGames } = buildStateWithLockedPicks();
  const owners = HUB.teams.map(t => t.owner);
  const poCount = {}, divCount = {}, byeCount = {};
  for (const o of owners) { poCount[o] = 0; divCount[o] = 0; byeCount[o] = 0; }

  const iters = unlockedGames.length === 0 ? 1 : nSims;
  for (let i = 0; i < iters; i++) {
    const simRec = {};
    for (const o of owners) simRec[o] = { wins: records[o].wins, losses: records[o].losses, pf: records[o].pf };
    const simH2H = Object.assign({}, h2h);

    for (const g of unlockedGames) {
      const aMu = getProjectedMeanForWeek(g.away, g.week, power);
      const hMu = getProjectedMeanForWeek(g.home, g.week, power);
      const aPts = aMu + gaussianRand() * power[g.away].std;
      const hPts = hMu + gaussianRand() * power[g.home].std;
      simRec[g.away].pf += aPts;
      simRec[g.home].pf += hPts;
      if (aPts >= hPts) {
        simRec[g.away].wins += 1; simRec[g.home].losses += 1;
        simH2H[`${g.away}|${g.home}`] = (simH2H[`${g.away}|${g.home}`] || 0) + 1;
      } else {
        simRec[g.home].wins += 1; simRec[g.away].losses += 1;
        simH2H[`${g.home}|${g.away}`] = (simH2H[`${g.home}|${g.away}`] || 0) + 1;
      }
    }

    const { sortedChamps, fullSeeding } = computeStandingsAndSeeding(simRec, simH2H, HUB.divisions);
    for (const dc of sortedChamps) divCount[dc] += 1;
    if (sortedChamps.length) byeCount[sortedChamps[0]] += 1;
    for (const pfTeam of fullSeeding.slice(0, 7)) poCount[pfTeam] += 1;
  }

  const odds = {};
  for (const o of owners) {
    odds[o] = {
      po: ((poCount[o] / iters) * 100).toFixed(1),
      div: ((divCount[o] / iters) * 100).toFixed(1),
      bye: ((byeCount[o] / iters) * 100).toFixed(1),
    };
  }
  const currentSeeding = computeStandingsAndSeeding(records, h2h, HUB.divisions);
  return { records, h2h, odds, currentSeeding, unlockedCount: unlockedGames.length, iters };
}

/* ---------- RENDER PLAYOFF MACHINE ---------- */

function renderPlayoffMachine() {
  const { records, h2h, odds, currentSeeding, unlockedCount, iters } = runBrowserMonteCarlo(2000);
  const lockedCount = Object.keys(LOCKED_PICKS).length;
  const totalRem = HUB.remaining_schedule.length;
  const teamMap = Object.fromEntries(HUB.teams.map(t => [t.owner, t]));

  const banner = document.getElementById("pickStatusBanner");
  const modelTxt = MODEL_MODE === "optimal"
    ? "Optimal Healthy Weekly Lineups (auto-fills Bye/Injury gaps with best Free Agent)"
    : "Season Average PPG";
  if (lockedCount === 0) {
    banner.innerHTML = `<strong>Standings:</strong> Completed Weeks 1–${HUB.completed_week} (no games locked yet). <strong>PO% / Div% / Bye%:</strong> ${iters.toLocaleString()} live Monte Carlo sims over Weeks ${HUB.completed_week + 1}–14 using <em>${modelTxt}</em>.`;
  } else if (unlockedCount === 0) {
    banner.innerHTML = `<strong>Full Season Locked (${lockedCount}/${totalRem} games):</strong> Exact deterministic Week 15 BFL Playoff Bracket computed with official H2H &amp; Points-For tiebreakers. Toggle <em>Edit Scores</em> to test exact PF tiebreakers.`;
  } else {
    banner.innerHTML = `<strong>Custom Scenario (${lockedCount}/${totalRem} locked):</strong> Standings reflect Weeks 1–${HUB.completed_week} + your picks; remaining ${unlockedCount} games simulated ${iters.toLocaleString()}x via <em>${modelTxt}</em>.`;
  }
  document.getElementById("simModeBadge").textContent =
    unlockedCount === 0 ? "100% Deterministic Bracket" : `${iters.toLocaleString()} Live Browser Sims`;

  // Playoff Table (#1-#7)
  const poBody = document.querySelector("#playoffTable tbody");
  poBody.innerHTML = currentSeeding.fullSeeding.slice(0, 7).map((own, idx) => {
    const seed = idx + 1;
    const r = records[own];
    const t = teamMap[own];
    const o = odds[own];
    const badgeCls = seed === 1 ? "seed-1" : (seed <= 4 ? "seed-div" : "seed-wc");
    const roleTag = seed === 1 ? "1st-Rd Bye" : (seed <= 4 ? `${t.division} Ldr` : `WC #${seed - 4}`);
    return `
      <tr>
        <td><span class="seed-badge ${badgeCls}">#${seed}</span></td>
        <td><strong>${displayName(own)}</strong><span class="tie-note">${roleTag}</span></td>
        <td>${t.division}</td>
        <td><strong>${r.wins}-${r.losses}</strong></td>
        <td>${r.pf.toFixed(1)}</td>
        <td><strong style="color:${Number(o.po) >= 75 ? 'var(--green)' : (Number(o.po) >= 35 ? 'var(--gold)' : 'var(--muted)')}">${o.po}%</strong></td>
        <td>${o.div}%</td>
        <td>${o.bye}%</td>
      </tr>
    `;
  }).join("");

  // Bubble Table (#8-#12)
  const bubBody = document.querySelector("#bubbleTable tbody");
  bubBody.innerHTML = currentSeeding.fullSeeding.slice(7, 12).map((own, idx) => {
    const rank = idx + 8;
    const r = records[own];
    const t = teamMap[own];
    const o = odds[own];
    return `
      <tr>
        <td>#${rank}</td>
        <td><strong>${displayName(own)}</strong></td>
        <td>${t.division}</td>
        <td>${r.wins}-${r.losses}</td>
        <td>${r.pf.toFixed(1)}</td>
        <td><strong>${o.po}%</strong></td>
      </tr>
    `;
  }).join("");

  // Week 15 Bracket Preview
  const f = currentSeeding.fullSeeding;
  const brHtml = `
    <div class="bracket-box bye">
      <div class="bracket-lbl">First-Round Bye (Week 15)</div>
      <div class="bracket-row"><span><strong>#1 ${shortName(f[0])}</strong></span><span>${records[f[0]].wins}-${records[f[0]].losses}</span></div>
      <div class="tie-note">Plays lowest surviving seed in Week 16 Semis</div>
    </div>
    <div class="bracket-box">
      <div class="bracket-lbl">Quarterfinal 1</div>
      <div class="bracket-row"><span>#2 <strong>${shortName(f[1])}</strong></span><span>${records[f[1]].wins}-${records[f[1]].losses}</span></div>
      <div class="bracket-row"><span>#7 <strong>${shortName(f[6])}</strong></span><span>${records[f[6]].wins}-${records[f[6]].losses}</span></div>
    </div>
    <div class="bracket-box">
      <div class="bracket-lbl">Quarterfinal 2</div>
      <div class="bracket-row"><span>#3 <strong>${shortName(f[2])}</strong></span><span>${records[f[2]].wins}-${records[f[2]].losses}</span></div>
      <div class="bracket-row"><span>#6 <strong>${shortName(f[5])}</strong></span><span>${records[f[5]].wins}-${records[f[5]].losses}</span></div>
    </div>
    <div class="bracket-box">
      <div class="bracket-lbl">Quarterfinal 3</div>
      <div class="bracket-row"><span>#4 <strong>${shortName(f[3])}</strong></span><span>${records[f[3]].wins}-${records[f[3]].losses}</span></div>
      <div class="bracket-row"><span>#5 <strong>${shortName(f[4])}</strong></span><span>${records[f[4]].wins}-${records[f[4]].losses}</span></div>
    </div>
  `;
  document.getElementById("bracketPreview").innerHTML = brHtml;

  // Interactive Schedule Picker grouped by week (with live weekly optimal projections & optional custom score editor!)
  const powerMap = Object.fromEntries(HUB.teams.map(t => [t.owner, { mean: t.power_mean, std: t.power_std }]));
  const byWeek = {};
  for (const g of HUB.remaining_schedule) {
    if (!byWeek[g.week]) byWeek[g.week] = [];
    byWeek[g.week].push(g);
  }
  const schedEl = document.getElementById("schedulePicker");
  schedEl.innerHTML = Object.keys(byWeek).map(Number).sort((a, b) => a - b).map(wk => {
    const gamesHtml = byWeek[wk].map(g => {
      const k = gameKey(g);
      const custom = CUSTOM_SCORES[k];
      const picked = custom
        ? (custom.away >= custom.home ? g.away : g.home)
        : (LOCKED_PICKS[k] || "");
      const aCls = picked === g.away ? "picked-win" : (picked === g.home ? "picked-loss" : "");
      const hCls = picked === g.home ? "picked-win" : (picked === g.away ? "picked-loss" : "");

      const aMu = getProjectedMeanForWeek(g.away, wk, powerMap);
      const hMu = getProjectedMeanForWeek(g.home, wk, powerMap);
      const combStd = Math.sqrt(powerMap[g.away].std ** 2 + powerMap[g.home].std ** 2) || 20.0;
      const aWinPct = Math.round(normCdf((aMu - hMu) / combStd) * 100);
      const hWinPct = 100 - aWinPct;
      const aFa = getFaFillsForWeek(g.away, wk);
      const hFa = getFaFillsForWeek(g.home, wk);
      const aFaBadge = (MODEL_MODE === "optimal" && aFa.length) ? `<span class="fa-tag" title="Auto-filled gap: ${aFa.join(', ')}">FA</span>` : "";
      const hFaBadge = (MODEL_MODE === "optimal" && hFa.length) ? `<span class="fa-tag" title="Auto-filled gap: ${hFa.join(', ')}">FA</span>` : "";

      const aDispScore = custom ? custom.away.toFixed(1) : aMu.toFixed(1);
      const hDispScore = custom ? custom.home.toFixed(1) : hMu.toFixed(1);

      const editRow = EDIT_SCORES_OPEN ? `
        <div class="score-edit-row">
          <input class="score-inp" type="number" step="0.5" data-skey="${k}" data-side="away" value="${aDispScore}" aria-label="${shortName(g.away)} score" />
          <span>Custom Score (PF)</span>
          <input class="score-inp" type="number" step="0.5" data-skey="${k}" data-side="home" value="${hDispScore}" aria-label="${shortName(g.home)} score" />
        </div>
      ` : "";

      return `
        <div class="match-row">
          <button class="pick-btn ${aCls}" data-key="${k}" data-win="${g.away}" title="${displayName(g.away)} projected ${aMu.toFixed(1)} pts (${aWinPct}% win prob)">
            <span>${shortName(g.away)}${aFaBadge}</span><span class="pick-rec">${aMu.toFixed(0)} · ${aWinPct}%</span>
          </button>
          <span class="at-sep">@</span>
          <button class="pick-btn ${hCls}" data-key="${k}" data-win="${g.home}" title="${displayName(g.home)} projected ${hMu.toFixed(1)} pts (${hWinPct}% win prob)">
            <span>${shortName(g.home)}${hFaBadge}</span><span class="pick-rec">${hMu.toFixed(0)} · ${hWinPct}%</span>
          </button>
        </div>
        ${editRow}
      `;
    }).join("");
    return `<div class="week-col"><h4><span>Week ${wk}</span><span class="muted small">${wk === 14 ? "Regular Season Finale" : "Proj · Win%"}</span></h4>${gamesHtml}</div>`;
  }).join("");

  schedEl.querySelectorAll(".pick-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const k = btn.dataset.key;
      const w = btn.dataset.win;
      delete CUSTOM_SCORES[k];
      if (LOCKED_PICKS[k] === w) delete LOCKED_PICKS[k];
      else LOCKED_PICKS[k] = w;
      renderPlayoffMachine();
    });
  });

  if (EDIT_SCORES_OPEN) {
    schedEl.querySelectorAll(".score-inp").forEach(inp => {
      inp.addEventListener("change", () => {
        const k = inp.dataset.skey;
        const side = inp.dataset.side;
        const val = parseFloat(inp.value);
        if (!Number.isFinite(val)) return;
        const g = HUB.remaining_schedule.find(x => gameKey(x) === k);
        if (!g) return;
        const cur = CUSTOM_SCORES[k] || {
          away: parseFloat(getProjectedMeanForWeek(g.away, g.week, powerMap).toFixed(1)),
          home: parseFloat(getProjectedMeanForWeek(g.home, g.week, powerMap).toFixed(1)),
        };
        cur[side] = val;
        CUSTOM_SCORES[k] = cur;
        LOCKED_PICKS[k] = cur.away >= cur.home ? g.away : g.home;
        renderPlayoffMachine();
      });
    });
  }

  // 4 Division Standings Cards
  const divEl = document.getElementById("divisionsGrid");
  divEl.innerHTML = DIV_ORDER.map(div => {
    const rows = currentSeeding.divTables[div].map((own, idx) => {
      const r = records[own];
      const o = odds[own];
      const seedNum = currentSeeding.fullSeeding.indexOf(own) + 1;
      const inPo = seedNum <= 7;
      return `
        <tr>
          <td>${idx + 1}</td>
          <td><strong>${displayName(own)}</strong> ${inPo ? `<span class="pill pill-green">#${seedNum}</span>` : ""}</td>
          <td><strong>${r.wins}-${r.losses}</strong></td>
          <td>${r.divWins}-${r.divLosses}</td>
          <td>${r.pf.toFixed(1)}</td>
          <td>${o.div}%</td>
        </tr>
      `;
    }).join("");
    return `
      <div class="card" style="margin-bottom:0">
        <div class="card-head"><h3>${div} Division</h3><span class="pill">Top team auto-bids</span></div>
        <div class="table-wrap">
          <table class="data-table compact">
            <thead><tr><th>#</th><th>Manager</th><th>Rec</th><th>Div</th><th>PF</th><th>Div %</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>
    `;
  }).join("");
}

/* ---------- RENDER RECORD BOOK & RIVALRY EXPLORER ---------- */

function renderHistoryTab() {
  const owners = HUB.teams.map(t => t.owner).sort();
  const sel1 = document.getElementById("rivOwner1");
  const sel2 = document.getElementById("rivOwner2");
  sel1.innerHTML = owners.map(o => `<option value="${o}">${displayName(o)}</option>`).join("");
  sel2.innerHTML = owners.map(o => `<option value="${o}">${displayName(o)}</option>`).join("");
  sel1.value = "Nick Christus";
  sel2.value = "Daniel Kruszewski";

  const drawRivalry = () => {
    const a = sel1.value, b = sel2.value;
    const card = document.getElementById("rivalryCard");
    if (a === b) {
      card.innerHTML = `<p class="muted">Select two different BFL managers to inspect their 18-year series.</p>`;
      return;
    }
    const [o1, o2] = a < b ? [a, b] : [b, a];
    const rec = HUB.history.h2h[`${o1}|||${o2}`];
    if (!rec) {
      card.innerHTML = `<p class="muted"><strong>${a}</strong> and <strong>${b}</strong> have never met in BFL history.</p>`;
      return;
    }
    const wA = a === o1 ? rec.w1 : rec.w2;
    const wB = a === o1 ? rec.w2 : rec.w1;
    const avgA = a === o1 ? rec.avg1 : rec.avg2;
    const avgB = a === o1 ? rec.avg2 : rec.avg1;
    const poA = a === o1 ? rec.po1 : rec.po2;
    const poB = a === o1 ? rec.po2 : rec.po1;
    const headTxt = wA > wB ? `<strong>${displayName(a)}</strong> leads ${wA}–${wB}` : (wB > wA ? `<strong>${displayName(b)}</strong> leads ${wB}–${wA}` : `Series deadlocked <strong>${wA}–${wB}</strong>`);
    const sbTxt = rec.super_bowls.length
      ? rec.super_bowls.map(s => `${s.year}: ${displayName(s.champ)} def. ${displayName(s.runner_up)} (${s.score})`).join(" • ")
      : "None";

    card.innerHTML = `
      <div style="font-size:1.1rem;margin-bottom:4px;">${headTxt} <span class="muted small">(${rec.games} meetings since 2008)</span></div>
      <div class="riv-stats">
        <div class="stat-box"><div class="lbl">Avg Points / Game</div><div class="val">${shortName(a)} ${avgA} vs ${shortName(b)} ${avgB}</div></div>
        <div class="stat-box"><div class="lbl">Current Streak</div><div class="val">${shortName(rec.streak_owner)} W${rec.streak_len}</div></div>
        <div class="stat-box"><div class="lbl">Playoff H2H</div><div class="val">${poA + poB > 0 ? `${shortName(a)} ${poA}–${poB} ${shortName(b)}` : "No Playoff Clashes"}</div></div>
        <div class="stat-box"><div class="lbl">Last Meeting</div><div class="val">${rec.last.season} Wk ${rec.last.week}: ${shortName(rec.last.winner)} (${rec.last.hi}–${rec.last.lo})</div></div>
        <div class="stat-box"><div class="lbl">Closest Finish</div><div class="val">${rec.closest.season} Wk ${rec.closest.week}: ${shortName(rec.closest.winner)} (+${rec.closest.margin})</div></div>
        <div class="stat-box"><div class="lbl">Super Bowl History</div><div class="val" style="font-size:0.84rem">${sbTxt}</div></div>
      </div>
    `;
  };
  sel1.addEventListener("change", drawRivalry);
  sel2.addEventListener("change", drawRivalry);
  drawRivalry();

  // Career table
  document.querySelector("#careerTable tbody").innerHTML = HUB.history.career.map((c, idx) => `
    <tr>
      <td>${idx + 1}</td>
      <td><strong>${displayName(c.owner)}</strong><span class="tie-note">${c.division} &bull; ${c.seasons} yrs</span></td>
      <td>${c.rings > 0 ? `<span class="pill pill-gold">${c.rings}</span>` : "0"}</td>
      <td>${c.wins}-${c.losses}</td>
      <td><strong>${c.win_pct.toFixed(1)}%</strong></td>
      <td>${c.avg_pf.toFixed(1)}</td>
      <td>${c.high_score.toFixed(1)}</td>
    </tr>
  `).join("");

  // Championships log
  document.querySelector("#champsTable tbody").innerHTML = HUB.history.championships.map(s => `
    <tr>
      <td><strong>${s.year}</strong></td>
      <td><strong>${displayName(s.champ)}</strong></td>
      <td>${displayName(s.runner_up)}</td>
      <td>${s.score}</td>
    </tr>
  `).join("");
}

/* ---------- RENDER DRAFT HALL ---------- */

function renderDraftsTab() {
  const sSel = document.getElementById("draftSeasonSelect");
  const oSel = document.getElementById("draftOwnerSelect");
  const years = HUB.history.draft_seasons || [];
  sSel.innerHTML = [
    `<option value="${HUB.season}">${HUB.season} Draft</option>`,
    `<option value="all">All-Time (2018–${HUB.season})</option>`,
    ...years.filter(y => y !== HUB.season).map(y => `<option value="${y}">${y} Draft</option>`),
  ].join("");
  oSel.innerHTML = `<option value="">All Managers</option>` + HUB.teams.map(t => `<option value="${t.owner}">${displayName(t.owner)}</option>`).join("");

  const draw = () => {
    const yr = sSel.value;
    const own = oSel.value;
    const pack = HUB.history.drafts[yr] || { steals: [], busts: [] };
    const filterFn = r => !own || r.owner === own;
    const fmt = (rows, isSteal) => {
      const f = rows.filter(filterFn).slice(0, 10);
      if (!f.length) return `<tr><td colspan="6" class="muted">No matching picks in top list for this filter.</td></tr>`;
      return f.map(r => `
        <tr>
          <td><strong>${r.player}</strong>${yr === "all" ? ` <span class="muted small">(${r.season})</span>` : ""}</td>
          <td>${shortName(r.owner)}</td>
          <td>Rd ${r.round} (#${r.pick})</td>
          <td>${r.pos}${r.pos_draft_rank} &rarr; ${r.pos}${r.pos_finish_rank}</td>
          <td>${r.pts}</td>
          <td style="color:${isSteal ? 'var(--green)' : 'var(--red)'};font-weight:700">${r.delta > 0 ? '+' : ''}${r.delta}</td>
        </tr>
      `).join("");
    };
    document.querySelector("#stealsTable tbody").innerHTML = fmt(pack.steals, true);
    document.querySelector("#bustsTable tbody").innerHTML = fmt(pack.busts, false);
  };
  sSel.addEventListener("change", draw);
  oSel.addEventListener("change", draw);
  draw();
}

/* ---------- RENDER TRADE VAULT ---------- */

let TRADE_ERA = "all_time"; // "all_time" | "live"
let TRADE_LENS = "started"; // "started" | "ros"

function renderTradesTab() {
  const pack = HUB.trades;
  if (!pack) return;

  const liveYr = pack.live ? pack.live.season : HUB.season;
  const btnLive = document.getElementById("btnLiveEra");
  if (btnLive) {
    btnLive.textContent = `${liveYr} Live Tracker (${pack.live ? pack.live.trade_count : 0})`;
  }
  const btnAll = document.querySelector('#tradeEraGroup [data-era="all_time"]');
  if (btnAll && pack.all_time) {
    const yrs = pack.all_time.seasons || [];
    const span = yrs.length ? `${Math.min(...yrs)}–${Math.max(...yrs)}` : "2018–2025";
    btnAll.textContent = `All-Time Vault (${span})`;
  }

  const catSel = document.getElementById("tradeCategorySelect");
  const yrSel = document.getElementById("tradeSeasonSelect");
  const ownSel = document.getElementById("tradeOwnerSelect");

  ownSel.innerHTML = `<option value="">All Managers</option>` +
    HUB.teams.map(t => t.owner).sort().map(o => `<option value="${o}">${displayName(o)}</option>`).join("");

  const syncSeasonSelect = () => {
    const bucket = pack[TRADE_ERA] || { seasons: [], trades: [] };
    if (TRADE_ERA === "live") {
      yrSel.innerHTML = `<option value="">${liveYr} Live Season</option>`;
      yrSel.disabled = true;
    } else {
      const yrs = bucket.seasons || [];
      yrSel.disabled = false;
      yrSel.innerHTML = `<option value="">All Seasons (${yrs.length ? Math.min(...yrs) + "–" + Math.max(...yrs) : ""})</option>` +
        yrs.map(y => `<option value="${y}">${y} Season</option>`).join("");
    }
  };

  const tagClass = (t) => {
    if (t.includes("Title") || t.includes("Finals") || t.includes("Playoff")) return "tag-gold";
    if (t.includes("Flipped") || t.includes("Win-Win") || t.includes("Deadline")) return "tag-green";
    if (t.includes("Injury") || t.includes("Cut") || t.includes("Lose-Lose")) return "tag-red";
    if (t.includes("Tree")) return "tag-purple";
    return "tag-blue";
  };

  const fmtVorp = (v) => `${v > 0 ? "+" : ""}${Number(v).toFixed(1)}`;

  const draw = () => {
    const bucket = pack[TRADE_ERA] || { trades: [], owner_cards: { started: [], ros: [] } };
    const lens = TRADE_LENS;
    const cat = catSel.value;
    const yrFilter = TRADE_ERA === "all_time" ? yrSel.value : "";
    const ownFilter = ownSel.value;

    document.getElementById("tradeVaultTitle").textContent =
      TRADE_ERA === "live"
        ? `${liveYr} Live Trade Tracker (Through Week ${HUB.completed_week})`
        : "BFL All-Time Trade Vault — Hall of Fleeces";
    document.getElementById("sharksTitle").textContent =
      TRADE_ERA === "live" ? `${liveYr} Live Deal Makers` : "Career Trade Sharks vs. Marks";
    document.getElementById("sharksLensPill").textContent =
      lens === "started" ? "Actual Lineup VORP" : "Pure Player VORP";

    // Methodology blurb tailored to active lens
    const blurb = document.getElementById("tradeMethodologyBlurb");
    blurb.innerHTML = lens === "started"
      ? `<strong>Actual Lineup Impact:</strong> Counts only weeks the acquiring manager <em>started</em> the player above 16-team positional replacement (QB16, RB36, WR38, TE16), with <strong>1.5&times; Championship Playoff weighting</strong> (consolation = 0&times;).`
      : `<strong>Pure Player Outcome:</strong> Counts all rest-of-season weeks above 16-team positional replacement from the trade week onward (even if benched, dropped, or flipped), with <strong>1.5&times; Playoff-week weighting</strong>.`;

    // 1. Render Sharks vs Marks Leaderboard
    const cards = (bucket.owner_cards && bucket.owner_cards[lens]) || [];
    const sharksBody = document.querySelector("#tradeSharksTable tbody");
    sharksBody.innerHTML = cards.map((c, idx) => {
      const col = c.net_vorp > 3 ? "var(--green)" : (c.net_vorp < -3 ? "var(--red)" : "var(--muted)");
      const selCls = ownFilter === c.owner ? "selected-row" : "";
      return `
        <tr class="clickable-row ${selCls}" data-owner="${c.owner}">
          <td>${idx + 1}</td>
          <td><strong>${shortName(c.owner)}</strong></td>
          <td>${c.trades}</td>
          <td>${c.wins}-${c.losses}${c.pushes ? `-${c.pushes}` : ""}</td>
          <td style="color:${col};font-weight:800">${fmtVorp(c.net_vorp)}</td>
        </tr>
      `;
    }).join("");

    sharksBody.querySelectorAll("tr[data-owner]").forEach(tr => {
      tr.addEventListener("click", () => {
        const clicked = tr.dataset.owner;
        ownSel.value = (ownSel.value === clicked) ? "" : clicked;
        draw();
      });
    });

    // 2. Owner Report Card (if a specific manager is selected)
    const repEl = document.getElementById("ownerTradeReport");
    const ownerCard = ownFilter ? cards.find(c => c.owner === ownFilter) : null;
    if (ownerCard) {
      repEl.style.display = "block";
      const netCol = ownerCard.net_vorp >= 0 ? "var(--green)" : "var(--red)";
      const favTxt = ownerCard.favorite_partner
        ? `${shortName(ownerCard.favorite_partner.owner)} (${ownerCard.favorite_partner.count} ${ownerCard.favorite_partner.count === 1 ? "deal" : "deals"}, ${fmtVorp(ownerCard.favorite_partner.net_vorp)})`
        : "—";
      const markTxt = ownerCard.biggest_mark
        ? `${shortName(ownerCard.biggest_mark.owner)} (${fmtVorp(ownerCard.biggest_mark.net_vorp)} in ${ownerCard.biggest_mark.count})`
        : "—";
      const nemTxt = ownerCard.worst_nemesis
        ? `${shortName(ownerCard.worst_nemesis.owner)} (${fmtVorp(ownerCard.worst_nemesis.net_vorp)} in ${ownerCard.worst_nemesis.count})`
        : "—";
      const renderExtreme = (label, item, cls, emptyMsg) => {
        if (!item) return `<div class="extreme-box ${cls}"><div style="font-weight:700;margin-bottom:3px;color:${cls === "best" ? "var(--green)" : "var(--red)"}">${label}</div><div class="muted small">${emptyMsg}</div></div>`;
        return `
          <div class="extreme-box ${cls}">
            <div style="display:flex;justify-content:space-between;margin-bottom:4px">
              <strong style="color:${cls === "best" ? "var(--green)" : "var(--red)"}">${label}</strong>
              <span class="pill">${item.season} Wk ${item.week} &bull; ${fmtVorp(item.swing)} VORP</span>
            </div>
            <div>vs <strong>${displayName(item.partner)}</strong></div>
            <div class="muted small" style="margin-top:3px">Got: ${item.received.join(", ")}</div>
            <div class="muted small">Sent: ${item.sent.join(", ")}</div>
          </div>
        `;
      };
      const bestLbl = TRADE_ERA === "live" ? `Best ${liveYr} Trade` : "Best Trade Ever (Biggest Fleece)";
      const worstLbl = TRADE_ERA === "live" ? `Worst ${liveYr} Trade` : "Worst Trade Ever (Biggest Regret)";
      repEl.innerHTML = `
        <div class="owner-report">
          <div class="owner-report-head">
            <div>
              <h3 style="font-size:1.05rem">${displayName(ownerCard.owner)} — ${TRADE_ERA === "live" ? liveYr + " Live" : "Career"} Trade Report Card</h3>
              <span class="muted small">${ownerCard.trades} verified ${ownerCard.trades === 1 ? "deal" : "deals"} &bull; ${lens === "started" ? "Actual Lineup Impact" : "Pure Player Outcome"}</span>
            </div>
            <button id="btnClearTradeOwner" class="btn btn-secondary btn-sm">Clear Manager Filter</button>
          </div>
          <div class="owner-report-grid">
            <div class="stat-box"><div class="lbl">Trade Record</div><div class="val">${ownerCard.wins}–${ownerCard.losses}${ownerCard.pushes ? `–${ownerCard.pushes}` : ""} (${ownerCard.win_pct}%)</div></div>
            <div class="stat-box"><div class="lbl">Net Trade VORP</div><div class="val" style="color:${netCol}">${fmtVorp(ownerCard.net_vorp)}</div></div>
            <div class="stat-box"><div class="lbl">Avg Swing / Deal</div><div class="val">${fmtVorp(ownerCard.avg_swing)}</div></div>
            <div class="stat-box"><div class="lbl">Favorite Partner</div><div class="val" style="font-size:0.84rem">${favTxt}</div></div>
            <div class="stat-box"><div class="lbl">Biggest Mark</div><div class="val" style="font-size:0.84rem;color:var(--green)">${markTxt}</div></div>
            <div class="stat-box"><div class="lbl">Worst Nemesis</div><div class="val" style="font-size:0.84rem;color:var(--red)">${nemTxt}</div></div>
          </div>
          <div class="owner-extremes">
            ${renderExtreme(bestLbl, ownerCard.best_trade, "best", "No winning trades in this view.")}
            ${renderExtreme(worstLbl, ownerCard.worst_trade, "worst", "No losing trades in this view.")}
          </div>
        </div>
      `;
      document.getElementById("btnClearTradeOwner").addEventListener("click", () => {
        ownSel.value = "";
        draw();
      });
    } else {
      repEl.style.display = "none";
      repEl.innerHTML = "";
    }

    // 3. Filter & Sort Trades
    let list = (bucket.trades || []).filter(t => {
      if (yrFilter && String(t.season) !== yrFilter) return false;
      if (ownFilter && t.owner_a !== ownFilter && t.owner_b !== ownFilter) return false;
      const ld = t[lens];
      if (cat === "playoffs") {
        return ld.win_playoff_vorp >= 10 || ld.tags.some(x => x.includes("Title") || x.includes("Finals") || x.includes("Playoff"));
      }
      if (cat === "deadline") return t.effective_week >= 8;
      if (cat === "win_win") {
        return Math.min(ld.a_vorp, ld.b_vorp) >= 12 && ld.abs_swing <= 18;
      }
      if (cat === "lose_lose") {
        return Math.max(ld.a_vorp, ld.b_vorp) <= 8;
      }
      return true;
    });

    if (cat === "recent") {
      list.sort((a, b) => (b.season - a.season) || (b.trade_week - a.trade_week));
    } else if (cat === "playoffs") {
      list.sort((a, b) => (b[lens].win_playoff_vorp - a[lens].win_playoff_vorp) || (b[lens].abs_swing - a[lens].abs_swing));
    } else if (cat === "win_win") {
      list.sort((a, b) => (Math.min(b[lens].a_vorp, b[lens].b_vorp) - Math.min(a[lens].a_vorp, a[lens].b_vorp)));
    } else if (cat === "lose_lose") {
      list.sort((a, b) => (a[lens].a_vorp + a[lens].b_vorp) - (b[lens].a_vorp + b[lens].b_vorp));
    } else {
      list.sort((a, b) => (b[lens].abs_swing - a[lens].abs_swing) || (b.season - a.season));
    }

    document.getElementById("tradeCountPill").textContent =
      `Showing ${Math.min(list.length, 30)} of ${list.length} trade${list.length === 1 ? "" : "s"}`;

    const listEl = document.getElementById("tradeList");
    if (!list.length) {
      listEl.innerHTML = `<div class="card"><p class="muted">No trades match this filter combination.</p></div>`;
      return;
    }

    listEl.innerHTML = list.slice(0, 30).map((t, idx) => {
      const ld = t[lens];
      const aV = ld.a_vorp;
      const bV = ld.b_vorp;
      const isPush = ld.winner === "PUSH";
      const aWon = ld.winner === t.owner_a;
      const bWon = ld.winner === t.owner_b;

      // Tug-of-war ratio (shift floor so negatives still render cleanly)
      const shift = Math.min(0, aV, bV);
      const adjA = Math.max(2, aV - shift + 4);
      const adjB = Math.max(2, bV - shift + 4);
      const pctA = Math.round((adjA / (adjA + adjB)) * 100);

      const minSideVorp = Math.min(aV, bV);
      let verb = "leads";
      if (TRADE_ERA !== "live") {
        if (ld.abs_swing >= 45 && minSideVorp < 25) verb = "fleeced";
        else if (ld.abs_swing >= 45) verb = "crushed";
        else if (ld.abs_swing >= 25) verb = "won over";
        else if (minSideVorp >= 18 && ld.abs_swing < 18) verb = "edged (Win-Win) vs.";
        else verb = "edged";
      } else {
        if (ld.abs_swing >= 45 && minSideVorp < 20) verb = "is fleecing";
        else if (ld.abs_swing >= 20) verb = "leads";
        else verb = "edges";
      }
      const headline = isPush
        ? `Even Swap (${fmtVorp(ld.abs_swing)} VORP diff)`
        : `${displayName(ld.winner)} ${verb} ${displayName(ld.loser)}`;

      const renderSide = (owner, players, vorp, outcome, gamesSwung, won, lost) => {
        const cls = won ? "side-win" : (lost ? "side-lose" : "");
        const col = vorp > 2 ? "var(--green)" : (vorp < -2 ? "var(--red)" : "var(--muted)");
        const metaBits = [];
        if (outcome && outcome.is_complete) {
          if (outcome.won_champ) metaBits.push("Won Championship");
          else if (outcome.runner_up) metaBits.push("Super Bowl Runner-Up");
          else if (outcome.made_playoffs) metaBits.push("Made Playoffs");
        }
        if (gamesSwung > 0 && lens === "started") metaBits.push(`+${gamesSwung}W swung`);

        const pRows = players.map(p => {
          const pV = lens === "started" ? p.started_vorp : p.ros_vorp;
          const pWks = lens === "started" ? p.started_wks : p.ros_wks;
          const pPts = lens === "started" ? p.started_pts : p.ros_pts;
          const pPpg = lens === "started" ? p.started_ppg : p.ros_ppg;
          const pPo = lens === "started" ? p.po_started_vorp : p.po_ros_vorp;
          const pCol = pV > 2 ? "var(--green)" : (pV < -2 ? "var(--red)" : "var(--muted)");
          const wkLbl = lens === "started" ? `${pWks} st` : `${pWks} gm`;
          const flipHtml = p.flipped
            ? `<span class="flip-note">&#8627; Flipped Wk ${p.flipped.week} &rarr; ${shortName(p.flipped.to)}</span>`
            : "";
          const poHtml = pPo >= 8 ? ` <span style="color:var(--gold)">(PO ${fmtVorp(pPo)})</span>` : "";
          return `
            <div class="trade-player-row">
              <div>
                <span class="pos-chip">${p.pos}</span>
                <span class="trade-player-name">${p.name}</span>${poHtml}
                ${flipHtml}
              </div>
              <div class="trade-player-stat">
                ${wkLbl} &bull; ${pPts} pts (${pPpg}) &bull; <strong style="color:${pCol}">${fmtVorp(pV)}</strong>
              </div>
            </div>
          `;
        }).join("");

        return `
          <div class="trade-side ${cls}">
            <div class="trade-side-head">
              <div>
                <span class="trade-side-owner">${displayName(owner)} received</span>
                ${metaBits.length ? `<span class="tie-note">${metaBits.join(" • ")}</span>` : ""}
              </div>
              <span class="trade-side-score" style="color:${col}">${fmtVorp(vorp)} VORP</span>
            </div>
            ${pRows}
          </div>
        `;
      };

      const tagsHtml = (ld.tags || []).map(tg => `<span class="tag-pill ${tagClass(tg)}">${tg}</span>`).join("");

      return `
        <div class="trade-card">
          <div class="trade-card-head">
            <div class="trade-meta-left">
              <span class="trade-rank">#${idx + 1}</span>
              <span class="pill">${t.season} &bull; Wk ${t.trade_week}</span>
              <span class="trade-headline">${headline}</span>
            </div>
            <span class="pill ${isPush ? "" : "pill-green"}">${isPush ? "PUSH" : `+${ld.abs_swing.toFixed(1)} VORP Swing`}</span>
          </div>
          ${tagsHtml ? `<div class="trade-tags">${tagsHtml}</div>` : ""}
          <div class="tug-wrap" title="${displayName(t.owner_a)} (${fmtVorp(aV)}) vs ${displayName(t.owner_b)} (${fmtVorp(bV)})">
            <div class="tug-a" style="width:${pctA}%"></div>
            <div class="tug-b" style="width:${100 - pctA}%"></div>
          </div>
          <div class="trade-sides">
            ${renderSide(t.owner_a, t.a_received, aV, t.a_outcome, t.a_games_swung, aWon, bWon)}
            ${renderSide(t.owner_b, t.b_received, bV, t.b_outcome, t.b_games_swung, bWon, aWon)}
          </div>
        </div>
      `;
    }).join("");
  };

  // Wire era & lens segmented controls
  document.querySelectorAll("#tradeEraGroup .seg-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#tradeEraGroup .seg-btn").forEach(b => b.classList.remove("active", "active-gold"));
      TRADE_ERA = btn.dataset.era;
      btn.classList.add(TRADE_ERA === "live" ? "active-gold" : "active");
      syncSeasonSelect();
      draw();
    });
  });

  document.querySelectorAll("#tradeLensGroup .seg-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#tradeLensGroup .seg-btn").forEach(b => b.classList.remove("active"));
      TRADE_LENS = btn.dataset.lens;
      btn.classList.add("active");
      draw();
    });
  });

  catSel.addEventListener("change", draw);
  yrSel.addEventListener("change", draw);
  ownSel.addEventListener("change", draw);

  syncSeasonSelect();
  draw();
}

/* ---------- RENDER PODCAST & AWARDS ---------- */

function renderPodcastAndAwards() {
  document.getElementById("rssLink").href = HUB.podcast.rss_url;
  const pList = document.getElementById("podcastList");
  const eps = HUB.podcast.episodes || [];
  if (!eps.length) {
    pList.innerHTML = `<p class="muted">No episodes published yet.</p>`;
  } else {
    pList.innerHTML = eps.map(e => `
      <div class="ep-card">
        <div class="ep-title">${e.title}</div>
        <div class="ep-meta">${e.pub_date}${e.duration ? ` &bull; ${e.duration}` : ""}</div>
        <audio controls preload="none" src="${e.audio_url}"></audio>
      </div>
    `).join("");
  }

  const aw = HUB.awards;
  if (aw && aw.awards) {
    document.getElementById("awardsTitle").textContent = `Week ${aw.week} Awards & Briefing`;
    document.getElementById("awardsAvg").textContent = `League Avg: ${aw.league_avg.toFixed(1)} pts`;
    document.getElementById("awardsList").innerHTML = aw.awards.map(a => {
      const cleanBody = a.body
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/`(.+?)`/g, "<code>$1</code>");
      return `
        <div class="award-card">
          <div class="award-head">
            <span class="award-title">${a.title}</span>
            <span class="pill pill-blue">${a.metric}</span>
          </div>
          <div class="award-body">${cleanBody}</div>
        </div>
      `;
    }).join("");
  }
}

/* ---------- BOOTSTRAP ---------- */

async function init() {
  const resp = await fetch("hub_data.json", { cache: "no-store" });
  HUB = await resp.json();

  document.getElementById("hubSubtitle").textContent =
    `Season ${HUB.season} • Live through Week ${HUB.completed_week} • 16 Teams • 18-Year History`;
  document.getElementById("footerTimestamp").textContent =
    `Updated ${new Date(HUB.generated_at).toLocaleString()}`;

  // Tab navigation
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    });
  });

  // Playoff Machine presets & scoring model controls
  document.getElementById("modelModeSelect").addEventListener("change", (e) => {
    MODEL_MODE = e.target.value;
    renderPlayoffMachine();
  });
  document.getElementById("btnToggleScores").addEventListener("click", (e) => {
    EDIT_SCORES_OPEN = !EDIT_SCORES_OPEN;
    e.target.textContent = EDIT_SCORES_OPEN ? "Hide Score Editor" : "Edit Scores (PF Tiebreakers)";
    renderPlayoffMachine();
  });
  document.getElementById("btnResetPicks").addEventListener("click", () => {
    for (const k of Object.keys(LOCKED_PICKS)) delete LOCKED_PICKS[k];
    for (const k of Object.keys(CUSTOM_SCORES)) delete CUSTOM_SCORES[k];
    renderPlayoffMachine();
  });
  document.getElementById("btnPickFavs").addEventListener("click", () => {
    const pwr = Object.fromEntries(HUB.teams.map(t => [t.owner, { mean: t.power_mean, std: t.power_std }]));
    for (const k of Object.keys(CUSTOM_SCORES)) delete CUSTOM_SCORES[k];
    for (const g of HUB.remaining_schedule) {
      const mA = getProjectedMeanForWeek(g.away, g.week, pwr);
      const mH = getProjectedMeanForWeek(g.home, g.week, pwr);
      LOCKED_PICKS[gameKey(g)] = (mA >= mH) ? g.away : g.home;
    }
    renderPlayoffMachine();
  });
  document.getElementById("btnPickDogs").addEventListener("click", () => {
    const pwr = Object.fromEntries(HUB.teams.map(t => [t.owner, { mean: t.power_mean, std: t.power_std }]));
    for (const k of Object.keys(CUSTOM_SCORES)) delete CUSTOM_SCORES[k];
    for (const g of HUB.remaining_schedule) {
      const mA = getProjectedMeanForWeek(g.away, g.week, pwr);
      const mH = getProjectedMeanForWeek(g.home, g.week, pwr);
      LOCKED_PICKS[gameKey(g)] = (mA < mH) ? g.away : g.home;
    }
    renderPlayoffMachine();
  });

  renderPlayoffMachine();
  renderHistoryTab();
  renderDraftsTab();
  renderTradesTab();
  renderPodcastAndAwards();
}

init().catch(err => {
  document.getElementById("hubSubtitle").textContent = "Failed to load league data: " + err.message;
});
