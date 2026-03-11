import io
import os
import configparser
from typing import Literal
import ee
import google.auth
import xarray as xr
import rioxarray
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import xee


config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(__file__), 'config.ini')
config.read(config_path)

PROJECT_ID = config.get('GEE', 'project_id', fallback='matilda-489310')
DEFAULT_ASSET = config.get('DEM', 'asset_path', fallback='USGS/SRTMGL1_003')

app = FastAPI(title="Matilda GEE Proxy")

def init_ee():
    try:

        credentials, project = google.auth.default()
        ee.Initialize(credentials, project=PROJECT_ID)
        print(f"GEE erfolgreich für Projekt {PROJECT_ID} initialisiert.")
    except Exception as e:
        print(f"Fehler bei der Initialisierung: {e}")


init_ee()

class GeometryPayload(BaseModel):
    type: Literal["Point", "Polygon"]
    coordinates: list


class DEMRequest(BaseModel):
    lat: float | None = None
    lon: float | None = None
    asset: str = DEFAULT_ASSET 
    buffer_m: int = 40000
    geometry: GeometryPayload | None = None

@app.get("/")
def health_check():
    return {"status": "online", "project": PROJECT_ID}

@app.post("/download-dem")
async def download_dem(req: DEMRequest):
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
        
        image = ee.Image(req.asset).select('elevation')
        
        ic = ee.ImageCollection([image])
        ds = xr.open_dataset(
            ic,
            engine='ee',
            projection=image.projection(),
            geometry=region
        )


        ds_t = ds.isel(time=0).drop_vars("time").transpose('lat', 'lon')
        ds_t.rio.set_spatial_dims("lon", "lat", inplace=True)
        ds_t.rio.write_crs(image.projection().crs().getInfo(), inplace=True)

        buffer = io.BytesIO()
        ds_t.rio.to_raster(buffer, driver="GTiff")
        buffer.seek(0)

        return StreamingResponse(
            buffer,
            media_type="image/tiff",
            headers={"Content-Disposition": "attachment; filename=dem_export.tif"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))