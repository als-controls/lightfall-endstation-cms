# CMS post-login device backend + catalog-driven SAM hosting — design

**Status:** approved (Ron, 2026-06-20)
**Date:** 2026-06-20
**Area:** `lightfall-endstation-cms` — device backend, profile bootstrap, panels
**Author:** Ayaka (with Ron)
**Depends on:** lightfall core "post-login plugin loading"
(`lightfall` PR #13 / spec `2026-06-20-post-login-plugin-loading-design.md`)

## Summary

Under lightfall's new **post-login plugin loading** model, the background plugin
wave (and thus each plugin's `create_backend`) fires *on* the `AUTHENTICATED`
transition. The CMS device backend currently arms its SAM bootstrap on that same
transition from inside `create_backend` — so the arm now happens **after**
`AUTHENTICATED`, and the bootstrap never runs.

This redesign makes the CMS device backend an **ordinary post-login happi-JSON
plugin** (standard background instantiation, no kernel device-injection) and
**re-expresses SAM hosting** as a catalog-driven, post-login action.

## Motivation

- The post-login core change breaks the `AUTHENTICATED`-armed bootstrap (timing).
- "Ordinary happi" (background instantiation) is the standard device path; the
  `instantiate="none"` + kernel device-injection workaround existed only to
  sequence around the profile's `set_defaults` (see below) and is no longer
  wanted ("kernel injection no longer strictly necessary" — Ron).

## Current architecture (as-is)

- `manifest.py`: `device_backend` `cms_profile_collection` (not preload),
  `auth_provider` `nsls2_tiled` (**preload**), three `panel`s
  (`cms_sample`/`cms_holder`/`cms_beamline`, **preload=True**), plans, statusbar.
- `plugin.CMSProfileCollectionPlugin.create_backend()` →
  `HappiBackend(cms_happi.json, beamline="CMS", instantiate="none")` and arms
  `CMSSessionTrigger(backend)`.
- `session_trigger.CMSSessionTrigger`: connects `SessionManager.state_changed`;
  on `AUTHENTICATED` runs `ProfileSessionBootstrapper.bootstrap()` on the GUI thread.
- `bootstrap.ProfileSessionBootstrapper.bootstrap()`: run infra scripts
  `00`–`03` → **adopt** the profile's `RE` + Tiled client → **inject** happi
  devices into the kernel namespace (built via the happi client, `mark_device_live`)
  → seed config globals → run SAM scripts `81/94/95/96/97/991`.
- The CMS panels veneer over the live **kernel** SAM objects via `kernel_access.py`.

## Target design (to-be)

### 1. Device backend → ordinary happi background

`create_backend()` returns `HappiBackend(cms_happi.json, beamline="CMS",
instantiate="background")`. The catalog instantiates ophyd objects via the
`DeviceConnectionManager`. **Remove** the `CMSSessionTrigger.arm()` call.

`EpicsSignalBase.set_defaults(timeout=120)` is **dropped** (Ron): the previous
`instantiate="none"` workaround existed only because `set_defaults` must run
before any `EpicsSignalBase` exists, which background instantiation violates.
Dropping it removes that ordering constraint entirely. If the 120 s connect
timeout turns out to matter, re-introduce it later as a *lightfall* device
config, not here.

### 2. By-name device helper

A helper resolves catalog devices by name (the happi DB item names already equal
the profile variable names — `smx`, `pilatus2M`, …, confirmed in `cms_happi.json`
and `bootstrap._inject_devices`). Used to seed the kernel namespace and to back
SAM. Lives in `kernel_access.py` (or a small sibling), reading the
`DeviceCatalog` rather than constructing via the happi client.

### 3. Re-implemented infra (in the plugin, not `00`–`03`)

The CMS RunEngine object is **not** special — `nslsii.configure_base` merely
attaches specialized bits to a stock `bluesky.RunEngine`. Lightfall already
builds its own RE (`get_engine().RE`), so we attach those bits to **it** instead
of running `00-startup`/adopting a separate RE:

| Bit | Source | Attach to Lightfall's RE |
|---|---|---|
| Redis-backed `RE.md` | `RedisJSONDict(redis_url="info.cms.nsls2.bnl.gov")` | `RE.md = RedisJSONDict(...)` |
| Tiled writing | `TiledInserter` → `from_profile("nsls2", api_key=…)["cms"]["raw"]` | `RE.subscribe(tiled_inserter.insert)` |
| Kafka publishing | nslsii kafka document publisher | `RE.subscribe(publisher)` |
| BestEffortCallback | live table/plot | `RE.subscribe(bec)` |
| SupplementalData | baseline readings | `RE.preprocessors.append(sd)` |

The infra setup also **seeds the kernel namespace** with the names the SAM
scripts reference (`RE`, `db`/`cat`, `mig`, the proposal/assets helpers, config
globals), so they resolve without running `00`–`03`.

> The exact set of bits and the Kafka-publisher wiring will be confirmed against
> the installed `nslsii.configure_base` **on the box** while implementing — that
> is the authority for what to replicate. `nslsii` is NSLS-II-only and not in the
> dev venv, so infra-attach code imports `nslsii` lazily and is unit-tested with
> `nslsii` mocked; the real wiring is box-validated.

### 4. SAM hosting as a post-login action (devices-live gated)

Replace the `AUTHENTICATED`-armed trigger with a post-login action that fires
**after the catalog has live devices** (background instantiation is async, so we
must wait — a `DeviceCatalog` "devices ready" signal or a bounded poll). It then:
seeds the kernel namespace from the catalog (by-name) + config globals + the
infra bits, and runs the SAM scripts (`81/94/95/96/97/991`) in the console kernel
as today. The SAM device-DEFINING scripts remain unrun; happi is the single
device source.

The SAM scripts are the beamline's framework and are **not** re-implemented — only
the infra (`00`–`03`) is. They run in the kernel so the console and the CMS
panels share the same live SAM objects (`kernel_access.py` unchanged).

### 5. Plugin phases

- `nsls2_tiled` (auth) stays the **only** `preload=True` (populates the login window).
- `cms_sample`/`cms_holder`/`cms_beamline` panels → **non-preload**: they register
  in the post-login wave, and lightfall's post-login `setup_default_layout` (the
  core change) picks them up. (Pre-login they had nothing to veneer anyway.)

## Sequencing (post-login)

```
AUTHENTICATED → lightfall plugin wave → cms device_backend create_backend()
   → HappiBackend(instantiate="background") added to catalog (async instantiate)
post-login SAM action (waits for devices-live):
   → set up infra bits on lightfall RE + seed namespace
   → seed catalog devices by name + config globals
   → run SAM scripts (81/94/95/96/97/991) in the console kernel
```

## Per-file changes

- `manifest.py` — panels → non-preload.
- `plugin.py` — `instantiate="background"`; drop `set_defaults`; remove
  `CMSSessionTrigger` arming; (keep registry name `cms_profile_collection` for
  persisted-selection compatibility).
- `session_trigger.py` — replace the `AUTHENTICATED` arm with a devices-live-gated
  post-login action (or fold into the bootstrapper).
- `bootstrap.py` — re-implement infra (attach RE bits + seed namespace) instead
  of running/adopting `00`–`03`; source devices from the catalog by name
  (replace `_inject_devices`/`_instantiate_device` happi-client construction).
- `kernel_access.py` — add the by-name catalog accessor (read-side unchanged).

## Locally testable vs box-validated

- **Local (TDD, lightfall venv, `nslsii` mocked):** backend `instantiate`/no-trigger;
  by-name helper against a fake catalog; panels non-preload; the post-login
  action's devices-live gating + once-only firing; the infra-attach seam (bits
  attached to a fake RE, namespace seeded) with `nslsii` mocked.
- **Box (ws5, live beamline):** real `nslsii.configure_base` bit-for-bit parity;
  Tiled writing to `tiled.nsls2.bnl.gov/cms/raw`; Redis md; Kafka; SAM scripts
  running against live devices; CMS panels showing live SAM objects.

## Risks & validation

- **Tiled/Redis/Kafka parity** is the highest risk — re-implementing what
  `configure_base` wires. Mitigation: confirm against `nslsii` on the box; keep
  the attach list minimal and explicit; validate a real scan writes to NSLS-II
  Tiled exactly as before.
- **Devices-live race** — SAM must not run before the catalog has live objects.
  Gate on a catalog signal / bounded poll; log and degrade if devices are missing
  (SAM scripts referencing an offline device fail per-script, as today).
- **RE double-writing** — ensure lightfall's RE isn't already subscribed to a
  conflicting Tiled writer for CMS; the attach is additive and CMS-specific.

## Open questions

- Which `DeviceCatalog` signal best marks "devices ready" for the SAM gate
  (vs a bounded poll)? Settle during implementation.
- Does any SAM script require an object only `00`–`03` produced beyond
  `RE`/`db`/`cat`/`mig`/config-globals? Confirm while seeding the namespace.
