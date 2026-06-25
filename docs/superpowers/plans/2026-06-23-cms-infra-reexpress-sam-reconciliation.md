# CMS post-login device backend: infra re-expression + SAM reconciliation

Status: DESIGN — needs decisions (architecture, infra-bit scope) and box validation before merge.
Branch: `feat/cms-infra-reexpress` (off `origin/master`).

## Current state on master (post PR #17)

- `create_backend()` returns a `HappiBackend` with background device instantiation. Devices
  come up; the trigger is **intentionally not armed** (plugin.py comment).
- `ProfileSessionBootstrapper` + `CMSSessionTrigger` exist but are **dormant** —
  `CMSSessionTrigger` is never armed anywhere, and `bootstrap()` is only invoked from it.
  So master currently performs **no** RE-infra wiring and **no** SAM hosting.
- `nsls2_provider` adopts a warm-token (Duo identity) read client → per-entry access policy
  filters everything → **data browser lists zero records** (still broken on master).

Net: the post-login device backend is **unfinished** on master, not a working flow to protect.
There is no live infra wiring to double-subscribe against.

## Goal (spec §3 + §4)

One RunEngine — Lightfall's `get_engine().RE` — with `configure_base`'s bits **re-expressed**
onto it (rather than running `00-startup`/adopting a separate profile RE), then host the SAM
framework by seeding the kernel namespace and running the SAM scripts.

## What exists vs. what's missing

| `configure_base` bit | Re-expressed? | Where |
|---|---|---|
| Redis-backed `RE.md` | YES | `run_engine_md.wire_redis_metadata()` |
| Tiled writing (`tiled_inserter`) | YES | `tiled_writer.wire_tiled_writer()` (idempotent, posts to cms/raw) |
| `assets_path()` | YES | `assets.assets_path` + `wire_assets_path()` |
| Service-key read client (browser) | YES | `nsls2_provider` @ `92ffae5` (adopts service client into TiledService) |
| **Kafka document publisher** | **NO** | — gap |
| **BestEffortCallback** | **NO** | — gap |
| **SupplementalData (baselines)** | **NO** | — gap |

SAM hosting (§4 — inject devices, seed namespace, run scripts `81/94/95/96/97/991`) exists on
master in `bootstrap.py` but assumes the namespace was populated by running infra scripts
`00–03` (it reads `RE`, `tiled_writing_client`, `mig`/`cat`, `assets_path` from `namespace`).

## Two candidate architectures

### A. Full re-expression (spec §3 literal)
Never run `00–03`. Re-express ALL `configure_base` bits (add Kafka/BEC/SD to the existing 3)
onto Lightfall's RE. Seed the namespace from the re-expressed bits (`RE = get_engine().RE`,
`cat`/`mig`/`tiled_writing_client` = service client, `assets_path` = our callable). Then run
SAM scripts. Most faithful; requires implementing the 3 missing bits and box-validating
`configure_base` parity.

### B. Hybrid — keep `bootstrap.py`'s SAM machinery, swap its infra source
Keep `bootstrap()`'s phase structure but replace the `run_profile(infra=00–03)` step with the
re-expressed wiring, and seed `RE = get_engine().RE` into the namespace instead of
`engine.adopt()`-ing a profile RE. `_inject_devices` / `_seed_namespace` / SAM run unchanged.
Same missing-bit question (Kafka/BEC/SD) if SAM/ops depend on them.

## Open decisions (need owner input)

1. **Architecture A vs B** — full re-express vs. hybrid-on-bootstrap.
2. **Kafka / BEC / SupplementalData scope** — re-express them (needed for the data broker /
   console live feedback / baselines) or accept their absence for now? This needs the actual
   `nslsii.configure_base` call on the box as the authority (spec §3 note).
3. **Arming** — where to arm SAM hosting (devices-live gate via `CMSSessionTrigger`, or the
   catalog "devices ready" signal). Master left this unarmed.

## Box validation (cannot be done headless — no devices, no console kernel, no pytest-qt)

- [ ] `configure_base` bit parity vs the installed `nslsii` on ws5.
- [ ] Redis `RE.md` connects; `cycle`/`data_session` populate `assets_path`.
- [ ] Tiled writer posts a real run to `cms/raw` with the service key (no 500/401).
- [ ] Data browser lists real runs through the adopted service client.
- [ ] SAM scripts `81/94/95/96/97/991` run in the live kernel against the seeded namespace.
- [ ] Devices stage without "assets_path is not set".

## Implementation steps (after decisions)

1. Bring `92ffae5` (provider service-key read) + the 3 helpers onto the branch.
2. (If chosen) re-express Kafka/BEC/SupplementalData onto `get_engine().RE`.
3. Wire infra at the chosen point; seed the namespace for SAM.
4. Arm SAM hosting on the chosen signal.
5. Unit tests (mock `nslsii`); mark box-validation items above.
6. Open PR (draft) — do NOT merge until box-validated.
