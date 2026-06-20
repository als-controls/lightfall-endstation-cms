"""Area detector device classes for CMS (11-BM) endstation.

Extracted from: profile-collection/startup/20-area-detectors.py

Post-construction configuration (read_attrs, kind, ensure_blocking/nonblocking)
is NOT performed here; callers are responsible for applying those settings after
instantiation via happi or direct construction.

``assets_path`` must be set to a callable before staging any detector that
writes data (TIFF or HDF5).  In the Lightfall/bluesky session it is supplied
by 00-startup.py.  Outside that session, set it explicitly::

    from lightfall_endstation_cms.devices import area_detectors as _ad
    _ad.assets_path = lambda: "/nsls2/data/cms/proposals/2024-1/PAS-000001/assets/"
"""

from __future__ import annotations

from ophyd import (
    Component as Cpt,
    EpicsSignal,
    ImagePlugin,
    PilatusDetector,
    PilatusDetectorCam,
    ProcessPlugin,
    ProsilicaDetector,
    ProsilicaDetectorCam,
    ROIPlugin,
    Signal,
    SingleTrigger,
    StatsPlugin,
    TransformPlugin,
)
from ophyd.areadetector.base import ADComponent, EpicsSignalWithRBV
from ophyd.areadetector.filestore_mixins import (
    FileStoreHDF5IterativeWrite,
    FileStoreTIFFIterativeWrite,
)
from ophyd.areadetector.plugins import HDF5Plugin, TIFFPlugin
from nslsii.ad33 import SingleTriggerV33, StatsPluginV33

# ---------------------------------------------------------------------------
# Module-level assets_path hook
# Must be set to a callable before staging any detector.
# ---------------------------------------------------------------------------
assets_path = None  # type: ignore[assignment]


def _get_assets_path() -> str:
    """Return the current assets path string, raising clearly if unset."""
    if callable(assets_path):
        return assets_path()
    raise RuntimeError(
        "area_detectors.assets_path is not set.  "
        "Assign a callable before staging: "
        "from lightfall_endstation_cms.devices import area_detectors as _ad; "
        "_ad.assets_path = my_func"
    )


# ---------------------------------------------------------------------------
# File-store plugin mixins
# ---------------------------------------------------------------------------

class TIFFPluginWithFileStore(TIFFPlugin, FileStoreTIFFIterativeWrite):
    """TIFF plugin that integrates with the Bluesky filestore."""

    def describe(self):
        ret = super().describe()
        key = self.parent._image_name
        color_mode = self.parent.cam.color_mode.get(as_string=True)
        if color_mode == "Mono":
            ret[key]["shape"] = [
                self.parent.cam.num_images.get(),
                self.array_size.height.get(),
                self.array_size.width.get(),
            ]
        elif color_mode in ("RGB1", "Bayer"):
            ret[key]["shape"] = [self.parent.cam.num_images.get(), *self.array_size.get()]
        else:
            raise RuntimeError(f"Color mode not supported: {color_mode!r}")

        cam_dtype = self.parent.cam.data_type.get(as_string=True)
        type_map = {
            "UInt8": "|u1",
            "UInt16": "<u2",
            "Float32": "<f4",
            "Float64": "<f8",
            "Int8": "|i1",
        }
        if cam_dtype in type_map:
            ret[key].setdefault("dtype_str", type_map[cam_dtype])
        return ret


class HDF5PluginWithFileStore(HDF5Plugin, FileStoreHDF5IterativeWrite):
    """HDF5 plugin that integrates with the Bluesky filestore."""
    pass


# ---------------------------------------------------------------------------
# Prosilica camera custom cam class
# ---------------------------------------------------------------------------

