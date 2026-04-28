FROM python:3.11-slim

# System libraries needed by rasterio / geopandas / pyogrio.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgdal-dev gdal-bin libgeos-dev libproj-dev \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so layer caching works on code-only changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code (data files are pulled at runtime via download_data.py).
COPY . .

# HF Spaces injects $PORT (default 7860). Bind there.
ENV PORT=7860 \
    HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1
EXPOSE 7860

# On boot: fetch raster + ports from HF dataset, then serve via gunicorn.
# -w 1: single worker so the 600 MB raster isn't duplicated in RAM.
# -t 300: long timeout for the first MCP request (30-60 s).
CMD python scripts/download_data.py && \
    gunicorn -b 0.0.0.0:${PORT} -w 1 -t 300 --preload app:app
