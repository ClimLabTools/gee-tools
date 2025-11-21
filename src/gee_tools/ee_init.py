import os
import pyproj
import ee

# Avoid PROJ errors in some environments
os.environ.setdefault("PROJ_LIB", pyproj.datadir.get_data_dir())

def init(project: str | None = None):
    """
    Initialize the Earth Engine API.

    Parameters
    ----------
    project : str | None
        Optional GEE project ID. If None, uses default credentials.

    Returns
    -------
    ee module
        The initialized ee module (for convenience).
    """
    if project is not None:
        ee.Initialize(project=project)
    else:
        ee.Initialize()
    return ee

