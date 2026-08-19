const CDN = "https://cdn.cloudflare.steamstatic.com";

let SNAP = null;
let roleFilter = "all";
let searchText = "";

const $ = (sel) => document.querySelector(sel);

function heroImage(img) {
  if (!img) return "";
  return img.startsWith("http") ? img : CDN + img;
}

function componentBars(components) {
  const entries = Object.entries(components || {}).filter(([, v]) => v !== 0);
  if (!entries.length) return "";
  const maxAbs = Math.max(...entries.map(([, v]) => Math.abs(v)), 1);
  return `<div class="score-bars">${entries.map(([key, value]) => {
    const label = key.replaceAll("_", " ");
    const pct = Math.min(100, Math.abs(value) / maxAbs * 100);
    const cls = value >= 0 ? "positive" : "negative";
    return `<div class="score-bar-row">
      <span class="bar-label">${label}</span>
      <span class="bar-track"><span class="bar-fill ${cls}" style="width:${pct}%"></span></span>
      <span>${value >= 0 ? "+" : ""}${value.toFixed(1)}</span>
    </div>`;
  }).join("")}</div>`;
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.hidden = true; }, 2600);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function postAction(path, body) {
  try {
    SNAP = await api(path, { method: "POST", body: JSON.stringify(body || {}) });
    render();
  } catch (err) {
    toast(err.message);
  }
}

function heroById(id) {
  return SNAP.heroes.find(h => h.id === id);
}

function teamHTML(side) {
  const team = SNAP[side];
  const picks = team.picks;
  const slots = [];
  for (let i = 0; i < 5; i++) {
    if (i < picks.length) {
      const pick = picks[i];
      const lineup = SNAP.lineups[side];
      const pos = lineup && lineup.positions.find(p => p.hero_id === pick.id);
      slots.push(`
        <div class="slot filled ${side}">
          <img src="${heroImage(pick.img)}" alt="">
          <span class="slot-name">${pick.name}</span>
          ${pos ? `<span class="pos">P${pos.position}</span>` : ""}
        </div>`);
    } else {
      slots.push(`<div class="slot ${side}"></div>`);
    }
  }
  const bans = team.bans.map(b => `
    <span class="ban-chip"><img src="${heroImage(b.img)}" alt="">${b.name}</span>
  `).join("");
  return `
    <h3>${side === "radiant" ? "Radiant" : "Dire"}${SNAP.first_pick_side === side ? " · first pick" : ""}</h3>
    <div class="pick-slots">${slots.join("")}</div>
    <div class="ban-label">Bans (${team.bans.length}/7)</div>
    <div class="bans">${bans || '<span class="muted">none</span>'}</div>`;
}

function renderTurnBanner() {
  const el = $("#turn-banner");
  if (!SNAP.turn) {
    el.textContent = "Draft complete";
    el.className = "turn-banner done";
    return;
  }
  el.textContent = `${SNAP.step + 1}/${SNAP.total} — ${SNAP.turn.side.toUpperCase()} ${SNAP.turn.action.toUpperCase()} · Phase ${SNAP.turn.phase}`;
  el.className = `turn-banner ${SNAP.turn.side}`;
  $("#draft-meta").textContent = `${SNAP.first_pick_side} first pick · step ${SNAP.step}/${SNAP.total}`;
}

function renderSuggestions() {
  const sug = SNAP.suggestions;
  const box = $("#suggestion-list");
  if (!sug.items.length) {
    box.innerHTML = '<div class="muted">Draft complete — no more suggestions.</div>';
    return;
  }
  $("#suggestions-title").textContent = `Suggested ${sug.action}s for ${sug.side.toUpperCase()}`;
  box.innerHTML = sug.items.map(s => `
    <div class="suggestion-card" data-hero="${s.id}">
      <div class="hero-row">
        <img src="${heroImage(s.img)}" alt="" style="width:56px;height:32px;object-fit:cover;border-radius:5px">
        <strong>${s.name}</strong>
        <span class="score">${s.score.toFixed(1)}</span>
      </div>
      <ul>${s.reasons.map(r => `<li>${r}</li>`).join("")}</ul>
      ${componentBars(s.components)}
    </div>`).join("");
  box.querySelectorAll(".suggestion-card").forEach(card => {
    card.addEventListener("click", () => applyHero(Number(card.dataset.hero)));
  });
}

