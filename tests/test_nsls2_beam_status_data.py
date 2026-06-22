"""Tests for NSLS-II beam status PV mapping (pure, no Qt / no IOC)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lightfall_endstation_cms.services import nsls2_beam_status as svc
from lightfall_endstation_cms.services.nsls2_beam_status import (
    NSLS2BeamData,
    apply_pv_value,
    shutter_means_available,
)


def test_all_pvs_tuple_has_eight_unique_entries():
    assert len(svc.ALL_PVS) == 8
    assert len(set(svc.ALL_PVS)) == 8


def test_string_pvs_are_the_short_enum_pvs():
    assert svc.STRING_PVS == frozenset(
        {svc.SR_MODE_PV, svc.SR_SHUTTER_PV, svc.TOPOFF_PV}
    )


def test_message_pvs_are_long_strings_not_dbr_string():
    # The ".VAL$" message PVs read as CHAR waveforms, not DBR_STRING (which
    # would truncate them at 40 chars).
    assert svc.LONG_STRING_PVS == frozenset({svc.OPS_MSG1_PV, svc.OPS_MSG2_PV})
    assert svc.OPS_MSG1_PV not in svc.STRING_PVS
    assert svc.OPS_MSG2_PV not in svc.STRING_PVS


def test_apply_numeric_pvs():
    data = NSLS2BeamData()
    apply_pv_value(data, svc.SR_CURRENT_PV, 401.27)
    apply_pv_value(data, svc.SR_LIFETIME_PV, 12.5)
    assert data.beam_current == 401.27
    assert data.lifetime == 12.5


def test_apply_string_pvs():
    data = NSLS2BeamData()
    apply_pv_value(data, svc.SR_MODE_PV, "Operations")
    apply_pv_value(data, svc.TOPOFF_PV, "On")
    apply_pv_value(data, svc.NEXT_INJ_PV, "120")
    apply_pv_value(data, svc.OPS_MSG1_PV, "Beam delivered to all beamlines")
    apply_pv_value(data, svc.OPS_MSG2_PV, "Next fill 14:00")
    assert data.mode == "Operations"
    assert data.topoff_state == "On"
    assert data.next_injection == "120"
    assert data.ops_message == "Beam delivered to all beamlines\nNext fill 14:00"


def test_shutter_pv_sets_availability():
    data = NSLS2BeamData()
    apply_pv_value(data, svc.SR_SHUTTER_PV, "Open")
    assert data.shutter_status == "Open"
    assert data.beam_available is True

    apply_pv_value(data, svc.SR_SHUTTER_PV, "Closed")
    assert data.shutter_status == "Closed"
    assert data.beam_available is False


def test_shutter_means_available_is_case_insensitive():
    assert shutter_means_available("OPEN") is True
    assert shutter_means_available("open") is True
    assert shutter_means_available("Closed") is False
    assert shutter_means_available("") is False
    assert shutter_means_available(0) is False


def test_ops_message_drops_blank_lines():
    data = NSLS2BeamData(ops_message_1="hello", ops_message_2="")
    assert data.ops_message == "hello"


def test_unknown_pv_is_ignored():
    data = NSLS2BeamData()
    apply_pv_value(data, "Some:Other-PV", 5.0)
    assert data == NSLS2BeamData()
