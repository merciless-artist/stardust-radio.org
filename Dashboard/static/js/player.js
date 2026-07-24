// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Config + DOM refs (Stardust Radio listener)
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
const CFG = window.RADIO_CONFIG ?? {};
const POLL_INTERVAL_MS = 8_000;
const PLACEHOLDER_ART = '/static/assets/placeholder-art.svg';

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      Read the active channel shortcode from the URL.
//      /<shortcode>  → that station's listener page.
//      / (root)      → falls back to config default (single-station mode).
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
const SHORTCODE = (() => {
  const path = window.location.pathname.replace(/^\/+|\/+$/g, '');
  return path && !path.includes('/')
    ? decodeURIComponent(path)
    : (CFG.defaultShortcode || SHORTCODE || 'stardust_radio');
})();

const els = {
  body:          document.body,
  audio:         document.getElementById('stream'),
  playToggle:    document.getElementById('play-toggle'),
  volume:        document.getElementById('volume'),
  trackTitle:    document.getElementById('track-title'),
  trackArtist:   document.getElementById('track-artist'),
  albumArt:      document.getElementById('album-art'),
  musicVideo:    document.getElementById('music-video'),
  albumInner:    document.querySelector('.album__inner'),
  listenerCount: document.getElementById('listener-count'),
  lyricsBox:      document.getElementById('lyrics-overlay'),
  lyricsTrack:    document.getElementById('lyrics-track'),
  historyList:    document.getElementById('history-list'),
};

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Apply runtime config from /api/config.js
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
(function applyConfig() {
  if (CFG.stationName) document.title = CFG.stationName;
  els.audio.src = streamUrl();
})();

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Per-channel theme — fetch + apply
//      Channels can override accent color, frame images, etc. Anything
//      not specified for the channel falls back to the default theme.
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
(async function applyTheme() {
  try {
    const res = await fetch(`/api/theme/${encodeURIComponent(SHORTCODE)}`, {
      cache: 'no-store',
    });
    if (!res.ok) return;
    const t = await res.json();
    if (!t || typeof t !== 'object') return;

    // CSS variable overrides (accent color)
    if (t.accent_color) {
      document.documentElement.style.setProperty('--c-accent', t.accent_color);
    }
    if (t.accent_color_soft) {
      document.documentElement.style.setProperty('--c-accent-soft', t.accent_color_soft);
    }

    // Image swaps — only swap if the theme provides a different URL
    const swap = (selector, key) => {
      const url = t[key];
      if (!url) return;
      document.querySelectorAll(selector).forEach((el) => {
        if (el.tagName === 'IMG') el.src = url;
      });
    };
    swap('.title-img--silver',          'title_silver');
    swap('.title-img--gold',            'title_lit');
    swap('.brand-footer__logo--silver', 'logo_silver');
    swap('.brand-footer__logo--gold',   'logo_lit');
    swap('.album__frame',               'frame_album');
    swap('.info-card__frame',           'frame_nowplaying');
    swap('.dustmotes',                  'dustmotes');
    swap('.play-btn__icon--play',       'btn_play');
    swap('.play-btn__icon--pause',      'btn_pause');
    swap('.listening-banner__icon',     'icon_people');
    swap('.history__icon',              'icon_history');
    swap('.volume__bar',                'volume_bar');

    // Spotlight is special — themes can use either an image or a video.
    // Detect the file type and route to the correct element so .mp4/.webm
    // play through <video> (smooth, no GIF crunch) and .png/.gif/.webp use
    // the <img> slot like before.
    if (t.spotlight) {
      const VIDEO_RE = /\.(mp4|webm|mov|m4v)(?:\?.*)?$/i;
      const imgEl = document.querySelector('.spotlight--img');
      const vidEl = document.querySelector('.spotlight--vid');
      const isVideo = VIDEO_RE.test(t.spotlight);
      if (isVideo && vidEl) {
        if (imgEl) imgEl.hidden = true;
        vidEl.src = t.spotlight;
        vidEl.hidden = false;
        // Some browsers won't autoplay until the element is visible
        vidEl.play().catch(() => { /* fine — will retry on visibility */ });
      } else if (imgEl) {
        if (vidEl) { vidEl.hidden = true; vidEl.removeAttribute('src'); }
        imgEl.src = t.spotlight;
        imgEl.hidden = false;
      }
    }

    // Stage backgrounds are CSS background-image, not <img> — set directly
    if (t.stage_silver) {
      document.querySelector('.stage-bg--silver').style.backgroundImage =
        `url('${t.stage_silver}')`;
    }
    if (t.stage_lit) {
      document.querySelector('.stage-bg--gold').style.backgroundImage =
        `url('${t.stage_lit}')`;
    }
    // Star knob is also a background-image inside the slider thumb
    if (t.star_knob) {
      // Inject a tiny stylesheet override for the thumb's background
      const style = document.createElement('style');
      style.textContent = `
        .volume__input::-webkit-slider-thumb { background-image: url('${t.star_knob}'); }
        .volume__input::-moz-range-thumb     { background-image: url('${t.star_knob}'); }
      `;
      document.head.appendChild(style);
    }

    // Per-channel page title (e.g., "Blue Hermit — Stardust Radio")
    if (t.name) {
      document.title = `${t.name} — Stardust Radio`;
    }

    // Twitch channels — when a station's theme.json sets
    // source_type: "twitch" + twitch_channel: "username", the listener
    // swaps from the AzuraCast stage to a Twitch embed.
    if (t.source_type === 'twitch' && t.twitch_channel) {
      document.body.classList.add('is-twitch');
      const twitchStage = document.getElementById('twitch-stage');
      const iframe     = document.getElementById('twitch-embed');
      const openLink   = document.getElementById('twitch-open-link');
      if (twitchStage && iframe) {
        // Twitch requires the embedding parent's hostname in the URL.
        const parent = window.location.hostname || 'stardust-radio.org';
        const ch = encodeURIComponent(t.twitch_channel);
        iframe.src =
          'https://player.twitch.tv/?channel=' + ch +
          '&parent=' + encodeURIComponent(parent) +
          '&autoplay=true&muted=false';
        twitchStage.hidden = false;
      }
      if (openLink) {
        openLink.href = 'https://twitch.tv/' + encodeURIComponent(t.twitch_channel);
      }
      // The AzuraCast audio element + polling can stop — nothing to do
      // when the channel is a Twitch passthrough.
      try { els.audio.pause(); els.audio.removeAttribute('src'); } catch (_) {}
    }
  } catch (err) {
    console.debug('[theme] no theme override:', err.message);
  }
})();

