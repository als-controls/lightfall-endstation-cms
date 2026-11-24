# Lightfall ↔ CMS Tiled / profile-collection integration

**Date:** 2026-06-16
**Status:** Design approved; implementation plan pending
**Scope:** CMS (11-BM) beamline deployment only. No off-beamline / read-only path is in scope.
**Repos touched:** `ncs/lightfall` (core) and `lightfall-endstation-cms` (CMS plugin package).

## 1. Problem

Lightfall must run acquisition at NSLS-II CMS *and* read CMS data back for visualization,
against NSLS-II's centrally-hosted Tiled (`tiled.nsls2.bnl.gov`). The CMS beamline's
acquisition environment, data pipeline, and Tiled clients are all defined in the
`cms-profile-collection` (the `data-security` branch, vendored as a git submodule of
`lightfall-endstation-cms`). Two impedance mismatches block a naive integration:

1. **Acquisition.** Lightfall's `BlueskyEngine` creates *its own* `RunEngine`
   (`acquire/engine/bluesky.py:155-178`). The CMS profile creates a *different* `RE` via
   `nslsii.configure_base(...)`, and that `RE` instance carries the entire document-writing
   pipeline (Kafka publisher → `cms/raw`; `tiled_inserter`; `02-tiled-writer.py`'s
   `TiledWriter` → `cms/migration`; the `sd` SupplementalData baseline preprocessor; and a
   Redis-backed `RE.md`). A second, independent `RE` would silently write nothing.

2. **Reading + auth.** Lightfall reads Tiled from a single configured `tiled_url` (default an
   ALS dev box) and browses from the server root. Its only non-trivial auth mode (Keycloak /
   auth-v2 `ServiceKeyAuth`) is hard-wired to ALS's identity provider. NSLS-II read access is
   `from_profile("nsls2", username=None)` → an interactive **Duo push**, producing the `cat`
   (`cms/raw`) and `mig` (`cms/migration`) reading clients. Lightfall has no NSLS-II auth and
   no per-beamline node scoping.

## 2. Core idea

**Lightfall hosts the CMS bluesky session inside its own embedded in-process IPython console,
then adopts — by direct object reference — the `RE`, ophyd devices, and Tiled reading client
that the profile-collection builds in that kernel's namespace.** NSLS-II's tiled/Duo login is
surfaced as Lightfall's beamline login and warms the tiled token cache so the profile loads
non-interactively.

This works because Lightfall's console uses `QtInProcessKernelManager`
(`ui/panels/ipython_panel.py:244`): the kernel's `shell.user_ns` lives in the *same Python
process* as the Qt app. Therefore:

- Inside that kernel `get_ipython()` returns a real IPython shell, so the profile's
  `configure_base(get_ipython().user_ns, ...)` and `ip.prompts = ...` run unmodified — the
  single hardest blocker to running the profile headless simply does not exist here.
- Lightfall reads `RE`, devices, and `mig` out of `shell.user_ns` as live references — same
  object identity, no serialization, no second process, no cross-process messaging.

This is strictly better than the two alternatives considered: "attach to an external IPython
session" (requires a separate process and IPC) and "Lightfall execs the profile in a sandboxed
namespace" (no real IPython shell; would duplicate the `RE` and the writing pipeline). See §7.

## 3. Architecture

```
Lightfall process
├── Qt GUI (engine control, viz panels, device panels, login dialog)
└── In-process IPython kernel  (QtInProcessKernelManager)
        └── shell.user_ns   ← the single shared namespace
              ├── RE, db, bec, sd        (built by nslsii.configure_base)
              ├── smx, pilatus2M, cms…   (ophyd device instances)
              └── mig, cat               (Duo-authed Tiled reading clients)
```

After the startup handshake there is exactly **one** of each shared resource. A scientist
typing `mov(smx, 5)`, `RE(scan(...))`, `<scan(...)`, `sam.measure()`, or `%magics` in the
console drives the **same** RunEngine and devices that Lightfall's GUI buttons and viz operate
on. Lightfall is a *consumer/controller* of the session, not a second producer.

## 4. Components & changes

### 4.1 Lightfall core (`ncs/lightfall`)

