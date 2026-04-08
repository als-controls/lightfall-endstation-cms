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

### Device Categories

| Category | Count | Examples |
|----------|-------|---------|
| Motors | ~50 | Monochromator, mirrors, slits, sample stages, detector stages |
| Area Detectors | ~14 | Pilatus 300K/800K/2M, Prosilica cameras |
| Scalers/Electrometers | ~20 | Quad electrometers, ion chambers |
| Fluorescence | 1 | Xspress3 |
| Serial Devices | ~5 | Linkam stages, diode box |
| I/O | ~40+ | ioLogik analog/digital, relays, RTDs, thermocouples |
| Shutters | ~7 | Experiment shutter, photon shutter |
| Misc | ~5 | Power supplies, chillers, PDUs |

## Source

Extracted from: https://github.com/NSLS2/cms-profile-collection/tree/data-security