function streamUrl(cacheBust = false) {
  const base = CFG.azuracastBase || '';
  const sc   = SHORTCODE || '';
  const url  = `${base}/listen/${sc}/radio.mp3`;
  return cacheBust ? `${url}?t=${Date.now()}` : url;
}

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Play / pause — also drives the lights-on body class
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
els.playToggle.addEventListener('click', async () => {
  if (els.audio.paused) {
    try {
      // Re-set src each time so we re-fetch a fresh chunk of the live stream.
      // Paused live streams go stale — this avoids resuming from old audio.
      els.audio.src = streamUrl(true);
      await els.audio.play();
    } catch (err) {
      console.error('[player] play failed:', err);
    }
  } else {
    els.audio.pause();
  }
});

const syncPlayState = () => {
  const playing = !els.audio.paused;
  els.playToggle.setAttribute('aria-pressed', String(playing));
  els.playToggle.setAttribute('aria-label', playing ? 'Pause stream' : 'Play stream');
  // Drive the lights-off / lights-on visual swap
  els.body.classList.toggle('is-playing', playing);
  els.body.classList.toggle('is-idle', !playing);
};
['play', 'pause', 'ended'].forEach((ev) =>
  els.audio.addEventListener(ev, syncPlayState)
);

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Volume slider with localStorage persistence
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
const STORED_VOL = Number(localStorage.getItem('stardust.volume'));
const initialVol = Number.isFinite(STORED_VOL) && STORED_VOL >= 0 && STORED_VOL <= 100
  ? STORED_VOL : 80;
els.volume.value = String(initialVol);
els.audio.volume = initialVol / 100;

els.volume.addEventListener('input', () => {
  const v = Number(els.volume.value);
  els.audio.volume = v / 100;
  localStorage.setItem('stardust.volume', String(v));
});

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Now-playing poll — hits AzuraCast public JSON endpoint
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
const NOW_PLAYING_URL =
  `${CFG.azuracastBase}/api/nowplaying/${SHORTCODE}`;

let lastSongId = null;