function renderTimeline() {
  $("#step-label").textContent = `${SNAP.step}/${SNAP.total}`;
  $("#order-list").innerHTML = SNAP.order.map(t => {
    const cls = [
      "order-cell",
      t.done ? `done ${t.side}` : "",
      t.index === SNAP.step ? "current" : "",
      t.phase === 3 ? "phase3" : "",
    ].join(" ");
    return `<div class="${cls}" title="Step ${t.index + 1}: ${t.side} ${t.action} phase ${t.phase}">${t.index + 1}</div>`;
  }).join("");
}

function renderLineups() {
  const box = $("#lineups");
  const cards = [];
  for (const side of ["radiant", "dire"]) {
    const lineup = SNAP.lineups[side];
    if (!lineup) continue;
    cards.push(`
      <div class="lineup-card ${side}">
        <h3 style="color:${side === "radiant" ? "var(--radiant)" : "var(--dire)"}">${side.toUpperCase()} lineup</h3>
        ${lineup.positions.map(p => `
          <div class="lineup-row">
            <span class="pos">P${p.position}</span>
            <img src="${heroImage(p.img)}" alt="">
            <strong>${p.name}</strong>
            <span class="role-tag">${p.role}</span>
          </div>`).join("")}
        <div class="lineup-summary">${lineup.cores} core · ${lineup.supports} support</div>
      </div>`);
  }
  box.innerHTML = cards.join("");
}

function usedState(id) {
  if (!SNAP) return null;
  for (const side of ["radiant", "dire"]) {
    if (SNAP[side].picks.some(h => h.id === id)) return { side, action: "picked" };
    if (SNAP[side].bans.some(h => h.id === id)) return { side, action: "banned" };
  }
  return null;
}

function renderHeroes() {
  const grid = $("#hero-grid");
  const q = searchText.toLowerCase();
  let heroes = SNAP.heroes.filter(h => h.cm_enabled);
  if (roleFilter !== "all") heroes = heroes.filter(h => h.role === roleFilter);
  if (q) heroes = heroes.filter(h => h.name.toLowerCase().includes(q));

  grid.innerHTML = heroes.map(h => {
    const used = usedState(h.id);
    const attrClass = h.attr === "all" ? "attr-all" : `attr-${h.attr}`;
    return `
      <div class="hero-card ${used ? "used" : ""}" data-hero="${h.id}">
        ${used ? `<span class="used-label ${used.side}">${used.action.toUpperCase()}</span>` : ""}
        <button class="hero-info-btn" data-info="${h.id}" title="Analysis">ⓘ</button>
        <img loading="lazy" src="${heroImage(h.img)}" alt="">
        <div class="hname ${attrClass}" title="${h.name}">${h.name}</div>
        <div class="hrole">${h.role}</div>
        <div class="pos-mini">
          ${h.pos_probs.map(p => `<span><span class="pfill" style="height:${Math.min(100, p)}%"></span></span>`).join("")}
        </div>
        <div class="roleline">core ${h.core_pct}% / sup ${h.support_pct}%</div>
      </div>`;
  }).join("");

  grid.querySelectorAll(".hero-card:not(.used)").forEach(card => {
    card.addEventListener("click", () => applyHero(Number(card.dataset.hero)));
  });
  grid.querySelectorAll(".hero-info-btn").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      openHeroModal(Number(btn.dataset.info));
    });
  });
}

async function openHeroModal(heroId) {
  const modal = $("#hero-modal");
  modal.hidden = false;
  $("#modal-content").innerHTML = "<div class='muted'>Loading analysis...</div>";
  try {
    const data = await api(`/api/heroes/${heroId}/analysis`);
    renderHeroModal(data);
  } catch (err) {
    $("#modal-content").innerHTML = `<div class="muted">${err.message}</div>`;
  }
}

function closeHeroModal() {
  $("#hero-modal").hidden = true;
}