class ProsilicaDetectorCamV33(ProsilicaDetectorCam):
    """Prosilica cam updated for AD 3.3 (adds WaitForPlugins)."""

    wait_for_plugins = Cpt(EpicsSignal, "WaitForPlugins", string=True, kind="config")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stage_sigs["wait_for_plugins"] = "Yes"

    def ensure_nonblocking(self):
        self.stage_sigs["wait_for_plugins"] = "Yes"
        for c in self.parent.component_names:
            cpt = getattr(self.parent, c)
            if cpt is self:
                continue
            if hasattr(cpt, "ensure_nonblocking"):
                cpt.ensure_nonblocking()

    def ensure_blocking(self):
        self.stage_sigs["wait_for_plugins"] = "Yes"
        for c in self.parent.component_names:
            cpt = getattr(self.parent, c)
            if cpt is self:
                continue
            if hasattr(cpt, "ensure_blocking"):
                try:
                    cpt.ensure_blocking()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Prosilica detector classes
# ---------------------------------------------------------------------------

class StandardProsilica(SingleTrigger, ProsilicaDetector):
    """Prosilica camera (AD 3.2 style)."""

    tiff = Cpt(TIFFPluginWithFileStore, suffix="TIFF1:", write_path_template="")
    image = Cpt(ImagePlugin, "image1:")
    stats1 = Cpt(StatsPluginV33, "Stats1:")
    stats2 = Cpt(StatsPluginV33, "Stats2:")
    stats3 = Cpt(StatsPluginV33, "Stats3:")
    stats4 = Cpt(StatsPluginV33, "Stats4:")
    stats5 = Cpt(StatsPluginV33, "Stats5:")
    trans1 = Cpt(TransformPlugin, "Trans1:")
    roi1 = Cpt(ROIPlugin, "ROI1:")
    roi2 = Cpt(ROIPlugin, "ROI2:")
    roi3 = Cpt(ROIPlugin, "ROI3:")
    roi4 = Cpt(ROIPlugin, "ROI4:")
    proc1 = Cpt(ProcessPlugin, "Proc1:")

    def stage(self, *args, **kwargs):
        base = _get_assets_path()
        self.tiff.write_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.tiff.read_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.tiff.reg_root = base + self.name
        return super().stage(*args, **kwargs)


class StandardProsilicaV33(SingleTriggerV33, ProsilicaDetector):
    """Prosilica camera (AD 3.3 style, with WaitForPlugins support)."""

    cam = Cpt(ProsilicaDetectorCamV33, "cam1:")
    tiff = Cpt(TIFFPluginWithFileStore, suffix="TIFF1:", write_path_template="")
    image = Cpt(ImagePlugin, "image1:")
    stats1 = Cpt(StatsPluginV33, "Stats1:")
    stats2 = Cpt(StatsPluginV33, "Stats2:")
    stats3 = Cpt(StatsPluginV33, "Stats3:")
    stats4 = Cpt(StatsPluginV33, "Stats4:")
    stats5 = Cpt(StatsPluginV33, "Stats5:")
    trans1 = Cpt(TransformPlugin, "Trans1:")
    roi1 = Cpt(ROIPlugin, "ROI1:")
    roi2 = Cpt(ROIPlugin, "ROI2:")
    roi3 = Cpt(ROIPlugin, "ROI3:")
    roi4 = Cpt(ROIPlugin, "ROI4:")
    proc1 = Cpt(ProcessPlugin, "Proc1:")

    @property
    def hints(self):
        return {"fields": [self.stats1.total.name]}

    def stage(self, *args, **kwargs):
        base = _get_assets_path()
        self.tiff.write_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.tiff.read_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.tiff.reg_root = base + self.name
        return super().stage(*args, **kwargs)

    def setExposureTime(self, exposure_time, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.acquire_time, self.cam.acquire_time.get())  # noop

    def setExposurePeriod(self, exposure_period, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.acquire_period, self.cam.acquire_period.get())  # noop

    def setExposureNumber(self, exposure_number, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.num_images, self.cam.num_images.get())  # noop


# ---------------------------------------------------------------------------
# Pilatus custom cam class
# ---------------------------------------------------------------------------

