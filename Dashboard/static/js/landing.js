// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      Stardust Radio — Landing Page
//      Fetches the station list, then for each station hits AzuraCast's
//      nowplaying endpoint to find what's currently broadcasting and
//      renders compact cards in the "Now Broadcasting" strip. Refreshes
//      every 30 seconds so the page feels alive.
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯

const CFG = window.STUDIOAI_CONFIG ?? {};
const REFRESH_INTERVAL_MS = 30_000;
const PLACEHOLDER_ART = '/static/assets/placeholder-art.svg';

const els = {
  list: document.getElementById('broadcasts-list'),
};

// Track the last rendered signature so we don't thrash the DOM when nothing changed
let lastRenderSig = '';

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Stations + per-station nowplaying fetch
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
async function fetchStations() {
  const res = await fetch('/api/stations', { cache: 'no-store' });
  if (!res.ok) throw new Error(`stations ${res.status}`);
  const data = await res.json();
  return Array.isArray(data) ? data : [];
}

async function fetchNowPlaying(shortcode) {
  if (!CFG.azuracastBase) return null;
  try {
    const res = await fetch(
      `${CFG.azuracastBase}/api/nowplaying/${encodeURIComponent(shortcode)}`,
      { cache: 'no-store' }
    );
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Render the broadcasts list
//      Only stations that are currently online + actively playing a song
//      get a card. Empty list → friendly "nobody's on right now" message.
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function renderBroadcasts(activeBroadcasts) {
  if (!els.list) return;

  if (activeBroadcasts.length === 0) {
    const sig = 'empty';
    if (sig === lastRenderSig) return;
    lastRenderSig = sig;
    els.list.innerHTML =
      '<p class="landing-broadcasts__empty">Nobody on the air right now — check back soon.</p>';
    return;
  }

  // Stable signature: if shortcodes + currently-playing IDs haven't changed,
  // skip the re-render to avoid flicker.
  const sig = activeBroadcasts
    .map((b) => `${b.shortcode}:${b.songId}`)
    .join('|');
  if (sig === lastRenderSig) return;
  lastRenderSig = sig;

  els.list.innerHTML = activeBroadcasts
    .map((b) => {
      const channel = escapeHtml(b.name);
      const title   = escapeHtml(b.title);
      const artist  = escapeHtml(b.artist);
      const trackLine = artist ? `${title} · ${artist}` : title;
      return `
        <a class="broadcast-card" href="/${encodeURIComponent(b.shortcode)}"
           aria-label="Listen to ${channel}">
          <img class="broadcast-card__art" src="${escapeHtml(b.art)}" alt="" loading="lazy" />
          <div class="broadcast-card__meta">
            <span class="broadcast-card__channel">${channel}</span>
            <span class="broadcast-card__track">${trackLine}</span>
          </div>
        </a>
      `;
    })
    .join('');
}

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Refresh loop — pulls stations + nowplaying in parallel
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
async function refresh() {
  let stations = [];
  try {
    stations = await fetchStations();
  } catch (err) {
    console.warn('[landing] stations fetch failed:', err.message);
    return;
  }
  if (stations.length === 0) {
    renderBroadcasts([]);
    return;
  }

  // Fetch all nowplaying in parallel — order doesn't matter, we filter after.
  const playingResults = await Promise.all(
    stations.map(async (s) => {
      const np = await fetchNowPlaying(s.shortcode);
      const song = np?.now_playing?.song;
      // Skip stations that aren't actually playing audio right now.
      // (No song = station offline / no media / autoDJ idle.)
      if (!song || !(song.title || song.text)) return null;
      return {
        shortcode: s.shortcode,
        name:      s.name || s.shortcode,
        title:     song.title || song.text || 'Untitled',
        artist:    song.artist || '',
        art:       song.art || PLACEHOLDER_ART,
        songId:    song.id || `${song.title}::${song.artist}`,
      };
    })
  );

  const active = playingResults.filter(Boolean);
  renderBroadcasts(active);
}

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Boot
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
refresh();
setInterval(refresh, REFRESH_INTERVAL_MS);
