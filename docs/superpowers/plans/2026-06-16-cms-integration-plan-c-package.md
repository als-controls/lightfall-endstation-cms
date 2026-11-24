# CMS Integration — Plan C: CMS Endstation Package Wiring

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Lightfall, at the CMS beamline, log in via NSLS-II (tiled/Duo), run the CMS profile-collection in its embedded in-process console, and adopt that session's `RunEngine`, ophyd devices, and `cms/migration` reading client — so plans run and data is read back, all through one shared session.

**Architecture:** One small core accessor (`IPythonPanel.ensure_kernel()` + `shell`) so a package can reach the live kernel cleanly. The CMS package then adds: a `ProfileCollectionBackend.populate_from_namespace()` so devices come from the *live* (fully-loaded) namespace rather than a sandboxed one; an `NSLS2TiledAuthProvider` + `NSLS2AuthPlugin` (Plan B's `auth_provider` type) that warms the tiled/Duo token cache; and a `ProfileSessionBootstrapper` that, on successful login, runs the profile into the kernel and performs the three adoptions (engine via Plan A's `BlueskyEngine.adopt` + `ConsoleREProxy`; devices; Tiled via Plan A's `adopt_client`).

**Tech Stack:** Python 3.10+, PySide6, IPython in-process kernel (`shell.run_cell`), Bluesky, ophyd, tiled client, pytest.

**Depends on:** Plan A (`BlueskyEngine.adopt`, `ConsoleREProxy`, `TiledService.adopt_client`) and Plan B (`AuthProviderPlugin`, `AuthProviderRegistry`, `auth_provider` loader wiring) must be merged first.

## Global Constraints

- Python `>=3.10`; `from __future__ import annotations`.
- Two repos:
  - **lightfall** core change (Task 1): CWD `C:\Users\rp\PycharmProjects\ncs\lightfall`, tests `.venv/Scripts/python -m pytest`.
  - **CMS package** (Tasks 2-5): CWD `C:\Users\rp\PycharmProjects\ncs\lightfall-endstation-cms`. Use that repo's venv if present, else the lightfall venv with `PYTHONPATH=src`.
- CMS deployment only — no off-beamline path. Off-beamline, the bootstrapper simply never triggers (no AUTHENTICATED state), so Lightfall runs without the CMS session.
- Tiled write API key env var is `TILED_BLUESKY_WRITING_API_KEY_CMS` (unchanged; used by the profile, not by Lightfall).
- Reading node is `cms/migration`; reading server is `https://tiled.nsls2.bnl.gov`.
- Commit after each task. Core-repo task commits in the lightfall repo; package task commits in the CMS repo.

## Beamline-only behavior (not unit-tested; see Acceptance section)

Real Duo push, real `from_profile("nsls2")`, executing the full 200 KB profile against live EPICS/Redis/Kafka, and DeviceCatalog refresh after late population only run at the beamline. Unit tests here exercise the seams against **stub** namespaces/providers, exactly as spec §8 prescribes. Open items from spec §9 (provider type, residual stdin, console-`RE` responsiveness, qtconsole availability) are confirmed during Acceptance.

---

### Task 1 (lightfall core): public kernel accessor on `IPythonPanel`

**Files:**
- Modify: `src/lightfall/ui/panels/ipython_panel.py` (add `ensure_kernel()` + `shell` property)
- Test: `tests/ui/panels/test_ipython_panel_shell.py`

**Interfaces:**
- Consumes: existing `self._kernel_manager`, `_setup_jupyter_widget()` (`ipython_panel.py:238`).
- Produces: `IPythonPanel.ensure_kernel() -> bool` (initializes the in-process kernel if not yet created; returns whether a kernel is available) and `IPythonPanel.shell` (property returning `self._kernel_manager.kernel.shell` or `None`). These let an external package obtain the live IPython shell without reaching into private attributes.

- [ ] **Step 1: Write the failing test**

```python
# tests/ui/panels/test_ipython_panel_shell.py
from __future__ import annotations

import pytest

pytest.importorskip("qtconsole")
pytest.importorskip("ipykernel")

from lightfall.ui.panels.ipython_panel import IPythonPanel


def test_ensure_kernel_then_shell_available(qapp):
    panel = IPythonPanel()
    assert panel.ensure_kernel() is True
    shell = panel.shell
    assert shell is not None
    # The shell is a usable IPython InteractiveShell: it has a user namespace.
    assert hasattr(shell, "user_ns")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/ui/panels/test_ipython_panel_shell.py -v`
Expected: FAIL with `AttributeError: 'IPythonPanel' object has no attribute 'ensure_kernel'`.

- [ ] **Step 3: Implement the accessor**

Read `ipython_panel.py` around `_setup_jupyter_widget` (line 238) and the `__init__` to confirm `self._kernel_manager` starts `None` and `_qtconsole_available`. Add these methods to `IPythonPanel`:

```python
    def ensure_kernel(self) -> bool:
        """Ensure the in-process IPython kernel exists.

        Initializes the kernel (and Jupyter widget) if it has not been
        created yet. Returns True if a kernel is available afterwards.
        """
        if self._kernel_manager is not None:
            return True
        if not self._qtconsole_available:
            return False
        self._setup_jupyter_widget()
        return self._kernel_manager is not None

    @property
    def shell(self):
        """The live IPython InteractiveShell, or None if no kernel."""
        if self._kernel_manager is None:
            return None
        return self._kernel_manager.kernel.shell
```

> Verify `_setup_jupyter_widget` is safe to call directly (it is what the panel calls on first display). If the panel defers it to a `showEvent`, calling it here is still correct — it is idempotent-guarded by the `self._kernel_manager is not None` check above.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/ui/panels/test_ipython_panel_shell.py -v`
Expected: PASS (or SKIP if qtconsole/ipykernel absent — install them in the test env: `.venv/Scripts/python -m pip install qtconsole ipykernel`).

- [ ] **Step 5: Commit (lightfall repo)**

```bash
git add src/lightfall/ui/panels/ipython_panel.py tests/ui/panels/test_ipython_panel_shell.py
git commit -m "feat(console): public ensure_kernel() + shell accessor on IPythonPanel"
```

---

### Task 2 (CMS package): `ProfileCollectionBackend.populate_from_namespace()`

**Files:**
- Modify: `src/lightfall_endstation_cms/backends/profile_collection.py`
- Test: `tests/test_backend_populate.py`

**Interfaces:**
- Consumes: existing `_build_device_catalog` (`profile_collection.py:157`), `extract_ophyd_devices` (`loader.py:189`).
- Produces: `ProfileCollectionBackend.populate_from_namespace(namespace: dict[str, Any]) -> int` — extracts ophyd instances from an already-populated (live) namespace, builds the device catalog from them, marks the backend connected, and returns the device count. Unlike `connect()`, it does **not** run `load_profile()` — the caller supplies the live namespace.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backend_populate.py
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ophyd.sim import SynAxis

from lightfall_endstation_cms.backends.profile_collection import ProfileCollectionBackend


def test_populate_from_namespace_builds_catalog():
    backend = ProfileCollectionBackend()
    ns = {"smx": SynAxis(name="smx"), "_hidden": 123, "note": "not a device"}

    count = backend.populate_from_namespace(ns)

    assert count == 1
    assert backend.is_connected is True
    dev = backend.get_device_by_name("smx")
    assert dev is not None
    assert dev.display_name == "smx"
```

- [ ] **Step 2: Run test to verify it fails**

Run (from the CMS repo): `python -m pytest tests/test_backend_populate.py -v`
Expected: FAIL with `AttributeError: 'ProfileCollectionBackend' object has no attribute 'populate_from_namespace'`.

- [ ] **Step 3: Implement the method**

Add to `ProfileCollectionBackend` (after `connect()`):

```python
    def populate_from_namespace(self, namespace: dict[str, Any]) -> int:
        """Build the device catalog from an already-populated namespace.

        Used when the CMS profile-collection has been executed in Lightfall's
        live IPython kernel (by the ProfileSessionBootstrapper) rather than in
        a sandboxed namespace. The full profile is the source of truth.

        Args:
            namespace: A namespace dict (e.g. the kernel's ``shell.user_ns``).

        Returns:
            Number of ophyd devices extracted and cataloged.
        """
        from lightfall_endstation_cms.loader import extract_ophyd_devices

        self._namespace = namespace
        ophyd_devices = extract_ophyd_devices(namespace)
        self._build_device_catalog(ophyd_devices)
        self._connected = True
        logger.info(
            "CMS backend populated from live namespace: {} devices",
            len(self._devices),
        )
        return len(self._devices)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_backend_populate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit (CMS repo)**

```bash
git add src/lightfall_endstation_cms/backends/profile_collection.py tests/test_backend_populate.py
git commit -m "feat(backend): populate device catalog from a live namespace"
```

---

### Task 3 (CMS package): `NSLS2TiledAuthProvider` + `NSLS2AuthPlugin`

**Files:**
- Create: `src/lightfall_endstation_cms/auth/nsls2_provider.py`
- Create: `src/lightfall_endstation_cms/auth/__init__.py` (empty)
- Test: `tests/test_nsls2_auth.py`

**Interfaces:**
- Consumes: `AuthProvider` (`lightfall.auth.providers.base`), `AuthProviderPlugin` (`lightfall.plugins.auth_provider_plugin`, Plan B), `Session` (`lightfall.auth.session`).
- Produces:
  - `NSLS2TiledAuthProvider(AuthProvider)` — `authenticate(username, password=None, **kwargs)` calls a `_tiled_login(username)` seam (the beamline-specific tiled/Duo login that warms the token cache) and, on success, returns a `Session`. The `_tiled_login` seam is overridable so it can be stubbed in tests and finalized at the beamline (spec §9 open item 1).
  - `NSLS2AuthPlugin(AuthProviderPlugin)` — `name="nsls2_tiled"`, `display_name="NSLS-II (CMS)"`, `requires_username=True`, `requires_password=False`, `create_provider()` returns an `NSLS2TiledAuthProvider`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_nsls2_auth.py
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.auth.nsls2_provider import (
    NSLS2AuthPlugin,
    NSLS2TiledAuthProvider,
)


def test_plugin_metadata():
    plugin = NSLS2AuthPlugin()
    assert plugin.name == "nsls2_tiled"
    assert plugin.display_name == "NSLS-II (CMS)"
    assert plugin.requires_username is True
    assert plugin.requires_password is False
    assert isinstance(plugin.create_provider(), NSLS2TiledAuthProvider)


def test_authenticate_warms_login_and_returns_session():
    calls = {}

    class _TestProvider(NSLS2TiledAuthProvider):
        def _tiled_login(self, username):
            calls["user"] = username
            return True  # pretend Duo succeeded + token cached

    provider = _TestProvider()
    session = asyncio.run(provider.authenticate(username="rond"))

    assert calls["user"] == "rond"
    assert session is not None
    assert session.user.username == "rond"


def test_authenticate_returns_none_on_login_failure():
    class _FailProvider(NSLS2TiledAuthProvider):
        def _tiled_login(self, username):
            return False

    provider = _FailProvider()
    assert asyncio.run(provider.authenticate(username="rond")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_nsls2_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lightfall_endstation_cms.auth'`.

- [ ] **Step 3: Implement the provider + plugin**

```python
# src/lightfall_endstation_cms/auth/nsls2_provider.py
"""NSLS-II tiled/Duo authentication for the CMS deployment.

Authenticating here performs the tiled login against tiled.nsls2.bnl.gov,
which fires a Duo push and caches the resulting token on disk. Because tiled
keys its token cache by server, the profile-collection's later
``from_profile("nsls2")`` reads resolve from that warm cache with no second
Duo push.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from lightfall.auth.providers.base import AuthProvider
from lightfall.plugins.auth_provider_plugin import AuthProviderPlugin

TILED_URI = "https://tiled.nsls2.bnl.gov"
TILED_PROFILE = "nsls2"


class NSLS2TiledAuthProvider(AuthProvider):
    """Auth provider that logs into NSLS-II tiled (Duo) and warms the token cache."""

    @property
    def name(self) -> str:
        return "nsls2_tiled"

    @property
    def supports_password_auth(self) -> bool:
        return False  # username + Duo push, no password typed into Lightfall

    @property
    def supports_browser_auth(self) -> bool:
        return False

    def _tiled_login(self, username: str) -> bool:
        """Perform the interactive tiled login (Duo). BEAMLINE SEAM.

        Returns True if a token was obtained and cached. Overridden in tests.
        Finalize against the real provider type during Acceptance (spec §9).

        Reference implementation (confirm at beamline)::

            from tiled.client import from_uri
            client = from_uri(TILED_URI, username=username or None)
            # touching the client forces auth; token caches to disk
            _ = client.context
            return True
        """
        from tiled.client import from_uri

        client = from_uri(TILED_URI, username=username or None)
        _ = client.context  # force the auth handshake (Duo) + token cache
        logger.info("NSLS-II tiled login complete for '{}'", username)
        return True

    async def authenticate(
        self,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> Any | None:
        from lightfall.auth.policy import Role
        from lightfall.auth.session import Session, User

        if not username:
            logger.warning("NSLS-II login requires a username")
            return None

        try:
            ok = self._tiled_login(username)
        except Exception:
            logger.exception("NSLS-II tiled login failed")
            return None
        if not ok:
            return None

        user = User(
            username=username,
            display_name=username,
            role=Role.USER,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        return Session(user=user)

    async def logout(self, session: Any) -> None:
        return None

    async def refresh(self, session: Any) -> Any | None:
        return session

    async def check_connectivity(self) -> bool:
        return True


class NSLS2AuthPlugin(AuthProviderPlugin):
    """Plugin that contributes the NSLS-II (CMS) login to the login dialog."""

    @property
    def name(self) -> str:
        return "nsls2_tiled"

    @property
    def display_name(self) -> str:
        return "NSLS-II (CMS)"

    @property
    def requires_username(self) -> bool:
        return True

    @property
    def requires_password(self) -> bool:
        return False

    def create_provider(self) -> NSLS2TiledAuthProvider:
        return NSLS2TiledAuthProvider()
```

> Implementer note: confirm the exact constructor signatures of `Session`, `User`, and `Role` in `lightfall/auth/session.py` and `lightfall/auth/policy.py`, and adjust the `User(...)`/`Session(...)` construction to match (the test only relies on `session.user.username`). This is the one spot where field names must be verified against the live auth model before the test will pass.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_nsls2_auth.py -v`
Expected: PASS (after matching the `Session`/`User` constructor).

- [ ] **Step 5: Commit (CMS repo)**

```bash
git add src/lightfall_endstation_cms/auth/ tests/test_nsls2_auth.py
git commit -m "feat(auth): NSLS-II tiled/Duo auth provider + plugin"
```

---

### Task 4 (CMS package): `ProfileSessionBootstrapper` (the three adoptions)

**Files:**
- Create: `src/lightfall_endstation_cms/bootstrap.py`
- Test: `tests/test_bootstrap_adopt.py`

**Interfaces:**
- Consumes: `get_engine()` (`lightfall.acquire.engine`), `BlueskyEngine.adopt` (Plan A), `ConsoleREProxy` (`lightfall.acquire.engine.console_proxy`, Plan A), `TiledService.adopt_client` (Plan A), `ProfileCollectionBackend.populate_from_namespace` (Task 2), `shell.run_cell`, `shell.user_ns`.
- Produces:
  - `ProfileSessionBootstrapper(backend)` with:
    - `run_profile(shell) -> None` — executes profile scripts `00`…`97` into `shell` via `shell.run_cell` (BEAMLINE behavior; thin, calls a `_profile_scripts()` seam so tests can bypass real files).
    - `adopt(namespace) -> None` — performs the three adoptions: `get_engine().adopt(namespace["RE"])`, then `namespace["RE"] = ConsoleREProxy(get_engine())`; `self._backend.populate_from_namespace(namespace)`; `TiledService.get_instance().adopt_client(namespace["mig"], url="https://tiled.nsls2.bnl.gov")`.
    - `bootstrap(shell) -> None` — `run_profile(shell)` then `adopt(shell.user_ns)`.

- [ ] **Step 1: Write the failing test (adopt seam against a stub namespace)**

```python
# tests/test_bootstrap_adopt.py
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ophyd.sim import SynAxis

from lightfall_endstation_cms.backends.profile_collection import ProfileCollectionBackend
from lightfall_endstation_cms.bootstrap import ProfileSessionBootstrapper


def test_adopt_wires_engine_devices_and_tiled(monkeypatch):
    # Stub namespace as if the profile had run.
    fake_re = MagicMock(name="RE")
    fake_mig = MagicMock(name="mig")
    ns = {"RE": fake_re, "mig": fake_mig, "smx": SynAxis(name="smx")}

    fake_engine = MagicMock(name="engine")
    fake_engine.RE = fake_re
    fake_tiled = MagicMock(name="TiledService")

    import lightfall_endstation_cms.bootstrap as boot
    monkeypatch.setattr(boot, "get_engine", lambda: fake_engine)
    monkeypatch.setattr(boot.TiledService, "get_instance", classmethod(lambda cls: fake_tiled))

    backend = ProfileCollectionBackend()
    bootstrapper = ProfileSessionBootstrapper(backend)

    bootstrapper.adopt(ns)

    # Engine adopted the profile's RE.
    fake_engine.adopt.assert_called_once_with(fake_re)
    # RE name rebound to a ConsoleREProxy (callable; delegates to engine.RE).
    from lightfall.acquire.engine.console_proxy import ConsoleREProxy
    assert isinstance(ns["RE"], ConsoleREProxy)
    # Devices populated from the live namespace.
    assert backend.is_connected is True
    assert backend.get_device_by_name("smx") is not None
    # mig adopted as the Tiled reading client.
    fake_tiled.adopt_client.assert_called_once()
    args, kwargs = fake_tiled.adopt_client.call_args
    assert args[0] is fake_mig
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bootstrap_adopt.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lightfall_endstation_cms.bootstrap'`.

- [ ] **Step 3: Implement the bootstrapper**

```python
# src/lightfall_endstation_cms/bootstrap.py
"""Run the CMS profile in Lightfall's console kernel and adopt its objects."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from lightfall.acquire.engine import get_engine
from lightfall.acquire.engine.console_proxy import ConsoleREProxy
from lightfall.services.tiled_service import TiledService

TILED_URI = "https://tiled.nsls2.bnl.gov"


class ProfileSessionBootstrapper:
    """Loads the CMS profile into the live kernel and adopts RE/devices/mig."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def _profile_scripts(self) -> list[Path]:
        """Ordered profile scripts to run. BEAMLINE SEAM (overridden in tests)."""
        from lightfall_endstation_cms.loader import _get_profile_path

        startup = _get_profile_path()
        return sorted(
            p for p in startup.glob("[0-9]*.py")
            if not p.name.endswith((".pybak", ".bak")) and "_new." not in p.name
        )

    def run_profile(self, shell: Any) -> None:
        """Execute the profile scripts into the kernel shell (beamline)."""
        for script in self._profile_scripts():
            logger.info("Running profile script into console: {}", script.name)
            shell.user_ns["__file__"] = str(script)
            result = shell.run_cell(script.read_text(encoding="utf-8"), store_history=False)
            if getattr(result, "error_in_exec", None) is not None:
                logger.error("Profile script {} raised: {}", script.name, result.error_in_exec)

    def adopt(self, namespace: dict[str, Any]) -> None:
        """Adopt RE, devices, and the mig reading client from the namespace.

        Must run AFTER the full profile load so that load-time
        ``RE.subscribe(...)`` / ``RE.md[...]`` wiring binds to the raw RE.
        """
        engine = get_engine()

        # 1) Engine adopts the profile's RE (subscriptions/preprocessors intact),
        #    then rebind the console name `RE` to a GUI-safe proxy.
        engine.adopt(namespace["RE"])
        namespace["RE"] = ConsoleREProxy(engine)
        logger.info("Adopted profile RunEngine; console RE is now a ConsoleREProxy")

        # 2) Devices come from the live namespace.
        n = self._backend.populate_from_namespace(namespace)
        logger.info("Adopted {} devices from profile namespace", n)

        # 3) Reading client: the Duo-authed, cms/migration-scoped `mig`.
        mig = namespace.get("mig")
        if mig is not None:
            TiledService.get_instance().adopt_client(mig, url=TILED_URI)
            logger.info("Adopted 'mig' as the Tiled reading client")
        else:
            logger.warning("Profile namespace has no 'mig' client; Tiled read not adopted")

    def bootstrap(self, shell: Any) -> None:
        """Full handshake: run the profile, then adopt its objects."""
        self.run_profile(shell)
        self.adopt(shell.user_ns)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_bootstrap_adopt.py -v`
Expected: PASS.

- [ ] **Step 5: Commit (CMS repo)**

```bash
git add src/lightfall_endstation_cms/bootstrap.py tests/test_bootstrap_adopt.py
git commit -m "feat(cms): ProfileSessionBootstrapper adopts RE/devices/mig"
```

---

### Task 5 (CMS package): manifest wiring + post-login trigger

**Files:**
- Modify: `src/lightfall_endstation_cms/manifest.py` (add the `auth_provider` entry)
- Create: `src/lightfall_endstation_cms/session_trigger.py` (subscribe to auth state; run the bootstrapper once)
- Modify: `src/lightfall_endstation_cms/backends/profile_collection.py` (the `device_backend` plugin `create_backend()` also arms the trigger)
- Test: `tests/test_session_trigger.py`

**Interfaces:**
- Consumes: `SessionManager` + `AuthState` (`lightfall.auth.session`), `LFApplication.main_window` → `get_panel("lightfall.panels.ipython")`/`add_panel(...)` + `IPythonPanel.ensure_kernel()`/`shell` (Task 1), `ProfileSessionBootstrapper` (Task 4).
- Produces: `CMSSessionTrigger(backend)` — `arm()` connects to `SessionManager.get_instance().state_changed`; when state becomes `AuthState.AUTHENTICATED` for the first time, it obtains the console shell and calls `ProfileSessionBootstrapper(backend).bootstrap(shell)`, exactly once.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session_trigger.py
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.session_trigger import CMSSessionTrigger


def test_trigger_runs_bootstrap_once_on_authenticated(monkeypatch):
    fake_shell = MagicMock(name="shell")
    fake_bootstrapper = MagicMock(name="bootstrapper")

    import lightfall_endstation_cms.session_trigger as st
    # Stub shell acquisition and bootstrapper construction.
    monkeypatch.setattr(CMSSessionTrigger, "_get_shell", lambda self: fake_shell)
    monkeypatch.setattr(st, "ProfileSessionBootstrapper", lambda backend: fake_bootstrapper)

    trigger = CMSSessionTrigger(backend=MagicMock())
    # Simulate two AUTHENTICATED transitions; bootstrap must run only once.
    trigger._on_state_changed(st.AuthState.AUTHENTICATED)
    trigger._on_state_changed(st.AuthState.AUTHENTICATED)

    fake_bootstrapper.bootstrap.assert_called_once_with(fake_shell)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_session_trigger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lightfall_endstation_cms.session_trigger'`.

- [ ] **Step 3: Implement the trigger**

```python
# src/lightfall_endstation_cms/session_trigger.py
"""Run the CMS profile bootstrap once the user authenticates."""

from __future__ import annotations

from typing import Any

from loguru import logger

from lightfall.auth.session import AuthState, SessionManager

from lightfall_endstation_cms.bootstrap import ProfileSessionBootstrapper


class CMSSessionTrigger:
    """Arms a one-shot profile bootstrap on the first AUTHENTICATED transition."""

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        self._done = False

    def arm(self) -> None:
        SessionManager.get_instance().state_changed.connect(self._on_state_changed)
        logger.info("CMS session trigger armed (waiting for NSLS-II login)")

    def _get_shell(self) -> Any | None:
        """Obtain the live console shell, creating the kernel if needed."""
        from lightfall.core import LFApplication

        app = LFApplication.get_instance()
        window = app.main_window if app else None
        if window is None:
            return None
        panel = window.get_panel("lightfall.panels.ipython") or window.add_panel(
            "lightfall.panels.ipython"
        )
        if panel is None or not panel.ensure_kernel():
            return None
        return panel.shell

    def _on_state_changed(self, new_state: Any, old_state: Any = None) -> None:
        # SessionManager.state_changed emits (new_state, old_state); the second
        # arg is optional so tests can call with just the new state.
        if self._done or new_state != AuthState.AUTHENTICATED:
            return
        shell = self._get_shell()
        if shell is None:
            logger.error("CMS bootstrap: console shell unavailable; cannot run profile")
            return
        self._done = True
        logger.info("CMS login detected — running profile-collection bootstrap")
        ProfileSessionBootstrapper(self._backend).bootstrap(shell)
```

- [ ] **Step 4: Add the `auth_provider` manifest entry and arm the trigger**

In `src/lightfall_endstation_cms/manifest.py`, add an entry to `plugins=[...]`:

```python
        PluginEntry(
            type_name="auth_provider",
            name="nsls2_tiled",
            import_path="lightfall_endstation_cms.auth.nsls2_provider:NSLS2AuthPlugin",
            metadata={"beamline": "11-BM CMS"},
        ),
```

In `src/lightfall_endstation_cms/backends/profile_collection.py`, find the `device_backend` plugin class whose `create_backend()` returns a `ProfileCollectionBackend` (it is referenced by the manifest's existing `device_backend` entry; read the manifest to locate the exact class). In that `create_backend()`, after constructing the backend, arm the trigger:

```python
        backend = ProfileCollectionBackend()
        from lightfall_endstation_cms.session_trigger import CMSSessionTrigger

        CMSSessionTrigger(backend).arm()
        return backend
```

> Note: the backend is added to the DeviceCatalog empty at plugin-load and populated later when the trigger fires. Confirm during Acceptance that DeviceCatalog reflects late-added devices (it may need a refresh/`backend_changed` signal); if not, emit the catalog's device-changed notification from `populate_from_namespace`. Tracked as an Acceptance item, not a unit test.

- [ ] **Step 5: Run the trigger test + full CMS suite**

Run: `python -m pytest tests/test_session_trigger.py -v`
Expected: PASS.
Run: `python -m pytest tests/ -q`
Expected: PASS (existing loader smoke test + new tests).

- [ ] **Step 6: Commit (CMS repo)**

```bash
git add src/lightfall_endstation_cms/manifest.py src/lightfall_endstation_cms/session_trigger.py src/lightfall_endstation_cms/backends/profile_collection.py tests/test_session_trigger.py
git commit -m "feat(cms): contribute NSLS-II auth + arm profile bootstrap on login"
```

---

## Acceptance (beamline, manual — spec §8 + §9)

Run at a CMS workstation with EPICS/Redis/Kafka reachable and qtconsole/ipykernel installed:

1. Launch Lightfall (CMS config). The login dialog shows an **"NSLS-II (CMS)"** button.
2. Enter BNL username → click it → **one Duo push** → approve. Session becomes AUTHENTICATED.
3. The console runs the profile (watch the log for each `Running profile script …`); `from_profile("nsls2")` resolves silently (no second Duo). Confirm spec §9 items: provider type (password vs device-code), no blocking `input()`/stdin hang, qtconsole present.
4. In the console, `RE` is a `ConsoleREProxy`; `smx`, `pilatus2M`, `cms`, `mig` exist in the namespace. `RE.md["data_session"]` returns the Redis-backed value.
5. Launch a short scan from a Lightfall GUI button AND type `<count([pilatus2M])` in the console — both drive the one RunEngine; the GUI stays responsive during the console scan and Abort works (spec §9 item 3).
6. Documents land in `cms/migration`; the Tiled browser (reading via adopted `mig`) lists the run and viz renders it; `tiled_access_tags` is populated.
7. Confirm DeviceCatalog shows the CMS devices (Task 5 note — late population refresh).

## Self-Review

**Spec coverage (Plan C's slice):**
- §4.1.5 startup hook → Task 1 (`ensure_kernel`/`shell`). ✓
- §4.2.1 `NSLS2TiledAuthProvider` warming the token → Task 3. ✓
- §4.2.2 bootstrapper: run profile + three adoptions, post-load RE rebind ordering → Task 4. ✓
- §4.2.2 devices from live namespace → Task 2. ✓
- §4.2.3 manifest gains `auth_provider`; keeps `device_backend`; post-login trigger → Task 5. ✓
- §6 safety (adopt-not-inject; post-load rebind) realized in Task 4's `adopt()`. ✓
- §9 open items → Acceptance section. ✓

**Placeholder scan:** No TBD/TODO. The `_tiled_login`, `_profile_scripts`, and `_get_shell` "seams" are real, implemented methods (with reference bodies) explicitly designed to be stubbed in tests and confirmed at the beamline — not placeholders. Three implementer notes ask for verification of `Session`/`User` fields, the `device_backend` plugin class location, and DeviceCatalog refresh; these are verification steps against code not fully read at plan-writing time.

**Type consistency:** `populate_from_namespace(namespace) -> int` (Task 2) is used by `adopt()` (Task 4) and `test_bootstrap_adopt`. `NSLS2TiledAuthProvider._tiled_login(username) -> bool` / `authenticate(...) -> Session|None` consistent across Task 3 + tests. `ProfileSessionBootstrapper(backend)` with `run_profile`/`adopt`/`bootstrap` consistent between Task 4, Task 5's trigger, and tests. `CMSSessionTrigger(backend)` with `arm`/`_get_shell`/`_on_state_changed` consistent between Task 5 and its test. `get_engine`, `ConsoleREProxy`, `TiledService.adopt_client`, `BlueskyEngine.adopt` match the Plan A signatures.
