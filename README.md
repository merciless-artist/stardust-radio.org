# Stardust Radio

A self-hosted internet radio + listening-party platform built on **AzuraCast** (streaming engine, Dockerized) and a custom **Flask dashboard**, exposed to the public internet through a **Cloudflare Tunnel** — no port forwarding, no exposed home IP.

---

## Repo layout

```
StardustRadio/
├── Dashboard/          Flask app: listener site + DJ booth
│   ├── server.py       Main Flask server, binds 127.0.0.1:8080
│   ├── static/         HTML pages, CSS, JS, minimal art assets
│   ├── watcher/        Optional "no-server" mode: local Suno-link watcher
│   ├── tests/          pytest suite
│   ├── .env.example    Environment variables template
│   └── README.md       Full setup guide (AzuraCast + Tunnel + Dashboard)
├── deploy/             Deployment helpers
│   ├── cloudflare-tunnel.example.yml
│   └── windows-task-scheduler.md
├── .gitignore
├── LICENSE             MIT (source code only)
└── README.md
```

---

## Architecture

```
┌────────── Listener's browser ──────────┐
│  [ Player ]        [ Discord chat ]    │
└───────────────┬────────────────────────┘
                │ https://yourdomain.com
    ┌───────────▼───────────┐
    │   Cloudflare Edge     │   DNS + Tunnel + free TLS
    └───────────┬───────────┘
                │  encrypted outbound tunnel
    ┌───────────▼───────────┐
    │  Host machine         │
    │  ├─ cloudflared       │
    │  ├─ Flask :8080  ──►  dashboard
    │  └─ AzuraCast :80 ──►  stream + nowplaying API
    └───────────────────────┘
```

---

## Two services, one domain

- **`radio.yourdomain.com`** → AzuraCast streams the always-on catalog.
- **`yourdomain.com`** → Flask dashboard: listener page + time-bounded "listening parties" hosted from a DJ booth.

The two services are **technically independent** — the dashboard does not push songs to the radio, and the radio does not surface on the dashboard. Songs played during a listening party are ephemeral and cleared from state at end of party.

---

## Quick start

Setup lives in **[`Dashboard/README.md`](Dashboard/README.md)**. High level:

1. **AzuraCast** (Docker via WSL2) — handles audio streaming.
2. **Cloudflare Tunnel** (`cloudflared`) — exposes the local machine to the public internet without opening ports.
3. **Dashboard** (this Flask app) — runs on `localhost:8080`, served through the tunnel.

Cloudflare terminates TLS at its edge and forwards plain HTTP to the origin.

---

## Requirements

- Python 3.10+
- Docker Desktop (for AzuraCast on Windows: WSL2 backend)
- Cloudflare account with a domain routed through Cloudflare
- Discord application (for host OAuth on the DJ booth) — optional if only running the public listener side

---

## License

MIT — see [`LICENSE`](LICENSE). Source code only. Any bundled art or media assets remain the property of their respective creators; replace with your own before redistribution.
