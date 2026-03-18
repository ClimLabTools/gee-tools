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
from pydantic import Field

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
#Validations of requests
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
    max_concurrent: int = Field(default=5, ge=1, le=100)

class CMIPRequest(BaseModel):
    catchment: dict  # GeoJSON
    date_range: list  # ['YYYY-MM-DD', 'YYYY-MM-DD']
    max_concurrent: int = Field(default=5, ge=1, le=100)

class ResourceSpace:
    def __init__(self, api_base_url, user, private_key): #Initializing (saving URL,name and kex)
        self.api_base_url = api_base_url
        self.user = user
        self.private_key = private_key

    def do_request(self, query):
        query = 'user=' + self.user + '&' + query #authentication
        sign = hashlib.sha256((self.private_key + query).encode()).hexdigest()
        url = self.api_base_url + query + '&sign=' + sign
        return requests.get(url)

    def do_search(self, search_term): #requesting a search and is sending a JSON back
        query = 'function=do_search&search=' + requests.utils.quote(str(search_term))
        response = self.do_request(query)
        if response.status_code == 200:
            return json.loads(response.text)
        return None

    def get_resource_data(self, ref): #get Metadata
        query = 'function=get_resource_data&resource=' + str(ref)
        response = self.do_request(query)
        if response.status_code == 200:
            return json.loads(response.text)
        return None

    def get_resource_file(self, ref, ext=""): #wrapper function
        if ext == "":
            resource_data = self.get_resource_data(ref) #gets metadata; only needed if not ending given 
            ext = resource_data["file_extension"]
        query = 'function=get_resource_path&ref=' + str(ref) + '&extension=' + str(ext)
        response = self.do_request(query) #gets downnload url
        if response.status_code == 200:
            download_url = response.text.replace('\\/', '/').strip('"')
            response = requests.get(download_url) #loads the requested data
            if response.status_code == 200:
                return response.content
        return None

    def get_collection_resources(self, colid):
        return self.do_search('!collection' + str(colid))

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
def health_check(): #check whether API is running
    return {"status": "online", "project": PROJECT_ID}


@app.post("/download-dem")
async def download_dem(req: DEMRequest, background_tasks: BackgroundTasks):
    try:
        if req.geometry is not None:
            geometry_type = req.geometry.type
            coordinates = req.geometry.coordinates
            if geometry_type == "Point":
                if not isinstance(coordinates, list) or len(coordinates) != 2:
                    raise HTTPException(status_code=422, detail="Point geometry needs [lon, lat] in coordinates.")
                point = ee.Geometry.Point(coordinates)
                region = point.buffer(req.buffer_m).bounds() #circle: Bounding Box
            elif geometry_type == "Polygon":
                region = ee.Geometry.Polygon(coordinates) #taking Polygon
            else:
                raise HTTPException(status_code=422, detail="geometry.type muss 'Point' oder 'Polygon' sein.")
        else:
            if req.lat is None or req.lon is None:
                raise HTTPException(status_code=422, detail="Please give a geometry or lat/lon.")
            point = ee.Geometry.Point([req.lon, req.lat])
            region = point.buffer(req.buffer_m).bounds() 

        # loading data from GEE
        image = ee.Image(req.asset).select(req.band)
        ic = ee.ImageCollection([image])
        ds = xr.open_dataset(ic, engine='ee', projection=image.projection(), geometry=region)

        ds_t = ds.isel(time=0).drop_vars("time").transpose('lat', 'lon')
        ds_t.rio.set_spatial_dims("lon", "lat", inplace=True)
        ds_t.rio.write_crs(image.projection().crs().getInfo(), inplace=True)

        buffer = io.BytesIO()
        ds_t.rio.to_raster(buffer, driver="GTiff")
        buffer.seek(0)
        background_tasks.add_task(buffer.close) # close ofter streaming; finally command

        return StreamingResponse(
            buffer,
            media_type="image/tiff",
            headers={"Content-Disposition": "attachment; filename=dem_export.tif"}
        )
    # ensures that log Stacktrace is send to Server and printed as an error message
    except HTTPException: 
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    