class PilatusDetectorCamV33(PilatusDetectorCam):
    """Pilatus cam updated for AD 3.3 (adds WaitForPlugins)."""

    wait_for_plugins = Cpt(EpicsSignal, "WaitForPlugins", string=True, kind="config")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stage_sigs["wait_for_plugins"] = "Yes"

    def ensure_nonblocking(self):
        self.stage_sigs["wait_for_plugins"] = "Yes"
        for c in self.parent.component_names:
            cpt = getattr(self.parent, c)
            if cpt is self:
                continue
            if hasattr(cpt, "ensure_nonblocking"):
                cpt.ensure_nonblocking()


# ---------------------------------------------------------------------------
# Pilatus detector classes – TIFF variants
# ---------------------------------------------------------------------------

class PilatusV33(SingleTriggerV33, PilatusDetector):
    """Pilatus 300k (AD 3.3, TIFF file store)."""

    cam = Cpt(PilatusDetectorCamV33, "cam1:")
    image = Cpt(ImagePlugin, "image1:")
    stats1 = Cpt(StatsPluginV33, "Stats1:")
    stats2 = Cpt(StatsPluginV33, "Stats2:")
    stats3 = Cpt(StatsPluginV33, "Stats3:")
    stats4 = Cpt(StatsPluginV33, "Stats4:")
    stats5 = Cpt(StatsPluginV33, "Stats5:")
    roi1 = Cpt(ROIPlugin, "ROI1:")
    roi2 = Cpt(ROIPlugin, "ROI2:")
    roi3 = Cpt(ROIPlugin, "ROI3:")
    roi4 = Cpt(ROIPlugin, "ROI4:")
    proc1 = Cpt(ProcessPlugin, "Proc1:")
    tiff = Cpt(
        TIFFPluginWithFileStore,
        suffix="TIFF1:",
        write_path_template="/nsls2/xf11bm/Pilatus300/%Y/%m/%d/",
        root="/nsls2/xf11bm",
    )

    def setExposureTime(self, exposure_time, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.acquire_time, exposure_time)

    def setExposurePeriod(self, exposure_period, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.acquire_period, exposure_period)

    def setExposureNumber(self, exposure_number, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.num_images, exposure_number)


class Pilatus800V33(SingleTriggerV33, PilatusDetector):
    """Pilatus 800k (AD 3.3, TIFF file store, assets_path-based paths)."""

    cam = Cpt(PilatusDetectorCamV33, "cam1:")
    image = Cpt(ImagePlugin, "image1:")
    stats1 = Cpt(StatsPluginV33, "Stats1:")
    stats2 = Cpt(StatsPluginV33, "Stats2:")
    stats3 = Cpt(StatsPluginV33, "Stats3:")
    stats4 = Cpt(StatsPluginV33, "Stats4:")
    stats5 = Cpt(StatsPluginV33, "Stats5:")
    roi1 = Cpt(ROIPlugin, "ROI1:")
    roi2 = Cpt(ROIPlugin, "ROI2:")
    roi3 = Cpt(ROIPlugin, "ROI3:")
    roi4 = Cpt(ROIPlugin, "ROI4:")
    proc1 = Cpt(ProcessPlugin, "Proc1:")
    tiff = Cpt(TIFFPluginWithFileStore, suffix="TIFF1:", write_path_template="")

    def stage(self, *args, **kwargs):
        base = _get_assets_path()
        self.tiff.write_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.tiff.read_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.tiff.reg_root = base + self.name
        return super().stage(*args, **kwargs)

    def setExposureTime(self, exposure_time, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.acquire_time, exposure_time)

    def setExposurePeriod(self, exposure_period, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.acquire_period, exposure_period)

    def setExposureNumber(self, exposure_number, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.num_images, exposure_number)


class Pilatus8002V33(PilatusV33):
    """Pilatus 800k-2 (alternate detector, assets_path-based paths)."""

    cam = Cpt(PilatusDetectorCamV33, "cam1:")
    tiff = Cpt(TIFFPluginWithFileStore, suffix="TIFF1:", write_path_template="")

    def stage(self, *args, **kwargs):
        base = _get_assets_path()
        self.tiff.write_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.tiff.read_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.tiff.reg_root = base + self.name
        return super().stage(*args, **kwargs)


