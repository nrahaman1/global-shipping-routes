"""Step 1 — preprocess the global ship-density raster.

Downsamples the ~500 m int32 raster to ~2 km (0.02°) using max-resampling so
narrow lanes survive, replaces nodata with 0, applies log1p, and writes a
float32 GeoTIFF. Land pixels are already encoded as 0 in the source, so no
separate land mask is required.
"""
from pathlib import Path
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "shipdensity_global" / "shipdensity_global.tif"
DST = ROOT / "work" / "density_log_2km.tif"
DST.parent.mkdir(parents=True, exist_ok=True)

FACTOR = 4  # 0.005° -> 0.02°

with rasterio.open(SRC) as src:
    src_nodata = src.nodata
    new_width = src.width // FACTOR
    new_height = src.height // FACTOR
    new_transform = src.transform * src.transform.scale(
        src.width / new_width, src.height / new_height
    )

    print(f"Source: {src.width}x{src.height} @ {src.res}")
    print(f"Target: {new_width}x{new_height} @ {new_transform.a}, {new_transform.e}")

    dst_arr = np.zeros((new_height, new_width), dtype=np.int32)

    reproject(
        source=rasterio.band(src, 1),
        destination=dst_arr,
        src_transform=src.transform,
        src_crs=src.crs,
        src_nodata=src_nodata,
        dst_transform=new_transform,
        dst_crs=src.crs,
        dst_nodata=0,
        resampling=Resampling.max,
        num_threads=4,
    )

    # Replace any residual nodata sentinel with 0 and log1p
    dst_arr[dst_arr == src_nodata] = 0
    dst_arr[dst_arr < 0] = 0
    logd = np.log1p(dst_arr.astype(np.float64)).astype(np.float32)

    profile = src.profile.copy()
    profile.update(
        width=new_width,
        height=new_height,
        transform=new_transform,
        dtype="float32",
        nodata=0,
        compress="deflate",
        tiled=True,
        blockxsize=512,
        blockysize=512,
        BIGTIFF="IF_SAFER",
    )

with rasterio.open(DST, "w", **profile) as dst:
    dst.write(logd, 1)

nz = logd[logd > 0]
print(f"Wrote {DST}")
print(f"Non-zero pixels: {nz.size:,} ({nz.size/logd.size:.1%})")
if nz.size:
    print(
        f"log-density stats: min={nz.min():.2f} mean={nz.mean():.2f} "
        f"p50={np.percentile(nz,50):.2f} p85={np.percentile(nz,85):.2f} "
        f"p90={np.percentile(nz,90):.2f} p95={np.percentile(nz,95):.2f} max={nz.max():.2f}"
    )
