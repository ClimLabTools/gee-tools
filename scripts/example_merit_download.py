from gee_tools.ee_init import init
from gee_tools.geometry import gdf_to_ee_fc, polygon_geometry
from gee_tools.datasets.dem_merit import download_merit_dem
import geopandas as gpd

def main():
    # Initialize Earth Engine
    ee = init(project="matilda-edu")

    # Load your polygon(s)
    path = "/home/phillip/Seafile/EBA-CA/Papers/No3_Issyk-Kul/geodata/issykul_vectors.gpkg"
    layer = "catchment_new"

    gdf = gpd.read_file(path, layer=layer)
    fc = gdf_to_ee_fc(path, layer)
    region = fc.geometry()  # or polygon_geometry(fc)?!

    out_dem = "/tmp/MERIT30_dem_issyk_kul.tif"
    download_merit_dem(region_geom=region, out_path=out_dem)

if __name__ == "__main__":
    main()

