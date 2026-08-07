// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Channel picker — fetches all stations + renders tiles
//      Polls /api/stations every 12s so cards reflect what's live now.
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
const CFG = window.STUDIOAI_CONFIG ?? {};
const POLL_MS = 12_000;
const grid = document.getElementById('picker-grid');

let stationsCache = [];   // last successful list — survives transient errors

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Fetch the station list + per-station now-playing
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
async function fetchStations() {
  try {
    const res = await fetch('/api/stations', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const list = await res.json();
    if (!Array.isArray(list)) return;
    stationsCache = list.filter((s) => s.is_public !== false && s.shortcode);
    renderGrid(stationsCache);
    // Then enrich each card with now-playing in parallel
    stationsCache.forEach(updateCardNowPlaying);
  } catch (err) {
    if (stationsCache.length === 0) {
      grid.innerHTML =
        '<p class="picker-loading">Could not reach the radio right now. Please try again.</p>';
    }
    console.warn('[picker] fetch failed:', err.message);
  }
}

async function updateCardNowPlaying(station) {
  try {
    const url =
      `${CFG.azuracastBase}/api/nowplaying/${encodeURIComponent(station.shortcode)}`;
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) return;
    const data = await res.json();
    applyNowPlayingToCard(station.shortcode, data);
  } catch (err) {
    /* leave card with placeholder — transient errors are fine */
  }
}

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: DOM rendering
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
function renderGrid(stations) {
  if (!stations.length) {
    grid.innerHTML =
      '<p class="picker-loading">No public channels yet. Check back soon.</p>';
    return;
  }

  // Only re-render if the set of stations changed (avoids flickering)
  const currentSig = Array.from(grid.querySelectorAll('[data-shortcode]'))
    .map((el) => el.dataset.shortcode)
    .join('|');
  const nextSig = stations.map((s) => s.shortcode).join('|');
  if (currentSig === nextSig) return;

  grid.innerHTML = stations.map(renderCard).join('');
}

function renderCard(station) {
  const safeName = escape(station.name || station.shortcode);
  const safeDesc = escape(station.description || '');
  const link = `/${encodeURIComponent(station.shortcode)}`;
  return `
    <a class="channel-card"
       data-shortcode="${escape(station.shortcode)}"
       href="${link}"
       data-online="false"
       aria-label="Open ${safeName}">
      <span class="channel-card__live">LIVE</span>
      <h2 class="channel-card__name">${safeName}</h2>
      <p class="channel-card__nowplaying" data-role="nowplaying">${safeDesc || '&nbsp;'}</p>
      <div class="channel-card__row">
        <span class="channel-card__listeners">
          <span class="channel-card__listeners-dot" aria-hidden="true"></span>
          <span data-role="listeners">—</span> listening
        </span>
        <span class="channel-card__cta">TUNE IN</span>
      </div>
    </a>
  `;
}

function applyNowPlayingToCard(shortcode, data) {
  const card = grid.querySelector(`[data-shortcode="${cssEscape(shortcode)}"]`);
  if (!card) return;

  const isOnline  = !!data?.is_online;
  const listeners = data?.listeners?.current;
  const song      = data?.now_playing?.song || {};
  const title     = song.title || song.text || '—';
  const artist    = song.artist || '';

  card.dataset.online = String(isOnline);

  const npEl = card.querySelector('[data-role="nowplaying"]');
  if (npEl) {
    npEl.textContent = isOnline
      ? (artist ? `${artist} — ${title}` : title)
      : 'Off-air';
  }

  const listenersEl = card.querySelector('[data-role="listeners"]');
  if (listenersEl && typeof listeners === 'number') {
    listenersEl.textContent = String(listeners);
  }
}

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Tiny helpers
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
function escape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function cssEscape(s) {
  // Escape so a shortcode is safe in a [attribute=...] CSS selector
  return CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/"/g, '\\"');
}

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Boot — initial fetch + interval polling
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
fetchStations();
setInterval(fetchStations, POLL_MS);