##### STREAMING ######
    '''
    Data is read and send partly: chunks possible
    '''


@app.post("/geopotential")
async def get_geopotential(req: GeopotentialRequest):
    try:
        # Get ZIP from Media server
        myrepository = ResourceSpace(MEDIA_API_URL, MEDIA_USER, MEDIA_PRIVATE_KEY)
        
        refs_raw = myrepository.get_collection_resources(ERA5L_COLLECTION_ID)
        print("RAW RESULT:", refs_raw)
        refs_era5l = pd.DataFrame(refs_raw)
        
        ref_geopot = refs_era5l.loc[refs_era5l['field8'] == ERA5L_GEOPOT_FIELD]

        if ref_geopot.empty:
            raise HTTPException(status_code=404, detail=f"Kein Datensatz mit field8='{ERA5L_GEOPOT_FIELD}' gefunden.")

        content = myrepository.get_resource_file(ref_geopot.at[ref_geopot.index[0], 'ref'])

        # read NetCDF
        with ZipFile(io.BytesIO(content), 'r') as zipObj:
            filename = zipObj.namelist()[0]
            file_bytes = zipObj.read(filename)

        ds = xr.open_dataset(io.BytesIO(file_bytes), engine='h5netcdf')

        # Catchment BBox + croppen
        catchment_gdf = gpd.GeoDataFrame.from_features(req.catchment['features'])
        catchment_gdf = catchment_gdf.set_crs('EPSG:4326')
        bounds = catchment_gdf.total_bounds
        min_lon, min_lat, max_lon, max_lat = bounds[0]-1, bounds[1]-1, bounds[2]+1, bounds[3]+1
        cropped_ds = ds.sel(lat=slice(min_lat, max_lat), lon=slice(min_lon, max_lon))

        #  GEE-Konvertierung + Reducer
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

        # encode NetCDF base64
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
    

#TO DO parralleisieren? Zu langsam
    
# @app.post("/climate-data")
# async def get_climate_data(req: ClimateRequest):
#     try:
#         # convert Catchment zto GEE
#         catchment_gdf = gpd.GeoDataFrame.from_features(req.catchment['features'])
#         catchment_gdf = catchment_gdf.set_crs('EPSG:4326')
#         catchment_ee = geemap.geopandas_to_ee(catchment_gdf)

#         # filter ERA5-Land Collection + reduceRegion
#         def set_property(image):
#             d = image.reduceRegion(ee.Reducer.mean(), catchment_ee)
#             return image.set(d)

#         collection = ee.ImageCollection('ECMWF/ERA5_LAND/DAILY_RAW') \
#             .select('temperature_2m', 'total_precipitation_sum') \
#             .filterDate(req.date_range[0], req.date_range[1])

#         with_mean = collection.map(set_property)

#         # send back raw errors
#         timestamps = with_mean.aggregate_array('system:time_start').getInfo()
#         temp = with_mean.aggregate_array('temperature_2m').getInfo()
#         prec = with_mean.aggregate_array('total_precipitation_sum').getInfo()

#         return {
#             "timestamps": timestamps,
#             "temp": temp,
#             "prec": prec
#         }

#     except HTTPException:
#         raise
#     except Exception as e:
#         traceback.print_exc()
#         raise HTTPException(status_code=500, detail=str(e))

