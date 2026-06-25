"""CMS (11-BM) site-notes skill plugin.

A concise, interim cheat-sheet for the embedded Claude agent covering the CMS
Tiled instances (and the per-entry access-policy read gotcha) plus a few
site-specific quirks. Intended to be slimmed down or retired once the CMS
deployment settles.
"""

from __future__ import annotations

from lightfall.plugins.agent_plugin import AgentPlugin


class CMSSiteNotesAgent(AgentPlugin):
    """Skill giving Claude CMS-specific context (Tiled instances + site quirks)."""

    @property
    def name(self) -> str:
        return "cms_site_notes"

    @property
    def display_name(self) -> str:
        return "CMS Site Notes"

    @property
    def description(self) -> str:
        return (
            "CMS (11-BM) site notes: the NSLS-II Tiled instances/nodes and the "
            "per-entry access-policy read gotcha (Duo identity lists zero "
            "records; the service key sees all), plus interim deployment "
            "quirks. Use when working with the data browser, Tiled, run "
            "history/scan data, login, or CMS profile/session setup."
        )

    @property
    def category(self) -> str:
        return "operations"

    @property
    def enabled_by_default(self) -> bool:
        return True

    @property
    def priority(self) -> int:
        return 5

    def get_system_prompt(self) -> str:
        return """
## CMS (11-BM) Site Notes

Interim cheat-sheet for the NSLS-II CMS beamline (11-BM) deployment, which is
still being set up. Treat unexpected emptiness or fallbacks as configuration,
not as absence of data.

### Tiled instances
- **Server:** `https://tiled.nsls2.bnl.gov` (NSLS-II). The data browser reads
  through Lightfall's `TiledService`. If no client is adopted it falls back to
  the ALS default server, which is unreachable from NSLS-II — a CMS session
  must adopt an NSLS-II client. The status bar shows `Tiled: On/Off`.
- **Nodes under `cms`:** `raw` (primary catalog — all bluesky runs),
  `migration` (legacy data; the old `mig` client points here), plus `sandbox`
  and `bluesky_sandbox`.
- **Two read identities — this is the common trap:**
  - *Per-user Duo identity* (e.g. `rpandolfi`): write-capable but
    **read-filtered to zero**. Tiled enforces a per-entry access policy (every
    run stores an `access_blob` stamped by AccessStamper), and existing
    `cms/raw` records authorize none of the per-user principals. So reading
    `cms/raw` as a Duo user returns an EMPTY catalog even though the user holds
    the global `read:data`/`read:metadata` scopes. Symptom: `<Catalog {}>`,
    "Loaded 0 of 0 records".
  - *Service/admin key* `TILED_BLUESKY_WRITING_API_KEY_CMS`: bypasses the
    per-entry policy and sees every record. **The data browser must read
    through this key.** It is the same key `00-startup.py` uses to build
    `tiled_writing_client = from_profile("nsls2", api_key=...)["cms"]["raw"]`.
- **Writes** go through the profile's `tiled_inserter` (Kafka +
  `nslsii.configure_base` in `00-startup.py`), NOT through `TiledService`'s
  writer — so the browser's read client and the write path are independent.
- The browser read client is adopted in
  `auth/nsls2_provider.py::_adopt_browser_client` (at login) and again in
  `bootstrap.py::adopt`; both must use the admin key. If run-history/scan-data
  tools come back empty, check which identity the adopted client carries
  before concluding the node is empty.

### Tiled here is BIG and SLOW — read narrowly
The `cms/raw` catalog holds **millions** of runs and the NSLS-II server is slow.
Walking the whole thing is bad form: it pages through the entire index, can take
many minutes, and (because the embedded IPython console runs on the Qt main
thread) will **freeze the whole Lightfall UI** while it churns.

- **Never materialize the full catalog.** Avoid `list(client)`, `for x in
  client:`, `client.values()`, `client.items()`, `len(client)`, or
  `np.asarray()` over a whole image column. Any of these fans out to millions of
  network round-trips.
- **Fetch a known run by UID** — `client[uid]` is O(1). Always prefer this when
  you have the UID.
- **For "recent runs", slice a server-sorted view**, e.g.
  `client.sort(("time", -1)).values_indexer[:N]` — let the server do the
  ordering and only pull N. Do **not** build `[k for k, _ in client.items()]`
  just to take the last few (that walks everything).
- **Prefer the MCP tools**, which already bound their reads:
  `lightfall_get_run_history(limit=...)`, `lightfall_get_last_run`,
  `lightfall_get_scan_data(uid=...)` (it summarizes image/array columns by
  shape instead of downloading pixels). Reach for raw `client` iteration only
  when a tool can't express what you need — and even then, keep it bounded.
- **Single frames, not whole stacks:** use `fetch_frame` / `fetch_subcube`
  (`lightfall.utils.tiled_helpers`) for server-side slicing instead of pulling a
  full N×H×W array.

### Session / profile / site
- The CMS IPython profile-collection is hosted inside Lightfall's kernel:
  infra scripts (00–03) stand up the RunEngine + Tiled clients; the SAM
  framework (`cms`, `beam`, samples, stages) loads after. Devices come from
  **happi** (injected), not the profile's device-defining scripts.
- Redis: `info.cms.nsls2.bnl.gov`. Pin `redis>=5,<6` — the old server only
  speaks the RESP2 `HELLO`.
- Login button: "NSLS-II (CMS)" (green). BNL username + password triggers a
  Duo push via Tiled's password grant; the token is cached (no re-prompt).
- Useful env overrides: `CMS_PROFILE_KEEP` / `CMS_PROFILE_SAM_KEEP` (which
  profile scripts run), `CMS_BEAMLINE_STAGE`, and detector enable flags
  (`CMS_PILATUS2M_ON`, `CMS_PILATUS800_ON`, `CMS_CAMERA_ON`, …).
"""
