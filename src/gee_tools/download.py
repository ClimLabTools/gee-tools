"""
Download and raster utilities for gee_tools.

This module provides:
- download_image: download a single ee.Image via getDownloadURL as a .zip
- stitch_and_clip_from_zips: merge tiled GeoTIFFs and clip to a polygon
- get_image_from_config: convenience for assets described by a small config list
- download_dem: generic DEM downloader using xarray->GeoTIFF with geemap fallback
"""

from __future__ import annotations

from typing import List, Tuple, Dict, Any

import os
import glob
import zipfile

import requests
import rasterio
from rasterio.merge import merge
from rasterio.mask import mask

import xarray as xr
import rioxarray  # noqa: F401  # needed to register .rio accessor
import xee        # noqa: F401  # needed to register the "ee" engine with xarray
import geemap
import ee
from dask.diagnostics import ProgressBar

# ---------------------------------------------------------------------------
# Basic ee.Image download helper
# ---------------------------------------------------------------------------

def download_image(
    image: ee.Image,
    region: Dict[str, Any],
    scale: int,
    crs: str,
    filename: str,
) -> str:
    """
    Download a single ee.Image using getDownloadURL as a .zip file.
    """
    params = {
        "name": filename,
        "scale": scale,
        "crs": crs,
        "region": region,
        "filePerBand": False,
    }
    url = image.getDownloadURL(params)
    out_zip = f"{filename}.zip"

    print(f"Downloading {filename} from {url} ...")

    resp = requests.get(url, stream=True)
    resp.raise_for_status()

    with open(out_zip, "wb") as f:
        for chunk in resp.iter_content(8192):
            f.write(chunk)

    print(f"Saved {out_zip}")
    return out_zip


# ---------------------------------------------------------------------------
# Local stitching and clipping of GeoTIFF tiles
# ---------------------------------------------------------------------------

def stitch_and_clip_from_zips(
    zip_pattern: str,
    out_mosaic: str,
    out_clipped: str,
    clip_gdf,
) -> None:
    """
    Stitch multiple downloaded tiles into a mosaic and clip to a GeoDataFrame.

    Parameters
    ----------
    zip_pattern : str
        Glob pattern for zip files, e.g. "GLC_annual_*_tile_*.zip"
        or "GLC_annual_*_tile_*/*.tif" if you already extracted TIFFs.
    out_mosaic : str
        Path to the intermediate mosaic GeoTIFF.
    out_clipped : str
        Path to the final, clipped GeoTIFF.
    clip_gdf : geopandas.GeoDataFrame
        Vector geometries to clip to (e.g. your polygon(s)).
    """
    zip_files = glob.glob(zip_pattern)
    tif_files: List[str] = []

    # If pattern matches TIFFs directly, just use them
    if any(z.lower().endswith(".tif") for z in zip_files):
        tif_files = [z for z in zip_files if z.lower().endswith(".tif")]
    else:
        # Otherwise assume we have zip archives containing TIFFs
        for z in zip_files:
            with zipfile.ZipFile(z, "r") as zf:
                extract_dir = z[:-4]
                zf.extractall(extract_dir)
                tif_files.extend(
                    glob.glob(os.path.join(extract_dir, "*.tif"))
                )

    if not tif_files:
        raise ValueError(f"No TIFFs found for pattern {zip_pattern}")

    # Merge
    srcs = [rasterio.open(t) for t in tif_files]
    mosaic, out_trans = merge(srcs)
    meta = srcs[0].meta.copy()
    meta.update({
        "height": mosaic.shape[1],
        "width": mosaic.shape[2],
        "transform": out_trans,
    })
    for src in srcs:
        src.close()

    with rasterio.open(out_mosaic, "w", **meta) as dst:
        dst.write(mosaic)

    # Clip to polygon(s)
    with rasterio.open(out_mosaic) as src:
        out_img, out_tr = mask(src, clip_gdf.geometry, crop=True)
        out_meta = src.meta.copy()
        out_meta.update({
            "height": out_img.shape[1],
            "width": out_img.shape[2],
            "transform": out_tr,
        })

    with rasterio.open(out_clipped, "w", **out_meta) as dst:
        dst.write(out_img)


# ---------------------------------------------------------------------------
# Generic DEM downloader
# ---------------------------------------------------------------------------

def get_image_from_config(cfg: list) -> ee.Image:
    """
    Construct an ee.Image from a simple config list.

    Parameters
    ----------
    cfg : list
        ['Image'|'ImageCollection', asset_id, band, name]

    Returns
    -------
    ee.Image
        The selected band as an ee.Image (mosaic for collections).
    """
    kind, asset_id, band, *_ = cfg
    if kind == "Image":
        return ee.Image(asset_id).select(band)
    elif kind == "ImageCollection":
        return ee.ImageCollection(asset_id).select(band).mosaic()
    else:
        raise ValueError(f"Unknown kind: {kind}")


def download_dem(
    dem_config: list,
    region_geom: ee.Geometry,
    out_path: str,
    scale: int = 30,
    scale_factor: float = 1.0,
) -> None:
    """
    Generic DEM downloader using xarray->GeoTIFF with geemap fallback.
    """
    img = get_image_from_config(dem_config)

    if scale_factor != 1:
        img = img.multiply(scale_factor).toFloat().rename(
            f"{dem_config[2]}_scaled"
        )

    img = img.clip(region_geom)

    try:
        print("Trying xarray/xee export for DEM...")
        ic = ee.ImageCollection(img)
        ds = xr.open_dataset(
            ic,
            engine="ee",
            projection=img.projection(),
            geometry=region_geom,
        )
        ds_t = ds.isel(time=0).drop_vars("time").transpose()
        ds_t.rio.set_spatial_dims("lon", "lat", inplace=True)
        ds_t.rio.to_raster(out_path)
        print(f"DEM saved via xarray/xee: {out_path}")
    except Exception as e:
        print(f"xarray/xee path failed ({e}), falling back to geemap.ee_export_image...")
        geemap.ee_export_image(
            img,
            filename=out_path,
            scale=scale,
            region=region_geom,
            file_per_band=False,
        )
        print(f"DEM saved via geemap.ee_export_image: {out_path}")

