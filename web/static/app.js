const CDN = "https://cdn.cloudflare.steamstatic.com";

let SNAP = null;
let roleFilter = "all";
let searchText = "";

const $ = (sel) => document.querySelector(sel);

function heroImage(img) {
  if (!img) return "";
  return img.startsWith("http") ? img : CDN + img;
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
        <img loading="lazy" src="${heroImage(h.img)}" alt="">
        <div class="hname ${attrClass}" title="${h.name}">${h.name}</div>
        <div class="hrole">${h.role} · ${h.roles.slice(0, 2).join("/")}</div>
      </div>`;
  }).join("");

  grid.querySelectorAll(".hero-card:not(.used)").forEach(card => {
    card.addEventListener("click", () => applyHero(Number(card.dataset.hero)));
  });
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
}

async function init() {
  SNAP = await api("/api/state");
  render();

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
}

init().catch(err => {
  toast(`Failed to load draft state: ${err.message}`);
});
