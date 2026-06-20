# NSLS-II Ring Status — StatusBar Plugin Design

**Date:** 2026-06-20
**Package:** `lightfall-endstation-cms`
**Status:** Approved (design), pending implementation plan

## Goal

Add a status-bar indicator to Lightfall showing the NSLS-II storage-ring
status (beam current, lifetime, operating mode, beam availability), analogous
to the existing ALS beam-status indicator in core `lightfall`. It belongs in
`lightfall-endstation-cms` because it is specific to the NSLS-II CMS (11-BM)
beamline, not the ALS home facility.

## Background

The public NSLS-II operating-status page
(`https://www.bnl.gov/nsls2/operating-status.php`) embeds a CS-Studio Display
Builder web runtime whose live values are served over a PVWS WebSocket. The
underlying values are ordinary EPICS PVs. Because `lightfall-endstation-cms`
runs on the NSLS-II network at the CMS beamline, those PVs are reachable
directly over Channel Access — so we subscribe to them with **caproto monitors**
rather than reverse-engineering the public WebSocket gateway. The gateway exists
only to expose these PVs to the public internet, which we do not need on-site.

The PV names were extracted from the display
(`NSLS2Status.bob`, via the DBWR `screen` render endpoint).

### Source PVs

| Field           | PV                          | Notes                          |
|-----------------|-----------------------------|--------------------------------|
| Beam current    | `SR:C03-BI{DCCT:1}I:Real-I` | mA                             |
| Beam lifetime   | `SR:C03-BI{DCCT:1}Lifetime-I` | hours                        |
| Operating mode  | `SR-OPS{}Mode-Sts`          | enum → string                  |
| Shutter / permit| `SR-OPS{}Shutter-Sts`       | enum → string; drives availability |
| Top-off state   | `INJ{TOC}OpControl-Sel`     | enum → string                  |
| Next injection  | `INJ{TOC-SM}Cnt:Next-I`     | countdown                      |
| Ops message 1   | `OP{1}Message.VAL$`         | long-string text               |
| Ops message 2   | `OP{2}Message.VAL$`         | long-string text               |

Exact enum value semantics (which `Shutter-Sts` string means "beam available")
are verified against the live PVs during implementation; parsing is defensive
until confirmed.

## Architecture

Two self-contained units in `lightfall-endstation-cms`, mirroring the public
shape of core's `lightfall.services.als_beam_status` /
`lightfall.ui.statusbar.plugins.als_beam_status`, plus one manifest entry. No
changes to core `lightfall`, and **no** refactor of the ALS code into a shared
base (YAGNI; keeps the units isolated and independently testable).

### 1. Service — `services/nsls2_beam_status.py`

`NSLS2BeamData` (dataclass):
`beam_current: float`, `beam_available: bool`, `mode: str`, `lifetime: float`,
`topoff_state: str`, `next_injection: str`, `ops_message: str`,
`timestamp: datetime | None`.

`NSLS2BeamStatusService(QObject)` — singleton, mirroring the ALS service's
public surface:

- Signals: `status_changed = Signal(object)` (carries `NSLS2BeamData`),
  `connection_changed = Signal(bool)`.
- Classmethods: `get_instance()`, `reset()` (test teardown).
- Properties: `current_data`, `is_connected`, `last_error`, `is_running`.
- Methods: `start()`, `stop()`, `force_refresh()`, `get_introspection_data()`.

Behaviour:

- `start()` creates a `caproto.threading.client.Context`, resolves the PVs via
  `ctx.get_pvs(..., connection_state_callback=...)`, and adds a monitor
  subscription per PV. Idempotent (no-op if already running).
- Monitor callbacks fire on caproto worker threads. Each callback maps its PV to
  the corresponding `NSLS2BeamData` field under a lock and emits
  `status_changed`. Qt delivers the cross-thread signal on the GUI thread
  (auto-queued connection), so widget updates stay on the main thread.
- Connection state is derived from the PV connection callbacks; `last_error`
  captures connection failures. Off-network, PVs never connect →
  `is_connected` stays `False` → no crash.
- A PV→field mapping table (constant) plus a pure
  `_apply_pv_value(pv_name, value) -> None` method holds all parsing logic
  (including `beam_available` derivation), so it is unit-testable without a live
  IOC.
- `get_introspection_data()` returns a dict of the current values for the MCP
  introspection tooling, matching the ALS service's keys where they correspond.

No proxy logic (Channel Access, on-network) — the ALS `ProxySettingsProvider`
path is intentionally omitted.

### 2. Plugin — `statusbar/nsls2_beam_status.py`

`NSLS2BeamStatusPlugin(StatusBarPlugin)`, mirroring `ALSBeamStatusPlugin`:

- `metadata`: id `lightfall.statusbar.nsls2_beam`, name "NSLS-II Beam Status",
  `priority=45`, `position="permanent"`.
- `update()`: lazy-starts the service if not running; renders from
  `service.current_data` or shows "Offline" when disconnected.
- Display: qtawesome icon + `"{current:.1f} mA | {lifetime:.1f}h"` text
  (compact — text hidden when nominal, shown when notable, as the ALS plugin
  does); color `success` when beam available, `error` when not, `text_secondary`
  when offline.
- `connect_signals()` / `disconnect_signals()`: wire `status_changed`,
  `connection_changed`, and theme `colors_changed` to `update()`.
- Toast on beam-availability transitions (ring open/closed), with a link to the
  operating-status page.
- Tooltip: mode, current, lifetime, top-off state, next injection, ops message,
  last-updated time.
- `on_clicked()`: opens `https://www.bnl.gov/nsls2/operating-status.php`.
- `get_introspection_data()`: extends the base with the service's data.

### 3. Manifest — `manifest.py`

Add one entry:

```python
PluginEntry(
    type_name="statusbar",
    name="nsls2_beam_status",
    import_path="lightfall_endstation_cms.statusbar.nsls2_beam_status:NSLS2BeamStatusPlugin",
    metadata={"beamline": "11-BM CMS"},
)
```

## Testing

TDD throughout. Run with `.venv/Scripts/python -m pytest` from the CMS package
(never bare `pytest`).

- **Service mapping**: feed synthetic PV values to `_apply_pv_value` and assert
  the resulting `NSLS2BeamData`, including `beam_available` derivation and the
  enum-string handling. No live IOC.
- **Service lifecycle**: monkeypatch the caproto `Context` so `start()` never
  touches the network; assert idempotent start, signal emission on update,
  connection-state transitions, `get_instance`/`reset` singleton behaviour, and
  `get_introspection_data()` output.
- **Plugin**: fake-service tests (same pattern as core's ALS beam-status test)
  covering display text/color/tooltip for available/closed/offline states,
  toast-on-availability-change, click URL, and introspection. Uses the offscreen
  Qt platform like the existing UI tests.
- **Manifest**: assert the `nsls2_beam_status` statusbar entry is present and its
  `import_path` imports.

## Decisions / non-goals

- Self-contained service + plugin; no shared base extracted from ALS code.
- caproto threading client, not an ophyd Device — lighter for a read-only
  indicator.
- Real-time monitors (push), not polling.
- No web-gateway/WebSocket fallback for off-site development — "Offline" is
  acceptable there.
- Exact `Shutter-Sts` / `Mode-Sts` enum semantics confirmed against live PVs
  during implementation.