class Pilatus2MV33(SingleTriggerV33, PilatusDetector):
    """Pilatus 2M (AD 3.3, TIFF file store)."""

    cam = Cpt(PilatusDetectorCamV33, "cam1:")
    image = Cpt(ImagePlugin, "image1:")
    stats1 = Cpt(StatsPluginV33, "Stats1:")
    stats2 = Cpt(StatsPluginV33, "Stats2:")
    stats3 = Cpt(StatsPluginV33, "Stats3:")
    stats4 = Cpt(StatsPluginV33, "Stats4:")
    stats5 = Cpt(StatsPluginV33, "Stats5:")
    roi1 = Cpt(ROIPlugin, "ROI1:")
    roi2 = Cpt(ROIPlugin, "ROI2:")
    roi3 = Cpt(ROIPlugin, "ROI3:")
    roi4 = Cpt(ROIPlugin, "ROI4:")
    proc1 = Cpt(ProcessPlugin, "Proc1:")
    trans1 = Cpt(TransformPlugin, "Trans1:")
    tiff = Cpt(TIFFPluginWithFileStore, suffix="TIFF1:", write_path_template="")

    def stage(self, *args, **kwargs):
        base = _get_assets_path()
        self.tiff.write_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.tiff.read_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.tiff.reg_root = base + self.name
        return super().stage(*args, **kwargs)

    def setExposureTime(self, exposure_time, verbosity=3):
        import time
        from bluesky.plan_stubs import mv, sleep as bps_sleep
        yield from mv(self.cam.acquire_time, exposure_time)
        print("Setting exposure time to", exposure_time)
        while self.cam.acquire_time.get() != exposure_time:
            yield from bps_sleep(0.1)
            print("Waiting for exposure time to be set correctly...")

    def setExposurePeriod(self, exposure_period, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.acquire_period, exposure_period)

    def setExposureNumber(self, exposure_number, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.num_images, exposure_number)


# ---------------------------------------------------------------------------
# Pilatus detector classes – HDF5 variants
# ---------------------------------------------------------------------------

class PilatusV33_h5(SingleTriggerV33, PilatusDetector):
    """Pilatus detector (AD 3.3, HDF5 file store) with staging retry loop."""

    cam = Cpt(PilatusDetectorCamV33, "cam1:")
    image = Cpt(ImagePlugin, "image1:")
    stats1 = Cpt(StatsPluginV33, "Stats1:")
    stats2 = Cpt(StatsPluginV33, "Stats2:")
    stats3 = Cpt(StatsPluginV33, "Stats3:")
    stats4 = Cpt(StatsPluginV33, "Stats4:")
    stats5 = Cpt(StatsPluginV33, "Stats5:")
    roi1 = Cpt(ROIPlugin, "ROI1:")
    roi2 = Cpt(ROIPlugin, "ROI2:")
    roi3 = Cpt(ROIPlugin, "ROI3:")
    roi4 = Cpt(ROIPlugin, "ROI4:")
    proc1 = Cpt(ProcessPlugin, "Proc1:")
    trans1 = Cpt(TransformPlugin, "Trans1:")
    h5 = Cpt(HDF5PluginWithFileStore, suffix="HDF1:", write_path_template="")

    def stage(self):
        base = _get_assets_path()
        self.h5.write_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.h5.read_path_template = base + f"{self.name}/%Y/%m/%d/"
        self.h5.reg_root = base + self.name
        # Retry loop: IOC may not answer on first attempt
        error = None
        for _ in range(5):
            try:
                return super().stage()
            except TimeoutError as err:
                error = err
            else:
                break
        raise error  # type: ignore[misc]

    def setExposureTime(self, exposure_time, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.acquire_time, exposure_time)

    def setExposurePeriod(self, exposure_period, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.acquire_period, exposure_period)

    def setExposureNumber(self, exposure_number, verbosity=3):
        from bluesky.plan_stubs import mv
        yield from mv(self.cam.num_images, exposure_number)


class Pilatus800V33_h5(PilatusV33_h5):
    """Pilatus 800k (AD 3.3, HDF5 file store) — inherits retry staging."""
    pass
