# CMS catalog-driven SAM hosting — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the CMS post-login rework: stop running the profile's infra scripts `00`–`03` and adopting a separate RunEngine; instead attach the `nslsii.configure_base` bits to **lightfall's own RE**, seed the kernel namespace from the device catalog by name, and run the SAM framework scripts as a **devices-live-gated post-login action**.

**Architecture:** The device backend is already an ordinary background happi backend (PR #14, §1 — done). This plan replaces `bootstrap.py`'s "run `00`–`03` + adopt RE/Tiled + inject devices" with "attach RE bits to `get_engine().RE` + seed namespace by-name", repoints the trigger from `AUTHENTICATED` to a devices-live gate, and flips the CMS panels to non-preload.

**Tech Stack:** Python, lightfall plugin SDK (`DeviceCatalog`, `get_engine`, `HappiBackend`), `nslsii` (NSLS-II-only; **not in the dev venv** → imported lazily, unit-tested mocked), caproto/ophyd, an in-process IPython kernel.

**Source of truth:** approved spec `docs/superpowers/specs/2026-06-20-cms-postlogin-device-backend-design.md`.

## Global Constraints

- `nslsii` is **NSLS-II-only and absent from the dev venv.** All code importing it must do so **lazily** (inside the function), and all unit tests run with `nslsii` **mocked**. The real wiring is the box's authority and is **box-validated on ws5**, not asserted locally.
- Tests run with the **lightfall venv**: `PYTHONPATH=src /c/Users/rp/PycharmProjects/ncs/lightfall/.venv/Scripts/python -m pytest` (the endstation has no venv of its own).
- Do **not** re-implement the SAM scripts (`81/94/95/96/97/991`) — they are the beamline's framework and run as-is in the kernel. Only the infra (`00`–`03`) is re-expressed. happi remains the single device source; the device-DEFINING profile scripts stay unrun.
- The attach to lightfall's RE is **additive and CMS-specific**; do not remove lightfall's existing RE subscriptions.
- Keep the device_backend registry name `cms_profile_collection` (persisted-selection compatibility) — already satisfied.
- `docs/superpowers/` is **not** gitignored in this repo (unlike lightfall) — commit docs normally.

## File Structure

- `manifest.py` — flip the three CMS panels to non-preload (§5).
- `kernel_access.py` — add a by-name catalog accessor used to seed the namespace (§2); read-side veneer accessors unchanged.
- `bootstrap.py` — replace `run infra 00–03 + adopt()` with `attach_infra(RE)` (attach nslsii bits to lightfall's RE) + `seed_namespace()` (by-name + config globals); keep `run_profile`/SAM execution (§3).
- `session_trigger.py` — replace the `AUTHENTICATED` arm with a devices-live-gated, once-only post-login action (§4).
- Tests under `tests/` mirroring the existing `test_*` files (lightfall venv, `nslsii` mocked).

---

### Task 1: CMS panels → non-preload (§5)

**Files:**
- Modify: `src/lightfall_endstation_cms/manifest.py` (the three `panel` entries `cms_sample`/`cms_holder`/`cms_beamline`)
- Test: `tests/test_manifest.py` (create if absent)

**Interfaces:**
- Consumes: `PluginManifest`, `PluginEntry` from `lightfall.plugins.manifest`.
- Produces: nothing downstream depends on the panels being preload.

- [ ] **Step 1: Write the failing test** — assert the three CMS panels are NOT preload and the auth provider IS preload:

```python
from lightfall_endstation_cms.manifest import manifest

def test_only_auth_provider_is_preload():
    by_name = {e.name: e for e in manifest.entries}
    assert by_name["nsls2_tiled"].preload is True
    for panel in ("cms_sample", "cms_holder", "cms_beamline"):
        assert by_name[panel].preload is False
```

- [ ] **Step 2: Run it, watch it fail** — `PYTHONPATH=src .../python -m pytest tests/test_manifest.py -q` → panels assert False fails (currently preload=True).
- [ ] **Step 3: Implement** — remove `preload=True` from the three panel `PluginEntry`s (default is non-preload). Leave the `nsls2_tiled` auth provider `preload=True`.
- [ ] **Step 4: Run it, watch it pass.**
- [ ] **Step 5: Commit** (`feat(cms): CMS panels load post-login (non-preload)`).

**Box-validation:** on ws5, the three CMS panels appear after login (lightfall's post-login `setup_default_layout` registers them) — they had nothing to veneer pre-login anyway.

---

### Task 2: By-name catalog accessor (§2)

**Files:**
- Modify: `src/lightfall_endstation_cms/kernel_access.py`
- Test: `tests/test_kernel_access_by_name.py`

**Interfaces:**
- Consumes: `lightfall.devices.catalog.DeviceCatalog` (the live catalog with instantiated `DeviceInfo._ophyd_device`).
- Produces: `devices_by_name(names: Iterable[str]) -> dict[str, object]` returning live ophyd objects keyed by happi item name (which equals the profile variable name — `smx`, `pilatus2M`, …). Missing/uninstantiated names are omitted (logged).

- [ ] **Step 1: Write the failing test** with a fake catalog exposing `DeviceInfo`-like entries (name + `_ophyd_device`):

```python
def test_devices_by_name_returns_live_objects(monkeypatch):
    from lightfall_endstation_cms import kernel_access
    sentinel = object()
    fake = _FakeCatalog({"smx": sentinel, "pilatus2M": None})  # None = not yet live
    monkeypatch.setattr(kernel_access, "_device_catalog", lambda: fake)
    result = kernel_access.devices_by_name(["smx", "pilatus2M", "absent"])
    assert result == {"smx": sentinel}   # only live, present devices
```

- [ ] **Step 2: Run it, watch it fail** (function missing).
- [ ] **Step 3: Implement** `devices_by_name` reading the catalog (reuse/extract the existing `_device_catalog()` accessor from `bootstrap.py` into `kernel_access.py` so both share it). Return only names whose `_ophyd_device` is non-None.
- [ ] **Step 4: Run it, watch it pass.**
- [ ] **Step 5: Commit.**

---

### Task 3: Devices-live gate + once-only post-login action (§4)

**Files:**
- Modify: `src/lightfall_endstation_cms/session_trigger.py`
- Test: `tests/test_session_trigger_gating.py` (extend existing trigger tests)

**Interfaces:**
- Consumes: a `DeviceCatalog` "devices ready" signal **or** a bounded poll (resolve the open question against the real `DeviceCatalog` API while implementing — prefer a signal if one exists, else a `QTimer` bounded poll of "all/most backend devices have `_ophyd_device`").
- Produces: a once-only `_run_when_devices_live(callback)` that fires `callback` on the GUI thread exactly once when devices are live, with a bounded timeout that logs-and-degrades (runs anyway) if devices never fully arrive.

- [ ] **Step 1: Write failing tests** — (a) the action fires once after the catalog reports devices live; (b) it does NOT fire before; (c) repeated "ready" emits fire it only once; (d) on timeout it logs and still runs (degraded). Drive with a fake catalog/signal and a fake clock/poll.
- [ ] **Step 2: Run, watch fail.**
- [ ] **Step 3: Implement** — replace the `AUTHENTICATED`-armed `CMSSessionTrigger._on_state_changed` path with the devices-live gate. Keep the GUI-thread marshaling (`invoke_in_main_thread`) and the `_done`/`_running` once-only guards already present. The post-login wave (which now loads this plugin) is the entry point; the action waits for devices, then calls the bootstrapper.
- [ ] **Step 4: Run, watch pass.**
- [ ] **Step 5: Commit.**

**Box-validation:** SAM does not run before the catalog has live ophyd objects; a missing device degrades per-SAM-script (as today), not catastrophically.

---

### Task 4: Re-implement infra — attach nslsii bits to lightfall's RE + seed namespace (§3) — **box-gated crux**

**Files:**
- Modify: `src/lightfall_endstation_cms/bootstrap.py` (replace `adopt()` + the `00`–`03` infra run with `attach_infra` + `seed_namespace`)
- Test: `tests/test_bootstrap_infra_attach.py`

**Interfaces:**
- Consumes: `lightfall.acquire.engine.bluesky.get_engine().RE` (lightfall's RE); the by-name accessor (Task 2); `nslsii` (lazy import).
- Produces: `attach_infra(re) -> None` attaching, against a **fake RE**: `RE.md = RedisJSONDict(...)`; `RE.subscribe(TiledInserter(...).insert)`; `RE.subscribe(kafka_publisher)`; `RE.subscribe(BestEffortCallback())`; `RE.preprocessors.append(SupplementalData(...))`. And `seed_namespace(ns: dict) -> None` putting `RE`, `db`/`cat`, `mig`, proposal/assets helpers, config globals, and the by-name devices into the kernel namespace.

- [ ] **Step 1: Write the failing test (nslsii mocked)** — `monkeypatch` a fake `nslsii` module + fakes for `RedisJSONDict`/`TiledInserter`/etc., pass a fake RE recording `.md`, `.subscribe(...)`, `.preprocessors`, and assert `attach_infra` wired each bit exactly once and additively (did not clear pre-existing subscriptions). Assert `seed_namespace` populates the expected keys.
- [ ] **Step 2: Run, watch fail.**
- [ ] **Step 3: Implement** the attach + seed. **The exact bit set and Kafka wiring are confirmed against the installed `nslsii.configure_base` on ws5** (spec §3 + the per-bit table) — replicate, do not invent. Import `nslsii` lazily; on `ImportError`/connection failure, log and degrade (a SAM script referencing an unattached bit fails per-script, as today).
- [ ] **Step 4: Run the mocked unit test, watch pass.**
- [ ] **Step 5: Commit.**

**Box-validation (ws5 — required, the real authority):**
- `attach_infra` wires bit-for-bit what `nslsii.configure_base` does (Redis md → `info.cms.nsls2.bnl.gov`; TiledInserter → `nsls2/cms/raw`; Kafka publisher; BEC; SupplementalData baseline).
- A real scan through lightfall's RE writes to NSLS-II Tiled **exactly as before** and publishes to Kafka; `RE.md` round-trips through Redis.
- lightfall's RE is not already bound to a conflicting CMS Tiled writer (no double-write).

---

### Task 5: Rewire the bootstrapper to infra-attach + by-name seeding; run SAM (§3/§4 integration)

**Files:**
- Modify: `src/lightfall_endstation_cms/bootstrap.py` (`ProfileSessionBootstrapper`)
- Test: `tests/test_bootstrap_flow.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces: a `bootstrap()` that, on the GUI thread, runs: `attach_infra(get_engine().RE)` → `seed_namespace(shell.user_ns)` (by-name devices + config globals) → `run_profile(shell, SAM_scripts)`. The `00`–`03` infra run and `adopt()` are removed; `DEFAULT_INFRA_KEEP` is dropped.

- [ ] **Step 1: Write the failing test** — with infra-attach/seed/run_profile mocked, assert `bootstrap()` calls them in order, only runs the SAM scripts (not `00`–`03`), and is once-only.
- [ ] **Step 2–4: Run/implement/verify** (remove the `00`–`03` + `adopt` path; keep `run_profile` and the SAM keep-set).
- [ ] **Step 5: Commit.**

**Box-validation:** the CMS panels show live SAM objects (`kernel_access` read-side unchanged); the console and panels share the same kernel SAM objects; a SAM script can drive a device sourced from happi.

---

## Self-review checklist (run before execution)

- Spec coverage: §1 done (PR #14); §2 Task 2; §3 Tasks 4–5; §4 Task 3; §5 Task 1. ✓
- No-placeholder: Task 4's infra body is intentionally box-confirmed (nslsii is the authority); its unit test is mock-based per the Global Constraints — this is the honest shape for NSLS-II-only code, not a placeholder.
- Open questions to settle during implementation (from the spec): which `DeviceCatalog` signal marks "devices ready" (vs bounded poll); whether any SAM script needs an object only `00`–`03` produced beyond `RE`/`db`/`cat`/`mig`/config-globals.

## Execution note

Tasks 1–3 and the mocked seams of 4–5 are fully local (lightfall venv, `nslsii` mocked). Task 4's real parity and Tasks' box-validation lines require **ws5 with the live beamline + `nslsii`**, ideally with Ron available — that is the highest-risk surface (re-creating `configure_base`). Recommend executing 1–3 + the mocked 4/5 as a first PR, then box-validating the infra parity before relying on it.
