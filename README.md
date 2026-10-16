# Garmin dashboard + MCP server

Pulls your Garmin Connect data into a local SQLite database, computes extra
training metrics, and shows everything on a local web dashboard. An MCP
server lets Claude query the same data ("how's my marathon prediction trending?").

Nothing leaves your machine except the calls to Garmin Connect. Garmin has no
official personal API, so this uses the community `garminconnect` library
(the same login the Connect app uses). Tokens are saved in `~/.garminconnect`.

## Setup (once)

```
cd C:\Kai_Li\Claude\garmin-dashboard
.venv\Scripts\python login.py        # email, password (hidden), MFA code if enabled
.venv\Scripts\python sync.py         # first run pulls ~2 years, takes several minutes
.venv\Scripts\python dashboard.py    # opens http://127.0.0.1:8765
```

After that, press **Sync Garmin** on the dashboard (or run `sync.py`) to pull new
data. Syncs are incremental. `sync.py --full` re-downloads the whole window.

To try it without an account: `.venv\Scripts\python dashboard.py --demo` (a synthetic
year of marathon training in `data/demo.sqlite`).

### Personal settings (optional)

Copy `settings.example.json` to `settings.json`. Here you can set a **goal race**
(countdown, target pace, predicted time and gap, VDOT needed), a sleep goal, and
override max/resting HR if Garmin's values are off.

## What's on the dashboard

| Section | From Garmin | Calculated here |
|---|---|---|
| VO2max | Garmin VO2max history, fitness age | HR-based VO2max per run (%HRR ≈ %VO2R, Daniels oxygen cost), rolling 6-week best **VDOT**, Uth estimate, age/sex category |
| Race predictions | Garmin 5K/10K/Half/Marathon + trend | **VDOT** (Daniels), **Riegel** from your best recent effort, **Tanda** marathon model (8-week km/week + mean pace), consensus (median) |
| Training load | Training status, acute/chronic load, 4-week load focus, readiness, endurance and hill scores, lactate threshold | Banister **TRIMP** per activity, **fitness/fatigue/form** (CTL/ATL/TSB), **ACWR** 7:28 with risk bands, monotony and strain, weekly ramp rate |
| Volume | Activities | Weekly hours by sport, run km plus longest run, 12-month heatmap, YTD vs last year, streak |
| Intensity | HR time-in-zone per activity | 28-day zone distribution, 80/20 polarization verdict |
| Efficiency | | Efficiency factor (speed per heartbeat) trend, Zone 2 pace, cadence |
| Recovery | HRV and baseline, resting HR, sleep, Body Battery, weight | **Recovery score** (HRV, RHR and sleep z-scores vs your 60-day baseline), RHR delta, sleep debt |
| Records | Garmin PRs | Best efforts from fastest splits (all-time and this year) |
| Insights | | Plain-language flags (ramping too fast, poor sleep, RHR up, grey-zone training, …) |

## MCP server (use your data from Claude)

It's registered as `garmin` in two places, so it's available in every session:

- Claude desktop chat: `%APPDATA%\Claude\claude_desktop_config.json` → `mcpServers.garmin`
- Claude Code / Code tab (user scope): `~\.claude.json` → `mcpServers.garmin`

Both files have a `.bak-before-garmin` backup from before the change. Restart the app
to pick it up. To serve demo data instead, add `"--demo"` to the server's `args`.

Tools: `sync_garmin`, `get_overview`, `get_activities`, `get_activity_details`
(live laps and HR zones), `get_vo2max`, `get_race_predictions`, `get_training_load`,
`get_recovery`, `get_weekly_volume`, `get_personal_records`, `run_sql` (read-only),
`open_dashboard`.

## Hosted version (GitHub Pages, password protected)

GitHub Pages sites are always public, so privacy comes from encryption:

- `data.enc` on the site is your dashboard data, AES-256-GCM encrypted with a key derived
  from your dashboard password (PBKDF2-SHA256, 600k iterations). The page decrypts it in
  your browser; the password never leaves your device. "Remember on this device" stores
  only the derived key in that browser; **Lock** forgets it.
- The `state` branch holds `state.enc`: database, a separate Garmin login token for GitHub,
  and `settings.json`, encrypted with a random key (`STATE_KEY` secret, local copy in
  `data/state_key.txt`). GitHub's token refreshes itself; each run saves the new one.
- `.github/workflows/sync.yml` runs twice a day: decrypt state → `sync.py` → re-encrypt
  state → build the encrypted site → deploy to Pages. Run it any time from the Actions tab.

Secrets: `STATE_KEY`, `DASHBOARD_PASSWORD` (use a long passphrase: the encrypted file is public).

If the GitHub Garmin login ever expires (the workflow fails with a message saying so):

```
.venv\Scripts\python publish.py login-cloud
.venv\Scripts\python publish.py bundle
```

then push `state.enc` to the `state` branch (see `publish.py`).

## Files

```
login.py / sync.py / dashboard.py / mcp_server.py   entry points
garmin_dash/sync.py        Garmin → SQLite (raw JSON kept, so new metrics need no re-download)
garmin_dash/analytics.py   every calculation
garmin_dash/demo.py        synthetic demo data
static/                    dashboard UI (Chart.js)
data/garmin.sqlite         your data (gitignored)
```

## Caveats

- Garmin changes its unofficial endpoints from time to time. Each sync step runs
  on its own, so one failing endpoint only blanks that part. The sync output and
  the dashboard footer name anything that failed.
- The calculated numbers are estimates. VDOT and Riegel assume a race-like effort
  exists in the last 6 weeks. Tanda is the most realistic marathon number when your
  long-run volume is low.