async function fetchNowPlaying() {
  // Twitch channels don't go through AzuraCast — skip the poll entirely
  // so we're not banging on a station that doesn't exist.
  if (document.body.classList.contains('is-twitch')) return;
  try {
    const res = await fetch(NOW_PLAYING_URL, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderNowPlaying(data);
  } catch (err) {
    console.warn('[nowplaying] poll failed:', err.message);
    // Don't blow away the UI — leave last good values.
  }
}

function renderNowPlaying(data) {
  // Listener count
  const listeners = data?.listeners?.current;
  if (typeof listeners === 'number') {
    els.listenerCount.textContent = String(listeners);
  }

  // Recent tracks list
  renderHistory(data?.song_history);

  const song = data?.now_playing?.song;
  if (!song) return;

  const title  = song.title  || song.text || 'Untitled';
  const artist = song.artist || '';
  const art    = song.art    || PLACEHOLDER_ART;
  const songId = song.id     || `${title}::${artist}`;

  if (els.trackTitle.textContent !== title) {
    els.trackTitle.textContent = title;
    applyMarqueeIfOverflowing(els.trackTitle);
  }
  if (els.trackArtist.textContent !== artist) els.trackArtist.textContent = artist;
  if (els.albumArt.getAttribute('src') !== art) {
    els.albumArt.src = art;
    els.albumArt.alt = artist ? `${title} — ${artist}` : title;
  }

  if (songId !== lastSongId) {
    lastSongId = songId;
    // Pull lyrics + duration from AzuraCast metadata if the host has set
    // them on the song. They override the Suno fallback so DJs can ship
    // lyrics for non-Suno tracks (YouTube, uploads, etc.).
    const azuraLyrics   = song.lyrics || '';
    const azuraDuration = data?.now_playing?.duration ?? null;
    onSongChanged(title, artist, azuraLyrics, azuraDuration);
  }
}

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Title marquee — auto-scroll long titles in the now-playing
//      Adds .is-scrolling and a CSS variable for the exact overflow distance.
//      Re-checks on window resize so it adapts to viewport changes.
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
function applyMarqueeIfOverflowing(el) {
  // `el` is the inner <span> — its parent <p> is the clipping viewport.
  // Reset both so we can re-measure cleanly.
  const parent = el.parentElement;
  el.classList.remove('is-scrolling');
  el.style.removeProperty('--scroll-distance');
  if (parent) parent.classList.remove('is-marquee');

  // Wait for the next paint so layout widths are accurate
  requestAnimationFrame(() => {
    if (!parent) return;
    const overflow = el.offsetWidth - parent.clientWidth;
    if (overflow > 4) {
      // Pad a touch so the last char fully clears the right edge
      el.style.setProperty('--scroll-distance', `-${overflow + 16}px`);
      parent.classList.add('is-marquee');   // switch parent to left-align
      el.classList.add('is-scrolling');     // start the animation
    }
  });
}

// Re-check on resize — viewport changes can flip overflow on/off
window.addEventListener('resize', () => {
  if (els.trackTitle) applyMarqueeIfOverflowing(els.trackTitle);
});

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Recent tracks — render the last 5-8 played songs
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
let lastHistorySig = '';

function renderHistory(history) {
  if (!els.historyList) return;
  if (!Array.isArray(history) || history.length === 0) {
    els.historyList.innerHTML = '<li class="history__empty">no tracks played yet</li>';
    return;
  }

  // Avoid rebuilding the DOM if the history hasn't changed since last poll
  const top = history.slice(0, 8);
  const sig = top.map((h) => h?.sh_id ?? `${h?.song?.title}-${h?.played_at}`).join('|');
  if (sig === lastHistorySig) return;
  lastHistorySig = sig;

  els.historyList.innerHTML = top.map((entry) => {
    const s = entry?.song ?? {};
    const title  = (s.title  || s.text || 'Untitled').replace(/&/g, '&amp;').replace(/</g, '&lt;');
    const artist = (s.artist || '').replace(/&/g, '&amp;').replace(/</g, '&lt;');
    const art    = s.art || '/static/assets/placeholder-art.svg';
    return `
      <li class="history__item">
        <img class="history__art" src="${art}" alt="" loading="lazy" />
        <div class="history__meta">
          <span class="history__title">${title}</span>
          <span class="history__artist">${artist || '—'}</span>
        </div>
      </li>
    `;
  }).join('');
}

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Song-change handler — drives video + lyrics overlay
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
async function onSongChanged(title, artist, azuraLyrics = '', azuraDuration = null) {
  setVideoMode(false);

  // Prefer AzuraCast lyrics (set by the host on the song's metadata) so DJs
  // can ship lyrics for tracks that aren't on Suno. Falls back to scraping
  // Suno only when nothing is set in AzuraCast.
  if (azuraLyrics) {
    setLyrics(azuraLyrics, azuraDuration);
  } else {
    setLyrics(null);
  }

  try {
    const params = new URLSearchParams({ title, artist });
    const res = await fetch(`/api/track-info?${params}`, { cache: 'no-store' });
    if (!res.ok) return;
    const info = await res.json();

    if (info.video_url) {
      els.musicVideo.src = info.video_url;
      els.musicVideo.hidden = false;
      els.musicVideo.play().catch(() => setVideoMode(false));
      setVideoMode(true);
    }
    // Only use Suno-scraped lyrics if AzuraCast didn't already provide some
    if (info.lyrics && !azuraLyrics) {
      setLyrics(info.lyrics, info.duration);
    }
  } catch (err) {
    console.debug('[track-info] not available:', err.message);
  }
}

function setVideoMode(on) {
  if (on) {
    els.albumInner.dataset.mode = 'video';
  } else {
    delete els.albumInner.dataset.mode;
    els.musicVideo.pause();
    els.musicVideo.removeAttribute('src');
    els.musicVideo.hidden = true;
  }
}

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Scrolling lyrics — vertical karaoke-style scroll
//      Adds an intro delay before the first line so the lyrics don't
//      start scrolling before the vocals do, and paces the rest of
//      the lines across the remaining song duration.
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
let lyricsTimer      = null;
let lyricsStartTimer = null;

// Wait this long after a song change before showing the first lyric
// line. Most songs have at least 5–10 seconds of intro before vocals.
const LYRICS_INTRO_DELAY_MS = 6000;

function setLyrics(text, durationSec) {
  // Cancel both the pre-roll wait and the per-line interval so a fast
  // song change doesn't leave stale timers running.
  if (lyricsStartTimer) {
    clearTimeout(lyricsStartTimer);
    lyricsStartTimer = null;
  }
  if (lyricsTimer) {
    clearInterval(lyricsTimer);
    lyricsTimer = null;
  }

  // No lyrics UI on this theme/page → nothing to render.
  if (!els.lyricsBox || !els.lyricsTrack) return;

  if (!text) {
    els.lyricsBox.hidden = true;
    els.lyricsTrack.innerHTML = '';
    els.lyricsTrack.style.transform = '';
    return;
  }

  // Drop bracketed section markers like [Verse], [Chorus]
  const lines = text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l.length > 0 && !/^\[[^\]]+\]$/.test(l));

  if (lines.length === 0) {
    els.lyricsBox.hidden = true;
    return;
  }

  els.lyricsTrack.innerHTML = lines
    .map((l, i) => {
      const safe = l.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      return `<span class="lyric-line" data-idx="${i}">${safe}</span>`;
    })
    .join('');
  els.lyricsBox.hidden = false;

  // Pace the lyrics over the song's playable time minus the intro delay,
  // so the last line lands roughly when the song ends instead of running
  // long. A floor of 1.8s keeps short songs / few lines from blurring by.
  const totalSec = Number.isFinite(durationSec) && durationSec > 0
    ? durationSec
    : lines.length * 4;
  const introSec     = LYRICS_INTRO_DELAY_MS / 1000;
  const effectiveSec = Math.max(totalSec - introSec, lines.length * 3);
  const perLineMs    = Math.max(1800, (effectiveSec / lines.length) * 1000);

  // Pre-roll: hold on no highlighted line for the intro window, then
  // start the karaoke scroll.
  lyricsStartTimer = setTimeout(() => {
    lyricsStartTimer = null;
    let cur = 0;
    highlightLyric(cur);
    lyricsTimer = setInterval(() => {
      cur += 1;
      if (cur >= lines.length) {
        clearInterval(lyricsTimer);
        lyricsTimer = null;
        return;
      }
      highlightLyric(cur);
    }, perLineMs);
  }, LYRICS_INTRO_DELAY_MS);
}

