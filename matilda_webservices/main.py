import io
import os
import traceback
import base64
from zipfile import ZipFile
from typing import Literal

import asyncio
import ee
import google.auth
import xarray as xr
import numpy as np
import pandas as pd
import geopandas as gpd
import geemap
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import hashlib
import json
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

# ── Config aus Environment Variables ────────────────────────────────────────
PROJECT_ID = os.environ.get('GEE_PROJECT_ID', 'matilda-489310')
MEDIA_API_URL = os.environ.get('MEDIA_API_URL')
MEDIA_PRIVATE_KEY = os.environ.get('MEDIA_PRIVATE_KEY')
MEDIA_USER = os.environ.get('MEDIA_USER')
ERA5L_COLLECTION_ID = int(os.environ.get('ERA5L_COLLECTION_ID', 128))
ERA5L_GEOPOT_FIELD = os.environ.get('ERA5L_GEOPOT_FIELD', 'ERA5_land_Z_geopotential')

app = FastAPI(title="Matilda GEE Proxy")

def init_ee():
    try:
        credentials, project = google.auth.default()
        ee.Initialize(credentials, project=PROJECT_ID)
        print(f"GEE erfolgreich für Projekt {PROJECT_ID} initialisiert.")
    except Exception as e:
        print(f"Fehler bei der Initialisierung: {e}")

init_ee()


# ── Models ───────────────────────────────────────────────────────────────────

class GeometryPayload(BaseModel):
    type: Literal["Point", "Polygon"]
    coordinates: list

class DEMRequest(BaseModel):
    lat: float | None = None
    lon: float | None = None
    asset: str
    band: str
    buffer_m: int = 40000
    geometry: GeometryPayload | None = None

class GeopotentialRequest(BaseModel):
    catchment: dict  # GeoJSON

class ClimateRequest(BaseModel):
    catchment: dict  # GeoJSON
    date_range: list  # ['YYYY-MM-DD', 'YYYY-MM-DD']

class CMIPRequest(BaseModel):
    catchment: dict  # GeoJSON
    date_range: list  # ['YYYY-MM-DD', 'YYYY-MM-DD']

class ResourceSpace:
    def __init__(self, api_base_url, user, private_key):
        self.api_base_url = api_base_url
        self.user = user
        self.private_key = private_key

    def do_request(self, query):
        query = 'user=' + self.user + '&' + query
        sign = hashlib.sha256((self.private_key + query).encode()).hexdigest()
        url = self.api_base_url + query + '&sign=' + sign
        return requests.get(url)

    def do_search(self, search_term):
        query = 'function=do_search&search=' + requests.utils.quote(str(search_term))
        response = self.do_request(query)
        if response.status_code == 200:
            return json.loads(response.text)
        return None

    def get_resource_data(self, ref):
        query = 'function=get_resource_data&resource=' + str(ref)
        response = self.do_request(query)
        if response.status_code == 200:
            return json.loads(response.text)
        return None

    def get_resource_file(self, ref, ext=""):
        if ext == "":
            resource_data = self.get_resource_data(ref)
            ext = resource_data["file_extension"]
        query = 'function=get_resource_path&ref=' + str(ref) + '&extension=' + str(ext)
        response = self.do_request(query)
        if response.status_code == 200:
            download_url = response.text.replace('\\/', '/').strip('"')
            response = requests.get(download_url)
            if response.status_code == 200:
                return response.content
        return None

    def get_collection_resources(self, colid):
        return self.do_search('!collection' + str(colid))

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
def health_check():
    return {"status": "online", "project": PROJECT_ID}


