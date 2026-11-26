/* BFL League Hub + Interactive Playoff Machine (Exact BFL Tiebreaker & Live Monte Carlo Engine) */

let HUB = null;
const LOCKED_PICKS = {}; // key: `${week}|${away}|${home}` -> winnerOwner

const DIV_ORDER = ["North", "South", "East", "West"];

function gameKey(g) {
  return `${g.week}|${g.away}|${g.home}`;
}

function shortName(owner) {
  if (!owner) return "";
  if (owner === "Shawn Lukose") return "Lukose";
  if (owner === "Shawn Ullenbrauck") return "Thor";
  if (owner === "Adam Olen") return "AMO";
  if (owner === "rej hoxha") return "Ray";
  return owner.split(" ")[0];
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
    const lockedWinner = LOCKED_PICKS[k];
    if (lockedWinner) {
      // Award deterministic average-power scores preserving the chosen winner
      const mA = power[g.away].mean;
      const mH = power[g.home].mean;
      const hi = Math.max(mA, mH) + 4.0;
      const lo = Math.min(mA, mH) - 4.0;
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
      const aPts = power[g.away].mean + gaussianRand() * power[g.away].std;
      const hPts = power[g.home].mean + gaussianRand() * power[g.home].std;
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
  if (lockedCount === 0) {
    banner.innerHTML = `<strong>Live Standings through Week ${HUB.completed_week}:</strong> ${totalRem} regular-season games remaining (Weeks ${HUB.completed_week + 1}–14). Click any team below to simulate scenarios instantly.`;
  } else if (unlockedCount === 0) {
    banner.innerHTML = `<strong>Full Season Locked (${lockedCount}/${totalRem} games picked):</strong> Exact deterministic Week 15 BFL Playoff Bracket computed with official H2H &amp; Points-For tiebreakers.`;
  } else {
    banner.innerHTML = `<strong>Custom Scenario Active:</strong> ${lockedCount} of ${totalRem} remaining games locked &bull; Simulating remaining ${unlockedCount} games across ${iters.toLocaleString()} live Monte Carlo runs.`;
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
        <td><strong>${own}</strong><span class="tie-note">${roleTag}</span></td>
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
        <td><strong>${own}</strong></td>
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

  // Interactive Schedule Picker grouped by week
  const byWeek = {};
  for (const g of HUB.remaining_schedule) {
    if (!byWeek[g.week]) byWeek[g.week] = [];
    byWeek[g.week].push(g);
  }
  const schedEl = document.getElementById("schedulePicker");
  schedEl.innerHTML = Object.keys(byWeek).map(Number).sort((a, b) => a - b).map(wk => {
    const gamesHtml = byWeek[wk].map(g => {
      const k = gameKey(g);
      const picked = LOCKED_PICKS[k] || "";
      const aCls = picked === g.away ? "picked-win" : (picked === g.home ? "picked-loss" : "");
      const hCls = picked === g.home ? "picked-win" : (picked === g.away ? "picked-loss" : "");
      const aRec = `${records[g.away].wins}-${records[g.away].losses}`;
      const hRec = `${records[g.home].wins}-${records[g.home].losses}`;
      return `
        <div class="match-row">
          <button class="pick-btn ${aCls}" data-key="${k}" data-win="${g.away}">
            <span>${shortName(g.away)}</span><span class="pick-rec">${aRec}</span>
          </button>
          <span class="at-sep">@</span>
          <button class="pick-btn ${hCls}" data-key="${k}" data-win="${g.home}">
            <span>${shortName(g.home)}</span><span class="pick-rec">${hRec}</span>
          </button>
        </div>
      `;
    }).join("");
    return `<div class="week-col"><h4><span>Week ${wk}</span><span class="muted small">${wk === 14 ? "Regular Season Finale" : "Click winner"}</span></h4>${gamesHtml}</div>`;
  }).join("");

  schedEl.querySelectorAll(".pick-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const k = btn.dataset.key;
      const w = btn.dataset.win;
      if (LOCKED_PICKS[k] === w) delete LOCKED_PICKS[k];
      else LOCKED_PICKS[k] = w;
      renderPlayoffMachine();
    });
  });

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
          <td><strong>${own}</strong> ${inPo ? `<span class="pill pill-green">#${seedNum}</span>` : ""}</td>
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
  sel1.innerHTML = owners.map(o => `<option value="${o}">${o}</option>`).join("");
  sel2.innerHTML = owners.map(o => `<option value="${o}">${o}</option>`).join("");
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
    const headTxt = wA > wB ? `<strong>${a}</strong> leads ${wA}–${wB}` : (wB > wA ? `<strong>${b}</strong> leads ${wB}–${wA}` : `Series deadlocked <strong>${wA}–${wB}</strong>`);
    const sbTxt = rec.super_bowls.length
      ? rec.super_bowls.map(s => `${s.year}: ${s.champ} def. ${s.runner_up} (${s.score})`).join(" • ")
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
      <td><strong>${c.owner}</strong><span class="tie-note">${c.division} &bull; ${c.seasons} yrs</span></td>
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
      <td><strong>${s.champ}</strong></td>
      <td>${s.runner_up}</td>
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
  oSel.innerHTML = `<option value="">All Managers</option>` + HUB.teams.map(t => `<option value="${t.owner}">${t.owner}</option>`).join("");

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

  // Playoff Machine presets
  document.getElementById("btnResetPicks").addEventListener("click", () => {
    for (const k of Object.keys(LOCKED_PICKS)) delete LOCKED_PICKS[k];
    renderPlayoffMachine();
  });
  document.getElementById("btnPickFavs").addEventListener("click", () => {
    const pwr = Object.fromEntries(HUB.teams.map(t => [t.owner, t.power_mean]));
    for (const g of HUB.remaining_schedule) {
      LOCKED_PICKS[gameKey(g)] = (pwr[g.away] >= pwr[g.home]) ? g.away : g.home;
    }
    renderPlayoffMachine();
  });
  document.getElementById("btnPickDogs").addEventListener("click", () => {
    const pwr = Object.fromEntries(HUB.teams.map(t => [t.owner, t.power_mean]));
    for (const g of HUB.remaining_schedule) {
      LOCKED_PICKS[gameKey(g)] = (pwr[g.away] < pwr[g.home]) ? g.away : g.home;
    }
    renderPlayoffMachine();
  });

  renderPlayoffMachine();
  renderHistoryTab();
  renderDraftsTab();
  renderPodcastAndAwards();
}

init().catch(err => {
  document.getElementById("hubSubtitle").textContent = "Failed to load league data: " + err.message;
});
