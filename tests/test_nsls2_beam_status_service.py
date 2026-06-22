"""Lifecycle tests for NSLS2BeamStatusService (caproto Context monkeypatched)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.services import nsls2_beam_status as svc
from lightfall_endstation_cms.services.nsls2_beam_status import NSLS2BeamStatusService


class _FakeSub:
    def __init__(self, pv):
        self.pv = pv
        self.callbacks = []

    def add_callback(self, cb):
        self.callbacks.append(cb)


class _FakePV:
    def __init__(self, name):
        self.name = name

    def subscribe(self, data_type=None):
        self.data_type = data_type
        return _FakeSub(self)


class _FakeContext:
    instances = []

    def __init__(self, *a, **k):
        self.connection_state_callback = None
        self.disconnected = False
        _FakeContext.instances.append(self)

    def get_pvs(self, *names, connection_state_callback=None):
        self.connection_state_callback = connection_state_callback
        return [_FakePV(n) for n in names]

    def disconnect(self):
        self.disconnected = True


@pytest.fixture(autouse=True)
def _patch_context(monkeypatch):
    _FakeContext.instances = []
    monkeypatch.setattr("caproto.threading.client.Context", _FakeContext)
    yield
    NSLS2BeamStatusService.reset()


def test_singleton_identity_and_reset():
    a = NSLS2BeamStatusService.get_instance()
    b = NSLS2BeamStatusService.get_instance()
    assert a is b
    NSLS2BeamStatusService.reset()
    c = NSLS2BeamStatusService.get_instance()
    assert c is not a


def test_start_is_idempotent_and_subscribes_all_pvs():
    s = NSLS2BeamStatusService.get_instance()
    s.start()
    s.start()  # second call must be a no-op
    assert s.is_running is True
    assert len(_FakeContext.instances) == 1
    # one PV per ALL_PVS, each with a monitor callback registered
    assert len(s._subs) == len(svc.ALL_PVS)


def test_string_pvs_subscribe_as_string():
    from caproto import ChannelType

    s = NSLS2BeamStatusService.get_instance()
    s.start()
    by_name = {sub.pv.name: sub.pv for sub in s._subs}
    # Must be the caproto ChannelType enum, NOT the literal "string": caproto's
    # _fill_defaults rejects an arbitrary str (only None / ChannelType / a DBR
    # category like "native" are valid), which crashed the subscription thread.
    assert by_name[svc.SR_MODE_PV].data_type == ChannelType.STRING
    assert not isinstance(by_name[svc.SR_MODE_PV].data_type, str)
    assert by_name[svc.SR_CURRENT_PV].data_type is None


def test_on_value_updates_data_and_emits_status():
    s = NSLS2BeamStatusService.get_instance()
    received = []
    s.status_changed.connect(received.append)
    s._on_value(svc.SR_CURRENT_PV, 401.0)
    assert s.current_data is not None
    assert s.current_data.beam_current == 401.0
    assert received and received[-1].beam_current == 401.0


def test_connection_transitions_emit_once():
    s = NSLS2BeamStatusService.get_instance()
    seen = []
    s.connection_changed.connect(seen.append)
    pv = _FakePV(svc.SR_CURRENT_PV)
    s._on_connection(pv, "connected")
    s._on_connection(_FakePV(svc.SR_LIFETIME_PV), "connected")  # still connected, no new emit
    assert s.is_connected is True
    assert seen == [True]
    s._on_connection(pv, "disconnected")
    s._on_connection(_FakePV(svc.SR_LIFETIME_PV), "disconnected")
    assert s.is_connected is False
    assert seen == [True, False]


def test_stop_disconnects_context():
    s = NSLS2BeamStatusService.get_instance()
    s.start()
    ctx = _FakeContext.instances[0]
    s.stop()
    assert s.is_running is False
    assert ctx.disconnected is True


def test_introspection_reports_values():
    s = NSLS2BeamStatusService.get_instance()
    s._on_value(svc.SR_CURRENT_PV, 401.0)
    s._on_value(svc.SR_SHUTTER_PV, "Open")
    data = s.get_introspection_data()
    assert data["beam_current_mA"] == 401.0
    assert data["beam_available"] is True
    assert data["is_running"] == s.is_running


class _Resp:
    def __init__(self, data):
        self.data = data


def test_decode_handles_response_shapes():
    # array-wrapped float
    result = NSLS2BeamStatusService._decode("pv", _Resp([401.0]))
    assert result == 401.0

    # array-wrapped bytes → decoded str
    result = NSLS2BeamStatusService._decode("pv", _Resp([b"Open"]))
    assert isinstance(result, str)
    assert result == "Open"

    # array-wrapped plain str
    result = NSLS2BeamStatusService._decode("pv", _Resp(["Operations"]))
    assert result == "Operations"

    # bare scalar (int): data[0] raises TypeError → fallback to data itself
    result = NSLS2BeamStatusService._decode("pv", _Resp(5))
    assert result == 5


def test_long_string_message_pvs_subscribe_as_char():
    from caproto import ChannelType

    s = NSLS2BeamStatusService.get_instance()
    s.start()
    by_name = {sub.pv.name: sub.pv for sub in s._subs}
    # OP{n}Message.VAL$ are long-string ".VAL$" fields: a DBR_STRING request
    # truncates at 40 chars, so they must be read as a native CHAR waveform.
    assert by_name[svc.OPS_MSG1_PV].data_type == ChannelType.CHAR
    assert by_name[svc.OPS_MSG2_PV].data_type == ChannelType.CHAR
    # the short enum/status PVs stay DBR_STRING
    assert by_name[svc.SR_MODE_PV].data_type == ChannelType.STRING


def test_decode_long_string_char_array_assembles_full_message():
    # A CHAR waveform: byte values, NUL-terminated, longer than the 40-char
    # DBR_STRING cap. The full message must be assembled (cut at the NUL).
    msg = "Beam dump: RF trip on cavity 3, recovery in progress, ETA ~30 min"
    assert len(msg) > 40  # would be truncated under DBR_STRING
    raw = list(msg.encode()) + [0, 0, 7]  # NUL terminator + trailing junk
    result = NSLS2BeamStatusService._decode(svc.OPS_MSG1_PV, _Resp(raw))
    assert result == msg


def test_decode_long_string_accepts_bytes_and_empty():
    assert (
        NSLS2BeamStatusService._decode(svc.OPS_MSG2_PV, _Resp(b"hello\x00xx"))
        == "hello"
    )
    assert NSLS2BeamStatusService._decode(svc.OPS_MSG1_PV, _Resp([0, 0, 0])) == ""
