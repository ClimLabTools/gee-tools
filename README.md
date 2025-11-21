# gee-tools

**gee-tools** is a lightweight Python package that provides reusable utilities for working with  
**Google Earth Engine (GEE)** in hydrology, climatology, and environmental data workflows.

It abstracts common patterns such as:

- loading and working with polygon geometries  
- downloading image datasets from GEE (single images or time-series collections)  
- stitching and clipping tiled exports  
- using modern xarray/xee pipelines to avoid GEE download size limits  
- dataset-specific “recipes” for common Earth Engine assets (e.g., MERIT DEM, MODIS ET)

The package is designed to be imported from any modeling or data-processing project, enabling consistent and reproducible workflows without rewriting boilerplate each time.

---

## Features

### ✔️ Geometry utilities
- Load polygons from GeoPackages, Shapefiles, or GeoJSON into GEE  
- Compute region geometry and bounding boxes  
- Create buffered boxes around points  
- Tile bounding boxes into a grid (EE or GeoJSON output)

### ✔️ Download utilities
- Robust `download_image()` wrapper around `getDownloadURL()`  
- Local stitching + clipping of exported TIFF tiles  
- Generic DEM downloader (`download_dem`) using:
  - **xarray + xee** for efficient chunked exports  
  - fallback to `geemap.ee_export_image()` if needed

### ✔️ Dataset modules
Currently implemented:

- **MERIT DEM** — robust DEM downloader with unit scaling  
- **MODIS MOD16A2GF ET** — yearly ET stacks as multi-band GeoTIFFs  

More datasets are planned (see TODO below).

### ✔️ Examples
The repo includes fully functioning example scripts demonstrating how to use the package.

---

## Installation

Create a fresh environment (recommended):

```bash
conda create -n gee-tools-dev python=3.11
conda activate gee-tools-dev
```

Install in editable mode:

```bash
pip install -e '.[dev]'
```

Authenticate Earth Engine (once):

```bash
earthengine authenticate
```

---

## Usage

### Example: Download MERIT DEM

```python
from pathlib import Path
import geopandas as gpd

from gee_tools.ee_init import init
from gee_tools.geometry import gdf_to_ee_fc
from gee_tools.datasets.dem_merit import download_merit_dem

# Initialize Earth Engine
ee = init(project="your-ee-project")

# Load polygon
polygon_path = Path("examples/data/example_polygon.geojson")
polygon_fc = gdf_to_ee_fc(str(polygon_path), layer=None)

# Use polygon geometry
region = polygon_fc.geometry()

# Download MERIT DEM clipped to region
download_merit_dem(
    region_geom=region,
    out_path="MERIT_DEM_example.tif",
)
```

### Example: Download one year of MODIS ET

```python
from gee_tools.ee_init import init
from gee_tools.geometry import gdf_to_ee_fc
from gee_tools.datasets.modis_et import download_modis_et
from pathlib import Path

ee = init(project="your-ee-project")

polygon_path = Path("examples/data/example_polygon.geojson")
polygon_fc = gdf_to_ee_fc(str(polygon_path), layer=None)

download_modis_et(
    polygon_fc,
    start_year=2020,
    end_year=2020,
    prefix="ET_2020_",
)
```

---

## Repository structure

```
gee-tools/
│
├── src/gee_tools/                # Package source code
│   ├── geometry.py               # Polygon/bbox helpers, tiling utilities
│   ├── download.py               # Core download + stitching tools
│   ├── ee_init.py                # Earth Engine initialization
│   └── datasets/                 # Dataset-specific downloaders
│       ├── modis_et.py
│       ├── dem_merit.py
│       └── ... (more coming)
│
├── scripts/                      # Example workflow scripts
│   ├── example_modis_et.py
│   ├── example_merit_download.py
│   └── ...
│
├── examples/
│   └── data/
│       └── example_polygon.geojson
│
├── tests/                        # Unit tests
│   └── test_geometry.py
│
├── pyproject.toml                # Package metadata & dependencies
├── README.md
└── LICENSE
```

---

## TODO / Roadmap

The following dataset modules are **planned but not yet implemented**:

### 🟡 Soil HSG (HiHydroSoil)
- Single-image download  
- Simple wrapper around `download_image()`  

### 🟡 GLC-FCS30D (annual & 5-year composites)
- Requires bbox tiling (3×3 or configurable)
- Per-tile download
- Final stitching + clipping

### 🟡 ERA5-Land daily temperature
- Full xarray/xee workflow
- NetCDF or Zarr export
- Chunking and unit conversion options

### 🟡 Config-driven workflows
Goal: run entire pipelines from a config, e.g.:

```yaml
datasets:
  - type: modis_et
    years: 2000-2020
  - type: merit_dem
polygon: examples/data/example_polygon.geojson
output_dir: results/
```

Then simply:

```bash
gee-tools run config.yml
```

### 🟡 Additional tests
- More robust tiling & geometry tests  
- Tests for stitching/clipping with synthetic rasters  
- Optional EE integration tests  

---

## Contributing

Pull requests are welcome — especially for new dataset modules or workflow improvements.  
Before submitting a PR:

```bash
ruff check .
black .
pytest
```

---

## License

MIT License — free to use and modify.