function renderHeroModal(d) {
  const posBars = d.pos_probs.map((p, i) => `
    <div class="row">
      <span>Position ${i + 1}</span>
      <span style="display:inline-flex;align-items:center;gap:6px">
        <span class="bar-track" style="width:120px;height:8px;display:inline-block;background:#0d1017;border-radius:4px;overflow:hidden">
          <span class="bar-fill positive" style="display:block;height:100%;width:${p}%"></span>
        </span>${p}%
      </span>
    </div>`).join("");

  const matchups = d.matchups.length
    ? d.matchups.map(m => `<div class="row">
        <span>vs ${m.enemy}</span>
        <span>${(m.winrate * 100).toFixed(1)}% <small>(${m.games} games)</small></span>
      </div>`).join("")
    : '<div class="muted">No enemy heroes revealed yet.</div>';

  const synergies = d.synergies.length
    ? d.synergies.map(x => `<div class="row">
        <span>with ${x.ally}</span>
        <span>${(x.winrate * 100).toFixed(1)}% <small>(${x.games} games)</small></span>
      </div>`).join("")
    : '<div class="muted">No allies revealed or synergy data not loaded.</div>';

  const used = d.availability !== "available";
  const applyDisabled = used || SNAP.done;
  const shapeWarning = d.action === "pick" && d.shape_valid === false
    ? '<div class="muted" style="color:var(--dire)">⚠ This pick would make a 3-core / 2-support lineup impossible.</div>'
    : "";

  $("#modal-content").innerHTML = `
    <div class="modal-hero-head">
      <img src="${heroImage(d.img)}" alt="">
      <div>
        <h2>${d.name}</h2>
        <div class="sub">${d.role.toUpperCase()} · core ${d.core_pct}% / support ${d.support_pct}%</div>
        <div class="sub">Score ${d.score.toFixed(1)} · ${d.meta.pro_ban} pro bans · ${d.meta.pro_pick} pro picks</div>
      </div>
    </div>
    ${shapeWarning}
    <div class="modal-grid">
      <div class="modal-section">
        <h3>Position model (Dirichlet-smoothed)</h3>
        ${posBars}
      </div>
      <div class="modal-section">
        <h3>Score breakdown (${d.action})</h3>
        ${componentBars(d.components)}
        ${d.reasons.length ? `<ul style="margin:8px 0 0 16px;padding:0;font-size:12px">${d.reasons.map(r => `<li>${r}</li>`).join("")}</ul>` : ""}
      </div>
      <div class="modal-section">
        <h3>Matchup vs revealed enemies (shrunk)</h3>
        ${matchups}
      </div>
      <div class="modal-section">
        <h3>Synergy with revealed allies</h3>
        ${synergies}
      </div>
    </div>
    <div class="modal-actions">
      <button class="btn" id="modal-apply" ${applyDisabled ? "disabled" : ""}>Apply ${d.action}</button>
      <button class="btn btn-ghost" id="modal-cancel">Close</button>
    </div>`;
  $("#modal-apply").addEventListener("click", () => {
    closeHeroModal();
    applyHero(d.hero_id);
  });
  $("#modal-cancel").addEventListener("click", closeHeroModal);
}

function applyHero(heroId) {
  if (SNAP.done) return;
  postAction("/api/action", { hero_id: heroId });
}

function render() {
  renderTurnBanner();
  renderSuggestions();
  renderTimeline();
  $("#radiant-team").innerHTML = teamHTML("radiant");
  $("#dire-team").innerHTML = teamHTML("dire");
  renderLineups();
  renderHeroes();
  $("#undo-btn").disabled = SNAP.step === 0;
  $("#auto-btn").disabled = SNAP.done;
  $("#reset-btn").disabled = SNAP.step === 0;
  renderEngineStatus();
}

function renderEngineStatus() {
  const e = SNAP.engine;
  $("#engine-status").innerHTML =
    `<b>${e.mode === "beam_search" ? "beam-search lookahead" : "greedy scorer"}</b>` +
    (e.mode === "beam_search" ? ` · depth ${e.lookahead_depth} · beam ${e.beam_width}` : "") +
    ` · ${e.synergy_enabled ? `${e.synergy_pairs} synergy pairs` : "synergy off"}` +
    `<br>Bayesian shrinkage: (wins + ${e.pseudo_count}×0.5) / (games + ${e.pseudo_count})` +
    ` · role threshold ${e.role_min_prob}`;
}

async function init() {
  SNAP = await api("/api/state");
  render();
  // Defensive: the modal must never be visible before the user opens it.
  const modal = $("#hero-modal");
  modal.hidden = true;
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeHeroModal();
  });

  $("#hero-search").addEventListener("input", e => {
    searchText = e.target.value;
    renderHeroes();
  });
  document.querySelectorAll(".role-filters .chip").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".role-filters .chip").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      roleFilter = btn.dataset.role;
      renderHeroes();
    });
  });
  $("#auto-btn").addEventListener("click", () => postAction("/api/auto"));
  $("#undo-btn").addEventListener("click", () => postAction("/api/undo"));
  $("#reset-btn").addEventListener("click", () => {
    if (confirm("Reset the whole draft?")) postAction("/api/reset");
  });
  $("#modal-close").addEventListener("click", closeHeroModal);
  $("#hero-modal").addEventListener("click", e => {
    if (e.target === $("#hero-modal")) closeHeroModal();
  });
}

init().catch(err => {
  toast(`Failed to load draft state: ${err.message}`);
});