@app.post("/climate-data")
async def get_climate_data(req: ClimateRequest):
    try:
        catchment_gdf = gpd.GeoDataFrame.from_features(req.catchment['features'])
        catchment_gdf = catchment_gdf.set_crs('EPSG:4326')
        catchment_ee = geemap.geopandas_to_ee(catchment_gdf)
        starty = int(req.date_range[0][:4])
        endy   = int(req.date_range[1][:4])
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

    @retry(stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=1, max=60))
    def fetch_year(year):
        start = f"{year}-01-01"
        end   = f"{year + 1}-01-01"

        def set_property(image):
            d = image.reduceRegion(ee.Reducer.mean(), catchment_ee)
            return image.set(d)

        collection = ee.ImageCollection('ECMWF/ERA5_LAND/DAILY_RAW') \
            .select('temperature_2m', 'total_precipitation_sum') \
            .filterDate(start, end)

        with_mean = collection.map(set_property)

        result = with_mean.reduceColumns(
            ee.Reducer.toList(3),
            ['system:time_start', 'temperature_2m', 'total_precipitation_sum']
        ).getInfo()

        values = result['list']
        return {
            "timestamps": [row[0] for row in values],
            "temp":       [row[1] for row in values],
            "prec":       [row[2] for row in values]
        }

    async def generate():

        semaphore = asyncio.Semaphore(req.max_concurrent)

        async def fetch_one(year):
            async with semaphore:
                loop = asyncio.get_event_loop()
                try:
                    data = await loop.run_in_executor(None, fetch_year, year)
                    return json.dumps({"year": year, **data}) + "\n"
                except Exception as e:
                    print(f"Error{year}: {e}")
                    return json.dumps({"year": year, "error": str(e)}) + "\n"

        years = list(range(starty, endy + 1))
        print(f"{len(years)} years created")

        queue = asyncio.Queue()

        async def worker(year):
            result = await fetch_one(year)
            await queue.put(result)

        tasks = [asyncio.create_task(worker(year)) for year in years]

        for _ in years:
            chunk = await queue.get()
            yield chunk.encode('utf-8')

        await asyncio.gather(*tasks)

    return StreamingResponse(generate(), media_type="application/x-ndjson")
    
# 30 threads gleichzeitig/ multi processing 

@app.post("/cmip6-download")
async def cmip6_download(req: CMIPRequest):
    try:
        catchment_gdf = gpd.GeoDataFrame.from_features(req.catchment['features'])
        catchment_gdf = catchment_gdf.set_crs('EPSG:4326')
        catchment_ee = geemap.geopandas_to_ee(catchment_gdf) #convert in GEE object

        starty = int(req.date_range[0][:4])
        endy = int(req.date_range[1][:4])
    
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

    @retry(stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=1, max=60)) #retry up to 10 time when API connection is instable
    def download_year(var, year): #filter data
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

            #calculate the spatial mean
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
        features = year_feature.getInfo()
        rows = [feat['properties'] for feat in features['features']]
        df = pd.DataFrame(rows)

        if 'system:index' not in df.columns:
            df['system:index'] = range(len(df))
        if '.geo' not in df.columns:
            df['.geo'] = None

        return df.to_csv(index=False)

    #async: running `run_in_executor`** – `download_year` in a separate thread to not block Server
    # Streaming data with Chunks: Sending yearly data directly and not all at once
        


    async def generate():
        print("🟢 generate() gestartet")

        semaphore = asyncio.Semaphore(req.max_concurrent)

        async def download_one(var, year):
            async with semaphore:
                loop = asyncio.get_event_loop()
                try:
                    csv_text = await loop.run_in_executor(None, download_year, var, year)
                    return json.dumps({"year": year, "var": var, "csv": csv_text}) + "\n"
                except Exception as e:
                    # Echten Fehler aus RetryError extrahieren
                    cause = e.last_attempt.exception() if hasattr(e, 'last_attempt') else e
                    print(f"🔴 {var} {year}: {cause}")
                    return json.dumps({"year": year, "var": var, "error": str(cause)}) + "\n"

        combinations = [
            (var, year)
            for var in ['tas', 'pr']
            for year in range(starty, endy + 1)
        ]
        print(f"🟢 {len(combinations)} Tasks erstellt")

        queue = asyncio.Queue()

        async def worker(var, year):
            result = await download_one(var, year)
            await queue.put(result)

        tasks = [asyncio.create_task(worker(var, year)) for var, year in combinations]

        for _ in combinations:
            chunk = await queue.get()
            yield chunk.encode('utf-8')

        await asyncio.gather(*tasks)

    return StreamingResponse(generate(), media_type="application/x-ndjson")
