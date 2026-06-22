"""Access the live IPython-kernel namespace where the CMS SAM framework lives.

The CMS beamline/sample framework (``cms``/``beam``/``stg`` and the user's
``Sample``/``Holder`` instances) is hosted in Lightfall's in-process IPython
kernel by the :class:`~lightfall_endstation_cms.bootstrap.ProfileSessionBootstrapper`
after login. The CMS PanelPlugins are a thin GUI veneer over those *same* live
objects, so the GUI and the console stay in sync — they never hold their own
copies.

This module is the single, reusable access pattern the panels use:

* :func:`get_kernel_shell` / :func:`get_kernel_object` — read live objects.
* :func:`find_kernel_objects` — discover objects by ophyd/SAM base-class name
  (so a panel finds samples/stages without hard-coding the variable names the
  beamtime happens to use).
* :func:`execute_in_console` — run a command in the kernel exactly as if the
  operator typed it (output appears in the console; history is recorded).

All accessors degrade to ``None``/empty/``False`` when the kernel or SAM is not
up yet (off-beamline, or pre-login), so panels can render a "not connected"
state instead of raising.
"""

from __future__ import annotations

from typing import Any, Iterable

from loguru import logger

_IPYTHON_PANEL_ID = "lightfall.panels.ipython"


def get_ipython_panel() -> Any | None:
    """Return the live IPythonPanel, or None if the app/window/panel is absent."""
    try:
        from lightfall.core import LFApplication
    except Exception:
        return None
    app = LFApplication.get_instance()
    window = app.main_window if app else None
    if window is None:
        return None
    try:
        return window.get_panel(_IPYTHON_PANEL_ID)
    except Exception:
        return None


def get_kernel_shell() -> Any | None:
    """Return the live IPython InteractiveShell, or None if no kernel."""
    panel = get_ipython_panel()
    if panel is None:
        return None
    try:
        return panel.shell
    except Exception:
        return None


def get_kernel_namespace() -> dict[str, Any]:
    """Return the kernel user namespace dict (empty if no kernel)."""
    shell = get_kernel_shell()
    ns = getattr(shell, "user_ns", None)
    return ns if isinstance(ns, dict) else {}


def get_kernel_object(name: str) -> Any | None:
    """Return ``user_ns[name]`` from the live kernel, or None."""
    return get_kernel_namespace().get(name)


def _mro_names(obj: Any) -> set[str]:
    try:
        return {c.__name__ for c in type(obj).__mro__}
    except Exception:
        return set()


def find_kernel_objects(*base_class_names: str) -> dict[str, Any]:
    """Find live kernel objects whose class MRO includes any of *base_class_names*.

    Returns a mapping of variable name -> object. Used to discover, e.g., all
    ``Sample`` or ``Stage`` instances regardless of what they are named, without
    importing the (kernel-resident, beamtime-specific) classes.
    """
    wanted = set(base_class_names)
    found: dict[str, Any] = {}
    for name, obj in get_kernel_namespace().items():
        if name.startswith("_"):
            continue
        if _mro_names(obj) & wanted:
            found[name] = obj
    return found


def sam_is_loaded() -> bool:
    """True if the SAM framework has been hosted in the kernel (``cms`` present)."""
    return get_kernel_object("cms") is not None


def _device_catalog() -> Any | None:
    """The DeviceCatalog singleton, or None if unavailable (e.g. tests, headless)."""
    try:
        from lightfall.devices import DeviceCatalog

        return DeviceCatalog.get_instance()
    except Exception:
        logger.debug("DeviceCatalog unavailable in kernel_access")
        return None


def devices_by_name(names: Iterable[str]) -> dict[str, object]:
    """Return a mapping of happi item name -> live ophyd device object.

    Only names whose catalog entry exists AND has a non-None ``._ophyd_device``
    are included.  Names that are absent from the catalog, or whose device has
    not finished instantiating yet, are silently omitted (logged at DEBUG).

    Args:
        names: Iterable of happi item / profile variable names to look up.

    Returns:
        ``{name: ophyd_device}`` for every name that is currently live.
    """
    catalog = _device_catalog()
    result: dict[str, object] = {}
    if catalog is None:
        logger.debug("devices_by_name: catalog unavailable, returning empty dict")
        return result
    for name in names:
        info = catalog.get_device_by_name(name)
        if info is None:
            logger.debug("devices_by_name: '{}' not found in catalog", name)
            continue
        obj = getattr(info, "_ophyd_device", None)
        if obj is None:
            logger.debug("devices_by_name: '{}' not yet live (._ophyd_device is None)", name)
            continue
        result[name] = obj
    return result


def execute_in_console(code: str) -> bool:
    """Run *code* in the live kernel as if typed in the console.

    Output appears in the console widget and the call is recorded in history,
    so a panel button is indistinguishable from the operator typing the command.

    ``run_cell`` itself runs on the GUI thread, but a SAM action that drives the
    RunEngine stays responsive: the bootstrap rebinds ``RE`` to a
    ``ConsoleREProxy``, which submits the plan to the engine's worker thread and
    spins a nested Qt event loop while it runs (so the GUI keeps painting and
    Abort still works). Only non-RunEngine busy work inside a SAM method would
    run uninterrupted on the GUI thread. Returns False if no kernel is available.
    """
    shell = get_kernel_shell()
    if shell is None:
        logger.warning("execute_in_console: no live IPython kernel; cannot run {!r}", code)
        return False
    try:
        shell.run_cell(code, store_history=True)
        return True
    except Exception:
        logger.exception("execute_in_console: error running {!r}", code)
        return False