1. **Pluggable auth providers.** Add an `auth_provider` plugin-manifest type and an auth
   provider registry. Refactor `ui/dialogs/login_dialog.py` and `main.py:137-177` so providers
   are rendered/selected from the registry instead of the hardcoded `keycloak`/`local`/`pam`
   branches; migrate those three onto the registry so behavior is unchanged for existing
   deployments. The `AuthProvider` ABC (`auth/providers/base.py`) is already the right
   contract and does not change.

2. **`BlueskyEngine.adopt(existing_RE)`.** A code path alongside the current
   "create my own `RE`" in `start()`. Adopts the `configure_base` `RE` *with its subscriptions
   and preprocessors intact* and drives it on the engine's executor so GUI-initiated plans
   remain non-blocking (the existing off-thread submit + off-thread stop/abort dispatch at
   `acquire/engine/bluesky.py:353` are reused).

3. **Engine-wrapper attribute delegation.** The engine wrapper that is bound to
   `user_ns["RE"]` (see 4.2.2) must transparently proxy attribute **get and set** to the
   underlying `RE`: at minimum `.md`, `.md = …` (`proposal_swap` reassigns it), `.subscribe`,
   `.unsubscribe`, `.preprocessors`, `.state`, `.abort`, `.resume`, `.halt`, `.stop`. Implement
   via `__getattr__`/`__setattr__` delegation with an explicit allowlist guard so the wrapper's
   own attributes are not shadowed.

4. **`TiledService.adopt_client(client)`.** Accept a pre-built, pre-authenticated,
   node-scoped client (`mig`). When adopted, skip `from_uri`/auth-mode/proxy construction
   entirely; keep the existing health-check loop and `connection_changed` signals running
   against the adopted client. Because `mig` is already `from_uri(...)['cms/migration']`, the
   browser is automatically scoped to the CMS migration node — no root-level cross-beamline
   listing, no separate node-scoping feature required.

5. **Startup hook.** Expose (a) a "console kernel ready" signal/accessor so an endstation
   package can obtain the live `shell`, and (b) the adopt entry points (engine, tiled service,
   device registry) so the package can orchestrate the handshake after login.

### 4.2 CMS endstation package (`lightfall-endstation-cms`)

1. **`NSLS2TiledAuthProvider`** (contributed via the new `auth_provider` entry point).
   `authenticate(username, ...)` performs the tiled login against `tiled.nsls2.bnl.gov`,
   triggering the Duo push, caches the resulting token (tiled's on-disk token cache, keyed by
   server netloc), and returns a `Session` carrying the BNL username. This is the **preemptive
   warm-up**: because the cache is keyed by server, the subsequent
   `from_profile("nsls2", username=None)` calls inside `00-startup.py` resolve from cache
   **silently — no second Duo push**. The warmed token is the *read* token; the *write* client
   uses the separate `TILED_BLUESKY_WRITING_API_KEY_CMS` API key and needs no push.

2. **Profile-session bootstrapper.** After a successful login, and once the console kernel
   exists, run profile scripts `00 → 97` into the live `shell.user_ns` (EPICS, Redis, Kafka
   wire up exactly as at the beamline today; reads are silent on the warm token). Then perform
   three handoffs, in this order:
   - **`RE` → engine:** `BlueskyEngine.adopt(user_ns["RE"])`, then **after the full profile
     load completes** rebind `user_ns["RE"]` to the engine wrapper. Ordering is load-bearing:
     `02`'s `RE.subscribe(tw)` and all load-time `RE.md[...]` setup must bind to the raw `RE`
     during load.
   - **devices → registry:** retarget the existing `ProfileCollectionBackend` at the live
     namespace (it already extracts ophyd instances; today it runs them in a sandboxed
     namespace with `00/02/03/90-97` blacklisted — here it reads the real, fully-loaded ns).
   - **`mig` → `TiledService.adopt_client(user_ns["mig"])`.**

3. **Manifest** gains `auth_provider` and bootstrapper entries; keeps the existing
   `device_backend` entry.

## 5. Startup sequence (CMS deployment)

1. App starts; plugins load via entry points (CMS manifest → device_backend, auth_provider,
   bootstrapper).
