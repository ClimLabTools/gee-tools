"""
Geometry utilities for gee_tools.

- Load a polygon (e.g. a catchment) from a GeoPackage as an ee.FeatureCollection
- Derive polygon geometry and bounding box
- Build buffered boxes around points
- Tile a bounding box into a regular grid (no GEE auth needed for this)
"""

from __future__ import annotations

from typing import Iterable, List, Tuple, Union, Dict, Any

import geopandas as gpd
import geemap
import ee


def gdf_to_ee_fc(path: str, layer: str) -> ee.FeatureCollection:
    """
    Read a layer from a GeoPackage (or other vector file) into a GeoDataFrame
    and convert it to an Earth Engine FeatureCollection.

    Parameters
    ----------
    path : str
        Path to the vector dataset (e.g. .gpkg, .shp).
    layer : str
        Layer name inside the dataset (for GeoPackage / multi-layer formats).

    Returns
    -------
    ee.FeatureCollection
        The converted feature collection.
    """
    gdf = gpd.read_file(path, layer=layer)
    return geemap.geopandas_to_ee(gdf)


def polygon_geometry(fc: ee.FeatureCollection) -> ee.Geometry:
    """
    Get the combined geometry of a FeatureCollection (e.g. catchments).
    """
    return fc.geometry()


def bbox_geometry(fc: ee.FeatureCollection) -> ee.Geometry:
    """
    Get the bounding box geometry of a FeatureCollection.
    """
    return fc.geometry().bounds()


def build_box_from_point(lon: float, lat: float, buffer_m: float = 40_000) -> ee.Geometry:
    """
    Build a rectangular bounding box around a point with a buffer in meters.
    """
    pt = ee.Geometry.Point(lon, lat)
    return pt.buffer(buffer_m).bounds()


# ---------------------------------------------------------------------------
# Tiling helpers
# ---------------------------------------------------------------------------

BBoxLike = Union[ee.Geometry, Dict[str, Any]]


def _coords_from_bbox(bbox: BBoxLike) -> Tuple[float, float, float, float]:
    """
    Internal helper to extract (xmin, ymin, xmax, ymax) from either
    an ee.Geometry (assumed to be a rectangle) or a GeoJSON-like dict.
    """
    if isinstance(bbox, ee.geometry.Geometry):
        info = bbox.getInfo()
    else:
        info = bbox

    coords = info["coordinates"][0]
    xmin, ymin = coords[0]
    xmax, ymax = coords[2]
    return xmin, ymin, xmax, ymax


def tile_bbox(
    bbox: BBoxLike,
    n_rows: int,
    n_cols: int,
    as_ee: bool = False,
) -> List[Tuple[Tuple[int, int], Any]]:
    """
    Split a bounding box into n_rows x n_cols rectangular tiles.

    Parameters
    ----------
    bbox : ee.Geometry or GeoJSON-like dict
        Bounding box geometry (e.g. from polygon_bbox_geometry(fc)) or an
        already materialized dict (useful for testing without GEE).
    n_rows : int
        Number of rows.
    n_cols : int
        Number of columns.
    as_ee : bool, default False
        If True, return ee.Geometry.Rectangle objects (requires EE init).
        If False, return GeoJSON-like dict polygons (no EE required).

    Returns
    -------
    list
        List of ((i, j), geometry) tuples, where
        i = column index [0..n_cols-1],
        j = row index    [0..n_rows-1],
        geometry is either a GeoJSON-like dict or an ee.Geometry.Rectangle.
    """
    xmin, ymin, xmax, ymax = _coords_from_bbox(bbox)

    x_steps = [xmin + i * (xmax - xmin) / n_cols for i in range(n_cols + 1)]
    y_steps = [ymin + j * (ymax - ymin) / n_rows for j in range(n_rows + 1)]

    tiles: List[Tuple[Tuple[int, int], Any]] = []
    for i in range(n_cols):
        for j in range(n_rows):
            x0, y0 = x_steps[i], y_steps[j]
            x1, y1 = x_steps[i + 1], y_steps[j + 1]

            if as_ee:
                geom = ee.Geometry.Rectangle([x0, y0, x1, y1])
            else:
                geom = {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [x0, y0],
                            [x1, y0],
                            [x1, y1],
                            [x0, y1],
                            [x0, y0],
                        ]
                    ],
                }

            tiles.append(((i, j), geom))

    return tiles

