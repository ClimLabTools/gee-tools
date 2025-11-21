from gee_tools.ee_init import init
from gee_tools.geometry import gdf_to_ee_fc
from gee_tools.datasets.modis_et import download_modis_et

import geopandas as gpd


def main():
    ee = init(project="matilda-edu")

    path = "/home/phillip/Seafile/EBA-CA/Papers/No3_Issyk-Kul/geodata/issykul_vectors.gpkg"
    layer = "catchment_new"

    gdf = gpd.read_file(path, layer=layer)
    polygon_fc = gdf_to_ee_fc(path, layer)

    download_modis_et(
        polygon_fc,
        start_year=2000,
        end_year=2001,   # small range for testing
        scale=500,
        crs="EPSG:4326",
        prefix="TEST_MODIS_ET_"
    )


if __name__ == "__main__":
    main()

