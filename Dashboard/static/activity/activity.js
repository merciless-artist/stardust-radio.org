/* Stardust Radio — Discord Activity client.
 * Runs inside Discord's iframe. All network calls go through Discord's proxy
 * (/.proxy/...) per the app's URL Mappings:
 *   root  /       -> stardust-radio.org        (this app + its /api routes)
 *   /radio         -> radio.stardust-radio.org  (AzuraCast stream + now-playing)
 */
(function () {
  "use strict";

  var CLIENT_ID = window.ACTIVITY_CLIENT_ID;
  var $ = function (id) { return document.getElementById(id); };

  // Outside a Discord voice channel there's no frame context — fail gracefully
  // instead of throwing (e.g. someone opens the URL directly in a browser).
  var sdk;
  try {
    sdk = new window.DiscordSDK(CLIENT_ID);
  } catch (e) {
    $("title").textContent = "Stardust Radio";
    $("status").textContent = "Open this from a Discord voice channel to listen together.";
    return;
  }

  var instanceId = null;
  var currentStation = "main";
  var audio = $("audio");

  var STATIONS = [
    ["main", "Stardust Radio"],
    ["kiki", "Kiki"],
    ["blue_hermit", "Blue Hermit"],
    ["syna", "SYN∆"],
    ["fades2red", "Fades2Red"],
  ];

  function streamUrl(sc) { return "/.proxy/radio/listen/" + sc + "/radio.mp3"; }
  function npUrl(sc) { return "/.proxy/radio/api/nowplaying/" + sc; }

  // ── Playback ──────────────────────────────────────────────────────────────
  function playStation(sc) {
    currentStation = sc;
    audio.src = streamUrl(sc);
    audio.play().catch(function () { $("tap").hidden = false; }); // autoplay blocked
  }
  $("tap").addEventListener("click", function () {
    $("tap").hidden = true;
    audio.play().catch(function () {});
  });

  // ── Now-playing (title / artist / art) ──────────────────────────────────────
  // Set the viewer's Discord rich presence to the current song (needs the
  // rpc.activities.write scope). This is what shows "Stardust Radio — <song>"
  // on their profile while the Activity is open.
  function setStatusPresence(np) {
    try {
      sdk.commands.setActivity({
        activity: {
          type: 2, // Listening
          details: (np && (np.title || np.text)) || "Stardust Radio",
          state: (np && np.artist) ? "by " + np.artist : "Stardust Radio",
        },
      });
    } catch (e) {}
  }

  function pollNowPlaying() {
    fetch(npUrl(currentStation))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var np = (Array.isArray(d) ? d[0] : d).now_playing.song;
        $("title").textContent = np.title || np.text || "Stardust Radio";
        $("artist").textContent = np.artist || "";
        if (np.art) {
          try { $("art").src = "/.proxy/radio" + new URL(np.art).pathname; } catch (e) {}
        }
        setStatusPresence(np);
      })
      .catch(function () { /* keep last render */ });
  }

  // ── Station picker + shared "pass the aux" ───────────────────────────────────
  function renderStations() {
    var box = $("stations");
    box.innerHTML = "";
    STATIONS.forEach(function (pair) {
      var sc = pair[0], label = pair[1];
      var b = document.createElement("button");
      b.textContent = label;
      b.className = "station" + (sc === currentStation ? " active" : "");
      b.addEventListener("click", function () {
        // Don't switch locally — set the shared station; the poll switches everyone.
        fetch("/.proxy/api/activity/instance/" + instanceId + "/station", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ station: sc }),
        }).then(pollStation).catch(function () {});
      });
      box.appendChild(b);
    });
  }

  function pollStation() {
    if (!instanceId) return;
    fetch("/.proxy/api/activity/instance/" + instanceId + "/station")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.station && d.station !== currentStation) {
          playStation(d.station);
          renderStations();
          pollNowPlaying();
        }
      })
      .catch(function () {});
  }

  // ── Participants (who's listening) ──────────────────────────────────────────
  function renderListeners(list) {
    var box = $("listeners");
    box.innerHTML = "";
    (list || []).forEach(function (p) {
      var img = document.createElement("img");
      img.className = "avatar";
      img.title = p.global_name || p.username || "";
      img.src = p.avatar
        ? "https://cdn.discordapp.com/avatars/" + p.id + "/" + p.avatar + ".png?size=64"
        : "https://cdn.discordapp.com/embed/avatars/0.png";
      box.appendChild(img);
    });
  }
  function initParticipants() {
    sdk.commands.getInstanceConnectedParticipants()
      .then(function (r) { renderListeners(r.participants); })
      .catch(function () {});
    try {
      sdk.subscribe("ACTIVITY_INSTANCE_PARTICIPANTS_UPDATE", function (e) {
        renderListeners(e.participants);
      });
    } catch (e) {}
  }

  // ── Boot ────────────────────────────────────────────────────────────────────
  function setup() {
    return sdk.ready()
      .then(function () {
        return sdk.commands.authorize({
          client_id: CLIENT_ID,
          response_type: "code",
          state: "",
          prompt: "none",
          scope: ["identify"],
        });
      })
      .then(function (r) {
        return fetch("/.proxy/api/activity/token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: r.code }),
        }).then(function (x) { return x.json(); });
      })
      .then(function (t) {
        return sdk.commands.authenticate({ access_token: t.access_token });
      })
      .then(function () {
        instanceId = sdk.instanceId;
        $("status").textContent = "";
        playStation("main");
        renderStations();
        pollNowPlaying();
        initParticipants();
        setInterval(pollStation, 2000);
        setInterval(pollNowPlaying, 10000);
      });
  }

  try {
    setup().catch(function (e) {
      $("status").textContent = "Couldn't connect to Discord — reopen the Activity.";
      console.error("[activity] setup failed:", e);
    });
  } catch (e) {
    // Synchronous throw (e.g. sdk.ready() outside a Discord frame).
    $("title").textContent = "Stardust Radio";
    $("status").textContent = "Open this from a Discord voice channel to listen together.";
  }
})();
