"""Tests for devices_by_name catalog accessor in kernel_access."""

from lightfall_endstation_cms import kernel_access


class _FakeDeviceInfo:
    """Minimal DeviceInfo stand-in with a ._ophyd_device attribute."""

    def __init__(self, ophyd_device):
        self._ophyd_device = ophyd_device


class _FakeCatalog:
    def __init__(self, mapping):
        # mapping: name -> live obj or None
        self._m = {
            name: _FakeDeviceInfo(obj) for name, obj in mapping.items()
        }

    def get_device_by_name(self, name):
        return self._m.get(name)


def test_devices_by_name_returns_only_live_present(monkeypatch):
    sentinel = object()
    fake = _FakeCatalog({"smx": sentinel, "pilatus2M": None})
    monkeypatch.setattr(kernel_access, "_device_catalog", lambda: fake)
    result = kernel_access.devices_by_name(["smx", "pilatus2M", "absent"])
    assert result == {"smx": sentinel}


def test_devices_by_name_empty_names(monkeypatch):
    fake = _FakeCatalog({"smx": object()})
    monkeypatch.setattr(kernel_access, "_device_catalog", lambda: fake)
    result = kernel_access.devices_by_name([])
    assert result == {}


def test_devices_by_name_no_catalog(monkeypatch):
    monkeypatch.setattr(kernel_access, "_device_catalog", lambda: None)
    result = kernel_access.devices_by_name(["smx", "pilatus2M"])
    assert result == {}


def test_devices_by_name_all_live(monkeypatch):
    s1, s2 = object(), object()
    fake = _FakeCatalog({"smx": s1, "pilatus2M": s2})
    monkeypatch.setattr(kernel_access, "_device_catalog", lambda: fake)
    result = kernel_access.devices_by_name(["smx", "pilatus2M"])
    assert result == {"smx": s1, "pilatus2M": s2}