@app.post("/download-dem")
async def download_dem(req: DEMRequest, background_tasks: BackgroundTasks):
    try:
        if req.geometry is not None:
            geometry_type = req.geometry.type
            coordinates = req.geometry.coordinates
            if geometry_type == "Point":
                if not isinstance(coordinates, list) or len(coordinates) != 2:
                    raise HTTPException(status_code=422, detail="Point geometry braucht [lon, lat] in coordinates.")
                point = ee.Geometry.Point(coordinates)
                region = point.buffer(req.buffer_m).bounds()
            elif geometry_type == "Polygon":
                region = ee.Geometry.Polygon(coordinates)
            else:
                raise HTTPException(status_code=422, detail="geometry.type muss 'Point' oder 'Polygon' sein.")
        else:
            if req.lat is None or req.lon is None:
                raise HTTPException(status_code=422, detail="Bitte entweder geometry oder lat/lon angeben.")
            point = ee.Geometry.Point([req.lon, req.lat])
            region = point.buffer(req.buffer_m).bounds()

        image = ee.Image(req.asset).select(req.band)
        ic = ee.ImageCollection([image])
        ds = xr.open_dataset(ic, engine='ee', projection=image.projection(), geometry=region)

        ds_t = ds.isel(time=0).drop_vars("time").transpose('lat', 'lon')
        ds_t.rio.set_spatial_dims("lon", "lat", inplace=True)
        ds_t.rio.write_crs(image.projection().crs().getInfo(), inplace=True)

        buffer = io.BytesIO()
        ds_t.rio.to_raster(buffer, driver="GTiff")
        buffer.seek(0)
        background_tasks.add_task(buffer.close)

        return StreamingResponse(
            buffer,
            media_type="image/tiff",
            headers={"Content-Disposition": "attachment; filename=dem_export.tif"}
        )

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/geopotential")
async def get_geopotential(req: GeopotentialRequest):
    try:
        # 1. ZIP vom Media-Server holen
        myrepository = ResourceSpace(MEDIA_API_URL, MEDIA_USER, MEDIA_PRIVATE_KEY)
        
        refs_raw = myrepository.get_collection_resources(ERA5L_COLLECTION_ID)
        print("RAW RESULT:", refs_raw)
        refs_era5l = pd.DataFrame(refs_raw)
        
        ref_geopot = refs_era5l.loc[refs_era5l['field8'] == ERA5L_GEOPOT_FIELD]

        if ref_geopot.empty:
            raise HTTPException(status_code=404, detail=f"Kein Datensatz mit field8='{ERA5L_GEOPOT_FIELD}' gefunden.")

        content = myrepository.get_resource_file(ref_geopot.at[ref_geopot.index[0], 'ref'])

        # 2. Entpacken → NetCDF einlesen
        with ZipFile(io.BytesIO(content), 'r') as zipObj:
            filename = zipObj.namelist()[0]
            file_bytes = zipObj.read(filename)

        ds = xr.open_dataset(io.BytesIO(file_bytes), engine='h5netcdf')

        # 3. Catchment BBox + croppen
        catchment_gdf = gpd.GeoDataFrame.from_features(req.catchment['features'])
        catchment_gdf = catchment_gdf.set_crs('EPSG:4326')
        bounds = catchment_gdf.total_bounds
        min_lon, min_lat, max_lon, max_lat = bounds[0]-1, bounds[1]-1, bounds[2]+1, bounds[3]+1
        cropped_ds = ds.sel(lat=slice(min_lat, max_lat), lon=slice(min_lon, max_lon))

        # 4. GEE-Konvertierung + Reducer
        catchment_ee = geemap.geopandas_to_ee(catchment_gdf)
        data = cropped_ds['z']
        lon_data = np.round(data['lon'], 3)
        lat_data = np.round(data['lat'], 3)
        dim_lon = np.unique(np.ediff1d(lon_data).round(3))
        dim_lat = np.unique(np.ediff1d(lat_data).round(3))

        data_np = np.transpose(np.array(data))

        if np.max(lon_data) > 180:
            data_np = np.roll(data_np, 180, axis=0)
            west_lon = lon_data[0] - 180
        else:
            west_lon = lon_data[0]

        transform = [dim_lon[0], 0, float(west_lon) - dim_lon[0]/2, 0, dim_lat[0], float(lat_data[0]) - dim_lat[0]/2]
        image = geemap.numpy_to_ee(data_np, "EPSG:4326", transform=transform, band_names='z')

        result = image.reduceRegion(ee.Reducer.mean(), geometry=catchment_ee, crs='EPSG:4326', crsTransform=transform)
        mean_val = result.getInfo()['z']
        ele_dat = mean_val / 9.80665

        # 5. Gecroptes NetCDF base64-encodieren
        netcdf_buffer = io.BytesIO()
        cropped_ds.to_netcdf(netcdf_buffer, engine='h5netcdf')
        netcdf_buffer.seek(0)
        netcdf_b64 = base64.b64encode(netcdf_buffer.read()).decode('utf-8')

        return {
            "elevation_m": round(ele_dat, 2),
            "geopotential_mean": round(mean_val, 2),
            "netcdf": netcdf_b64
        }

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/climate-data")
async def get_climate_data(req: ClimateRequest):
    try:
        # 1. Catchment zu GEE konvertieren
        catchment_gdf = gpd.GeoDataFrame.from_features(req.catchment['features'])
        catchment_gdf = catchment_gdf.set_crs('EPSG:4326')
        catchment_ee = geemap.geopandas_to_ee(catchment_gdf)

        # 2. ERA5-Land Collection filtern + reduceRegion
        def set_property(image):
            d = image.reduceRegion(ee.Reducer.mean(), catchment_ee)
            return image.set(d)

        collection = ee.ImageCollection('ECMWF/ERA5_LAND/DAILY_RAW') \
            .select('temperature_2m', 'total_precipitation_sum') \
            .filterDate(req.date_range[0], req.date_range[1])

        with_mean = collection.map(set_property)

        # 3. Rohe Arrays zurückschicken
        timestamps = with_mean.aggregate_array('system:time_start').getInfo()
        temp = with_mean.aggregate_array('temperature_2m').getInfo()
        prec = with_mean.aggregate_array('total_precipitation_sum').getInfo()

        return {
            "timestamps": timestamps,
            "temp": temp,
            "prec": prec
        }

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    