function highlightLyric(idx) {
  if (!els.lyricsTrack) return;
  const lineEls = els.lyricsTrack.querySelectorAll('.lyric-line');
  lineEls.forEach((el) => el.classList.remove('is-current'));
  const target = lineEls[idx];
  if (!target) return;
  target.classList.add('is-current');

  const trackRect    = els.lyricsTrack.getBoundingClientRect();
  const targetRect   = target.getBoundingClientRect();
  const viewportRect = els.lyricsTrack.parentElement.getBoundingClientRect();
  const offset = (targetRect.top - trackRect.top)
               - (viewportRect.height / 2 - target.offsetHeight / 2);
  els.lyricsTrack.style.transform = `translateY(${-offset}px)`;
}

// ╭─────────────────────────────────────────────────────── ♱ · 𓆩🤍𓆪 · ♱ ─╮
//      SECTION: Boot — start polling if config is present
// ╰─ ♱ · 𓆩🤍𓆪 · ♱ ───────────────────────────────────────────────────────╯
if (CFG.azuracastBase && SHORTCODE) {
  fetchNowPlaying();
  setInterval(fetchNowPlaying, POLL_INTERVAL_MS);
} else {
  els.trackTitle.textContent = 'Not configured';
  els.trackArtist.textContent = 'Set AZURACAST_BASE in .env';
}
