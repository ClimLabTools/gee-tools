"""
MODIS MOD16A2GF Evapotranspiration downloader.

Exports yearly ET stacks (8-day, 500m) as multi-band GeoTIFFs.
Each band is named by its date (YYYY_MM_dd).
"""

from __future__ import annotations
import ee

from gee_tools.download import download_image


def download_modis_et(
    polygon_fc: ee.FeatureCollection,
    start_year: int = 2000,
    end_year: int = 2024,
    scale: int = 500,
    crs: str = "EPSG:4326",
    prefix: str = "MOD16A2GF_ET_",
):
    """
    Download MODIS ET yearly stacks clipped to the given polygon(s).

    Parameters
    ----------
    polygon_fc : ee.FeatureCollection
        Feature collection defining the region of interest.
    start_year, end_year : int
        Year range to export.
    scale : int
        Export pixel size (MODIS native: ~500m).
    crs : str
        Output CRS, default EPSG:4326.
    prefix : str
        Base filename prefix.
    """
    region = polygon_fc.geometry().getInfo()
    modis = ee.ImageCollection("MODIS/061/MOD16A2GF")

    def rename_et(img):
        date_str = ee.Date(img.get("system:time_start")).format("YYYY_MM_dd")
        return img.select("ET").rename(date_str)

    for year in range(start_year, end_year + 1):
        print(f"Processing MODIS ET for year {year}...")
        year_collection = (
            modis.filterDate(f"{year}-01-01", f"{year}-12-31")
                 .map(rename_et)
                 .map(lambda img: img.clip(polygon_fc))
        )
        stack = year_collection.toBands()

        filename = f"{prefix}{year}"
        download_image(stack, region=region, scale=scale, crs=crs, filename=filename)

