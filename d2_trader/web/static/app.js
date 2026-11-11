// Item search, chart hover, and keeping every page live without a reload.

const POLL_MS = 15000;
// Countdowns ("ends in 2d 3h") move without any data changing, so redraw now and then.
const FORCE_REDRAW_MS = 5 * 60 * 1000;

function setUpItemSearch() {
  const list = document.getElementById("item-options");
  if (!list) return;
  let timer;
  document.querySelectorAll("[data-item-search]").forEach((input) => {
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const query = input.value.trim();
        if (query.length < 2) return;
        const response = await fetch(`/api/items?q=${encodeURIComponent(query)}`);
        if (!response.ok) return;
        const items = await response.json();
        list.replaceChildren(
          ...items.map((item) => {
            const option = document.createElement("option");
            option.value = item.name;
            option.label = item.category;
            return option;
          }),
        );
      }, 150);
    });
  });
}

function setUpChart(wrap) {
  const svg = wrap.querySelector("svg");
  const hit = wrap.querySelector(".hit");
  const cross = wrap.querySelector(".cross");
  const tooltip = wrap.querySelector(".tooltip");
  const rows = JSON.parse(wrap.querySelector("script").textContent);

  const nearest = (x) =>
    rows.reduce((best, row) => (Math.abs(row.x - x) < Math.abs(best.x - x) ? row : best));

  hit.addEventListener("pointermove", (event) => {
    const box = svg.getBoundingClientRect();
    const scale = svg.viewBox.baseVal.width / box.width;
    const row = nearest((event.clientX - box.left) * scale);

    cross.removeAttribute("hidden");
    cross.setAttribute("x1", row.x);
    cross.setAttribute("x2", row.x);

    tooltip.replaceChildren();
    const heading = document.createElement("b");
    heading.textContent = row.day;
    tooltip.append(heading);
    row.lines.forEach((text) => {
      const line = document.createElement("div");
      line.textContent = text;
      tooltip.append(line);
    });

    tooltip.hidden = false;
    const left = row.x / scale;
    const flip = left + tooltip.offsetWidth + 16 > box.width;
    tooltip.style.left = `${flip ? left - tooltip.offsetWidth - 10 : left + 10}px`;
  });

  hit.addEventListener("pointerleave", () => {
    cross.setAttribute("hidden", "");
    tooltip.hidden = true;
  });
}

function setUpPage() {
  setUpItemSearch();
  document.querySelectorAll("[data-chart]").forEach(setUpChart);
  const enable = document.querySelector("[data-enable-browser-alerts]");
  if (enable && "Notification" in window && Notification.permission === "default") {
    enable.hidden = false;
  }
}

// Redrawing must never cost the operator what they are doing on the page.
const activity = { editing: false, hovering: false };

function isBusy() {
  const focused = document.activeElement;
  return (
    activity.editing ||
    activity.hovering ||
    document.querySelector("main details[open]") !== null ||
    (focused !== null && focused.closest("main form") !== null)
  );
}

async function redraw() {
  const response = await fetch(location.href, { headers: { "X-Live": "1" } });
  if (!response.ok) return false;
  const fresh = new DOMParser().parseFromString(await response.text(), "text/html");
  const main = fresh.querySelector("main");
  if (!main) return false;
  document.querySelector("main").replaceChildren(...main.childNodes);
  setUpPage();
  return true;
}

function showStatus(text) {
  const status = document.getElementById("live-status");
  if (status) status.textContent = text;
}

async function fetchLive() {
  try {
    const response = await fetch("/api/live");
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

// The unread badge, and a browser notification for anything new while the tab is open.
function announce(data, state) {
  const badge = document.getElementById("unread");
  badge.textContent = data.count;
  badge.hidden = data.count === 0;
  const isNew = data.latest && state.lastNotice !== null && data.latest.id !== state.lastNotice;
  if (isNew && "Notification" in window && Notification.permission === "granted") {
    new Notification(data.latest.title, { body: data.latest.body });
  }
  state.lastNotice = data.latest ? data.latest.id : (state.lastNotice ?? 0);
}

async function keepCurrent(data, state) {
  if (state.shown === null) state.shown = data.version;
  const stale = Date.now() - state.lastRedraw > FORCE_REDRAW_MS;
  if (data.version === state.shown && !stale) {
    showStatus("Live");
    return;
  }
  if (isBusy()) {
    showStatus(activity.editing ? "Live · paused while you edit" : "Live · update waiting");
    return;
  }
  if (await redraw()) {
    state.shown = data.version;
    state.lastRedraw = Date.now();
    showStatus("Live");
  }
}

function setUpLive() {
  // A flash message belongs to the action that caused it, not to every redraw after it.
  const url = new URL(location.href);
  if (url.searchParams.has("message") || url.searchParams.has("error")) {
    url.searchParams.delete("message");
    url.searchParams.delete("error");
    history.replaceState(null, "", url);
  }

  document.addEventListener("input", (event) => {
    if (event.target.closest("main form")) activity.editing = true;
  });
  document.addEventListener("pointerover", (event) => {
    activity.hovering = event.target.closest("[data-chart]") !== null;
  });
  document.addEventListener("click", async (event) => {
    if (!event.target.closest("[data-enable-browser-alerts]")) return;
    await Notification.requestPermission();
    event.target.hidden = true;
  });

  const state = { shown: null, lastNotice: null, lastRedraw: Date.now() };

  const poll = async () => {
    if (document.hidden) return;
    const data = await fetchLive();
    if (!data) {
      showStatus("Offline");
      return;
    }
    announce(data, state);
    await keepCurrent(data, state);
  };

  poll();
  setInterval(poll, POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });
}

setUpPage();
setUpLive();
