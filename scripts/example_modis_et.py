from pathlib import Path
import geopandas as gpd

from gee_tools.ee_init import init
from gee_tools.geometry import gdf_to_ee_fc
from gee_tools.datasets.modis_et import download_modis_et


def main():
    # Initialize Earth Engine
    ee = init(project="matilda-edu")

    # Path to the example polygon shipped with the repo
    repo_root = Path(__file__).resolve().parents[1]
    polygon_path = repo_root / "examples" / "data" / "example_polygon.geojson"

    gdf = gpd.read_file(polygon_path)
    polygon_fc = gdf_to_ee_fc(str(polygon_path), layer=None)  # layer ignored for GeoJSON

    # Download just one year for demonstration
    download_modis_et(
        polygon_fc,
        start_year=2020,
        end_year=2020,
        prefix="EXAMPLE_MODIS_ET_",
    )


if __name__ == "__main__":
    main()

