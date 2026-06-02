# lightfall-endstation-cms

Device catalog and Lightfall plugins for the NSLS-II CMS (Complex Materials Scattering)
beamline, 11-BM.

## Overview

This package provides:
- A happi JSON device catalog (`static.json`) for all CMS beamline hardware
- Custom ophyd device classes for CMS-specific equipment
- Lightfall controller plugins for CMS endstation devices

## Device Catalog

The `static.json` file is a happi-format device catalog extracted from the
[cms-profile-collection](https://github.com/NSLS2/cms-profile-collection) IPython
startup scripts (`data-security` branch).

### Catalog Summary (109 devices)

| Group | Count | Key devices |
|---|---|---|
| Optics | 24 | Mono (bragg/pitch/roll/perp), toroidal mirror (6 axes), slits s0-s5, 8 filters |
| Sample | 21 | smx/smy/sth/schi, arm (x/y/z/phi/r), strans/stilt/srot, Linkam thermal+tensile |
| Diagnostics | 30 | 2x quad electrometers (8ch), 4 ion chambers, BIMs, diode box, 8 Prosilica cameras |
| Detectors | 16 | Pilatus 800K + 2M (active), 300K + 800K#2 (inactive), Xspress3, SAXS/WAXS/MAXS stage motors, Ocean Optics |
| Infrastructure | 13 | 8 PDU switches, chiller, Sorrenson power supply |
| Shutters | 4 | Photon shutter (open/close/status), experiment shutter trigger |

### Not yet cataloged

- ioLogik analog/digital I/O loop devices (AO, AI, Relay, DI, RTD, TC -- ~44 devices)
- PhotoThermalAnnealer (uses raw caput/caget, not ophyd)
- Serial devices (Agilent, Keithley, syringe pump -- all commented out in profile)
- S4Dev/ICDev/BPMDev (hardcoded absolute PVs in class, no prefix)

## Profile Collection Inventory

Everything below lives in the
[cms-profile-collection](https://github.com/NSLS2/cms-profile-collection/tree/data-security)
IPython startup scripts and will need to be transitioned into this repo as either
device classes, plugins, or configuration.

### Device Classes

Custom ophyd `Device` subclasses that need to be ported (or wrapped). These are
referenced by the happi catalog's `device_class` field.

| Module | Classes | Source file(s) |
|---|---|---|
| `slits` | `MotorCenterAndGap`, `Blades`, `Filter`, `Configurable` mixin | `10-motors.py` |
| `detectors` | `StandardProsilicaV33`, `PilatusV33`, `Pilatus800V33`, `Pilatus8002V33`, `Pilatus2MV33`, `OPLSXspress3Detector` | `20-area-detectors.py`, `27-Xspress3.py` |
| `scalers` | `IonChamber`, `ScaleSignal`, `EpicsSignalROWait`, `EpicsSignalROIntegrate` | `25-scalers.py`, `26-IonChamber.py` |
| `diodebox` | `DiodeBox` | `42-diodebox.py` |
| `iologik` | `ioLogik`, `MassFlowControl`, `SorrensonPowerSupply`, `Chiller`, `PowerDUnit`, `Potentiostats`, `S4Dev`, `ICDev`, `BPMDev` | `43-endstation-ioLogik.py` |
| `linkam` | `LinkamThermal`, `LinkamTensile` | `51-linkam-stages.py` |
| `oceanoptics` | `OceanOpticsSpectrometer` | `52-oceanoptics.py` |
| `serial` | `Agilent_34970A`, `Keithley_2000`, `Minichiller`, `SyringePump` (all currently commented out) | `41-endstation-serial-dev.py` |
| (non-ophyd) | `PhotoThermalAnnealer` (raw caput/caget), `Beamstop` (plain class) | `44-laserPTA.py`, `82-beamstop.py` |

### Sample System (~9,000 lines)

The `sam` / `hol` / `stg` framework for sample tracking, alignment, and measurement.

| Category | Classes | Source |
|---|---|---|
| Coordinate framework | `CoordinateSystem`, `Axis`, `Stage`, `SampleStage` | `94-sample.py` |
| Generic sample | `Sample_Generic` (measure, snap, align, named positions, file naming) | `94-sample.py` |
| Holders | `Holder`, `PositionalHolder` | `94-sample.py` |
| TSAXS samples | `SampleTSAXS_Generic` | `95-sample-custom.py` |
| GISAXS samples | `SampleGISAXS_Generic` (theta alignment, reflectivity) | `95-sample-custom.py` |
| CD-SAXS samples | `SampleCDSAXS_Generic` (phi rotation) | `95-sample-custom.py` |
| XR samples | `SampleXR_WAXS` | `95-sample-custom.py` |
| Goniometer | `SampleGonio_Generic`, `SampleSecondStage` | `95-sample-custom.py` |
| Holder types | `GIBar`, `CapillaryHolder`, `WellPlateHolder`, `DSCStage`, `HumidityStage`, `InstecStage60`, `PaloniThermalStage`, thermal/Linkam variants | `95-sample-custom.py` |
| User-facing | `Sample`, `SampleTSAXS`, `SampleGISAXS`, `SampleCDSAXS` (thin wrappers with naming schemes) | `97-user.py` |

### Beamline Model (~4,300 lines)

The `cms` object that models the entire beamline as a Python object hierarchy.

| Category | Classes | Source |
|---|---|---|
| Detectors | `BeamlineDetector`, `CMS_SAXS_Detector`, `CMS_WAXS_Detector` (geometry, calibration) | `81-beam.py` |
| Components | `BeamlineElement`, `Shutter`, `GateValve`, `ThreePoleWiggler` | `81-beam.py` |
| Monitors | `Monitor`, `DiagnosticScreen`, `IonChamber_CMS`, `Scintillator_CMS`, `DiamondDiode_CMS` | `81-beam.py` |
| Beam | `CMSBeam` (energy, wavelength, slits, attenuators, transmission) | `81-beam.py` |
| Beamline | `CMS_Beamline` (vacuum, pump/vent, modes, ROI, exposure management) | `81-beam.py` |
| Mode variants | `CMS_Beamline_GISAXS`, `CMS_Beamline_XR` | `81-beam.py` |

### Automation (~2,000 lines)

| Item | Description | Source |
|---|---|---|
| `SampleExchangeRobot` | Automated sample exchange with tray positions, safety interlocks | `96-automation.py` |
| `Queue` | Measurement queue with priority and scheduling | `96-automation.py` |
| Slack integration | `post_to_slack()`, `status_to_slack()` via webhook | `96-automation.py` |

### Scanning & Fitting (~2,900 lines)

| Item | Description | Source |
|---|---|---|
| `detselect()` | Select active detector(s) for scans | `90-bluesky.py` |
| `config_update/load` | Save/restore beamline config to JSON | `90-bluesky.py` |
| Pneumatic/valve/pump control | Utility functions for vacuum system | `90-bluesky.py` |
| `fit_scan()` / `fit_edge()` | Scan with live Gaussian/Lorentzian fitting | `91-fit_scan.py` |
| `flyscan()` / `fly_scan()` | Fly-scanning support | `91-fit_scan.py` |
| `align_motor()` / `ps()` | Automated motor alignment routines | `91-fit_scan.py` |
| Custom live callbacks | `LiveTable_Custom`, `LiveStatPlot`, `LiveFitPlot_Custom` | `91-fit_scan.py` |

### Infrastructure & Config

| Item | Description | Source |
|---|---|---|
| Data pipeline | Tiled writing client, Kafka publishing, Redis config | `00-startup.py` |
| Optics utilities | Mirror/mono alignment helpers | `15-optics-utilities.py` |
| Archiver | EPICS Archiver Appliance integration | `55-archiver.py` |
| Supplemental data | Baseline device streams for run metadata | `93-supplemental-data.py` |
| IPython magics | `%magics` for common operations | `92-magics.py` |
| Caproto test | Caproto test code | `99-caproto-test.py` |

## Source

Extracted from: https://github.com/NSLS2/cms-profile-collection/tree/data-security
