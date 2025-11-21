"""
Dataset recipe for MERIT DEM.
"""

from __future__ import annotations

import ee

from gee_tools.download import download_dem

# Default MERIT config
MERIT_CONFIG = ["Image", "MERIT/DEM/v1_0_3", "dem", "MERIT 30m"]


def download_merit_dem(
    region_geom: ee.Geometry,
    out_path: str,
    scale: int = 30,
    scale_factor: float = 1000.0,
) -> None:
    """
    Download MERIT DEM for a given region to a GeoTIFF.

    Parameters
    ----------
    region_geom : ee.Geometry
        Region (e.g. polygon geometry) to clip MERIT DEM to.
    out_path : str
        Output GeoTIFF filepath.
    scale : int, optional
        Pixel size for fallback geemap export.
    scale_factor : float, optional
        Multiply DEM values by this factor before export.
        MERIT DEM often needs 1000 to convert from km to m.
    """
    download_dem(
        MERIT_CONFIG,
        region_geom=region_geom,
        out_path=out_path,
        scale=scale,
        scale_factor=scale_factor,
    )

