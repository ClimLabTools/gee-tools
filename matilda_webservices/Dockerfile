# 1. Basis-Image mit Python 3.10
FROM python:3.11-slim

# 2. System-Abhängigkeiten für Geodaten (GDAL/PROJ) installieren
# Das ist wichtig, damit rioxarray und rasterio funktionieren
RUN apt-get update && apt-get install -y \
    binutils \
    libproj-dev \
    gdal-bin \
    libgdal-dev \
    python3-gdal \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# 3. Arbeitsverzeichnis im Container festlegen
WORKDIR /app

# 4. Anforderungen kopieren und installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Den Rest deines Codes kopieren (inkl. main.py und config.ini)
COPY . .

# 6. Den Server mit Uvicorn starten
# Port 8080 ist der Standard-Port für Google Cloud Run
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]