2. Login dialog presents the **NSLS-II (CMS)** provider. User enters BNL username → tiled
   login → **Duo push** → approve → token cached, `Session` established. This replaces ALS
   Keycloak as the beamline login for the CMS deployment.
3. Ensure the in-process console kernel exists (force-initialize it if the panel has not been
   shown yet).
4. Bootstrapper runs profile `00→97` into the kernel namespace (silent reads via warm token;
   EPICS connects).
5. Adopt `RE` (adopt + post-load wrapper rebind), devices, `mig`.
6. GUI live; console is the live CMS session; viz reads `cms/migration`; engine drives the
   profile's `RE`.

## 6. Why this is safe (grounded in investigation)

- **No by-value `RE` captures.** A grep of the profile found zero `self.RE = RE`; every
  reference (`RE.md[...]`, `RE(...)`, `proposal_swap`'s `RE.md = …`) uses the *late-bound
  global name* `RE`. Rebinding `user_ns["RE"]` therefore propagates to every function across
  `81-beam.py`, `91-fit_scan.py`, `94/95-sample*.py`, and the magics.
- **The writing pipeline lives on the `RE` instance, not in the modules.** Kafka publisher,
  `tiled_inserter`, `sd` preprocessor, the Redis `RE.md`, and `02`'s `RE.subscribe(tw)` are
  bolted to the object `configure_base` created. **Adopting** that object preserves all of
  them; **injecting a fresh `RE`** would drop them all (silent data loss + broken
  `tiled_access_tags` authz). Hence adopt-then-wrap, never fresh-inject.
- **Node scoping is free.** `mig` is already scoped to `cms/migration`.

## 7. Alternatives considered (rejected)

- **Lightfall owns/creates the `RE`.** Rejected: would require re-implementing and re-wiring
  the profile's entire document-writing pipeline Lightfall-side, risking drift from the
  canonical beamline profile and silent write loss.
- **Lightfall execs the full profile in a sandboxed (non-IPython) namespace.** Rejected:
  `get_ipython()`/`configure_base`/`ip.prompts` require a real IPython shell; running it
  standalone also implies Lightfall *replaces* the beamline's sole bluesky session and drags
  startup onto the full Redis/Kafka/Duo/EPICS backplane with no clean dev path.
- **Attach to an external running IPython session.** Rejected in favor of hosting the session
  in Lightfall's own in-process kernel: same end state (one `RE`, one namespace) without a
  second process or IPC.
- **Separate static read API key instead of reusing `mig`.** Deferred: reusing the
  Duo-warmed `mig` means one login serves both the profile's reads and Lightfall's reads, and
  avoids provisioning/scoping a second credential. Fallback if `mig` reuse proves unworkable.

## 8. Testing

- **Unit:** auth provider registry + manifest loading; engine-wrapper attribute get/set
  delegation against a mock `RE`; `TiledService.adopt_client` skips construction and runs
  health-checks against the adopted client.
- **Integration (mockable off-beamline):** bootstrapper runs against a *stub* namespace that
  exposes fake `RE`/devices/`mig`; assert all three adoptions wire up and that
  `user_ns["RE"]` is the wrapper only *after* load completes.
- **Beamline acceptance:** one Duo login → profile loads silently → a scan launched from a
  GUI button and `<scan(...)` typed in the console both drive the one `RE`; documents land in
  `cms/migration`; viz reads them back; `tiled_access_tags` populated.

## 9. Open items to confirm at the beamline

1. **Tiled auth provider type** (password+Duo vs OAuth device-code/browser). Decides whether
   `NSLS2TiledAuthProvider.authenticate` takes a typed username/password or drives a browser
   flow, and the exact prompt handling.
2. **Residual stdin during profile load.** Confirm the in-process kernel cleanly handles any
   `input()`/`getpass` the profile might still issue (mitigated by pinning username + warm
   token).
3. **Console-typed `RE(scan)` responsiveness.** Confirm GUI responsiveness and that
   interrupt/abort from the qtconsole in-process kernel is acceptable for a console-initiated
   (as opposed to GUI-button) plan.
4. **qtconsole/ipykernel availability** in the CMS deployment environment (currently optional
   Lightfall deps).