@app.post("/cmip6-download")
async def cmip6_download(req: CMIPRequest):
    try:
        catchment_gdf = gpd.GeoDataFrame.from_features(req.catchment['features'])
        catchment_gdf = catchment_gdf.set_crs('EPSG:4326')
        catchment_ee = geemap.geopandas_to_ee(catchment_gdf)

        starty = int(req.date_range[0][:4])
        endy = int(req.date_range[1][:4])

        @retry(stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=1, max=60))
        def download_year(var, year):
            start = f"{year}-01-01"
            end = f"{year + 1}-01-01"
            start_date = ee.Date(start)
            end_date = ee.Date(end)
            n = end_date.difference(start_date, 'day').subtract(1)

            collection = ee.ImageCollection('NASA/GDDP-CMIP6') \
                .select(var) \
                .filterDate(start_date, end_date) \
                .filterBounds(catchment_ee) \
                .filter(ee.Filter.neq('model', 'NorESM2-LM'))

            def rename_band(b):
                split = ee.String(b).split('_')
                return ee.String(split.splice(split.length().subtract(2), 1).join("_"))

            def build_feature(i):
                t1 = start_date.advance(i, 'day')
                t2 = t1.advance(1, 'day')
                daily_coll = collection.filterDate(t1, t2)
                daily_img = daily_coll.toBands()
                bands = daily_img.bandNames()
                renamed = bands.map(rename_band)
                d = daily_img.rename(renamed).reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=catchment_ee,
                ).combine(
                    ee.Dictionary({'system:time_start': t1.millis(), 'isodate': t1.format('YYYY-MM-dd')})
                )
                return ee.Feature(None, d)

            year_feature = ee.FeatureCollection(ee.List.sequence(0, n).map(build_feature))
            url = year_feature.getDownloadURL()

            r = requests.get(url, stream=True)
            r.raise_for_status()
            return r.text

        async def generate():
            for var in ['tas', 'pr']:
                for year in range(starty, endy + 1):
                    try:
                        csv_text = await asyncio.get_event_loop().run_in_executor(
                            None, download_year, var, year
                        )
                        chunk = json_lib.dumps({
                            "year": year,
                            "var": var,
                            "csv": csv_text
                        }) + "\n"
                        yield chunk.encode('utf-8')

                    except Exception as e:
                        error_chunk = json_lib.dumps({
                            "year": year,
                            "var": var,
                            "error": str(e)
                        }) + "\n"
                        yield error_chunk.encode('utf-8')

        return StreamingResponse(generate(), media_type="application/x-ndjson")

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))