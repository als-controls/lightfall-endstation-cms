# lucid-endstation-cms

Device catalog and LUCID plugins for the NSLS-II CMS (Complex Materials Scattering)
beamline, 11-BM.

## Overview

This package provides:
- A happi JSON device catalog (`static.json`) for all CMS beamline hardware
- Custom ophyd device classes for CMS-specific equipment
- LUCID controller plugins for CMS endstation devices

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

## Source

Extracted from: https://github.com/NSLS2/cms-profile-collection/tree/data-security
