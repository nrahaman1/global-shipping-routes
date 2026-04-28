"""Flask app: cascading origin/destination port selector that returns the
least-cost density path between any two World Port Index ports.

Run:
    pip install -r requirements.txt
    python app.py          # http://127.0.0.1:5000
"""
from pathlib import Path
import base64
import io
import threading
import numpy as np
import rasterio
from rasterio.transform import xy
import geopandas as gpd
from shapely.geometry import LineString, mapping
from skimage.graph import MCP_Geometric
from PIL import Image
from flask import Flask, jsonify, request, render_template

ROOT = Path(__file__).resolve().parent
DENSITY_TIF = ROOT / "work" / "density_log_2km.tif"
PORTS_GPKG = ROOT / "world_port_index_25th.gpkg"
STATIC_DIR = ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)
OVERLAY_PNG = STATIC_DIR / "density_overlay.png"

app = Flask(__name__)

# -------------------------- Startup load --------------------------
print("Loading density raster...")
with rasterio.open(DENSITY_TIF) as src:
    DENSITY = src.read(1).astype(np.float32)
    TRANSFORM = src.transform
    INV_TRANSFORM = ~src.transform
    H, W = DENSITY.shape
    BOUNDS = src.bounds

MAX_D = float(DENSITY.max())
COST = np.where(DENSITY > 0, (MAX_D + 1.0) - DENSITY, np.inf).astype(np.float32)
print(f"  shape={DENSITY.shape} nonzero={np.count_nonzero(DENSITY):,}")

print("Loading ports...")
PORTS = gpd.read_file(PORTS_GPKG).to_crs("EPSG:4326")
PORTS = PORTS[PORTS.geometry.notna()].reset_index(drop=True)
PORTS["lon"] = PORTS.geometry.x
PORTS["lat"] = PORTS.geometry.y
print(f"  {len(PORTS):,} ports, {PORTS['COUNTRY'].nunique()} countries")


def _snap(lon: float, lat: float, win: int = 25):
    """Snap a (lon, lat) to the nearest non-zero density pixel within ``win``
    pixels. Returns (row, col) or None."""
    c, r = INV_TRANSFORM * (lon, lat)
    r = int(np.clip(r, 0, H - 1))
    c = int(np.clip(c, 0, W - 1))
    if DENSITY[r, c] > 0:
        return r, c
    rlo, rhi = max(0, r - win), min(H, r + win + 1)
    clo, chi = max(0, c - win), min(W, c + win + 1)
    patch = DENSITY[rlo:rhi, clo:chi]
    nz = np.argwhere(patch > 0)
    if nz.size == 0:
        return None
    dr = nz[:, 0] + rlo - r
    dc = nz[:, 1] + clo - c
    j = int(np.argmin(dr * dr + dc * dc))
    return int(nz[j, 0] + rlo), int(nz[j, 1] + clo)


print("Snapping ports to density pixels...")
snapped = [_snap(lon, lat) for lon, lat in zip(PORTS["lon"], PORTS["lat"])]
PORTS["row"] = [s[0] if s else -1 for s in snapped]
PORTS["col"] = [s[1] if s else -1 for s in snapped]
PORTS["reachable"] = [s is not None for s in snapped]
print(f"  reachable: {PORTS['reachable'].sum():,}/{len(PORTS):,}")


def _make_density_overlay():
    """Downsample the density raster to an RGBA PNG used as a low-opacity
    Leaflet ImageOverlay. Transparent where density == 0."""
    if OVERLAY_PNG.exists():
        print(f"  using existing overlay {OVERLAY_PNG.name}")
        return
    print("Generating density overlay PNG...")
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from PIL import Image

    DS = 4
    h, w = H // DS, W // DS
    block = DENSITY[:h * DS, :w * DS].reshape(h, DS, w, DS).max(axis=(1, 3))
    cmap = LinearSegmentedColormap.from_list(
        "dens",
        ["#0f172a", "#1f3558", "#4b74a6", "#b9d1e5", "#e6eef7"],
        N=256,
    )
    norm = Normalize(vmin=0, vmax=18)
    rgba = (cmap(norm(block)) * 255).astype(np.uint8)
    rgba[block == 0, 3] = 0  # transparent on land / empty ocean
    Image.fromarray(rgba, "RGBA").save(OVERLAY_PNG)
    print(f"  wrote {OVERLAY_PNG}  ({rgba.shape})")


_make_density_overlay()

# -------------------------- MCP cache --------------------------
# Each (cum_costs, predecessor) pair from MCP_Geometric uses ~1.2 GB on
# this 8.5k x 18k raster. Keep a small LRU so the 2nd query for the same
# origin is instant.
_mcp_cache: "dict[tuple[int, int], tuple]" = {}
_mcp_lock = threading.Lock()
_CACHE_MAX = 2


def _get_mcp_from_origin(r: int, c: int):
    key = (int(r), int(c))
    with _mcp_lock:
        if key in _mcp_cache:
            _mcp_cache[key] = _mcp_cache.pop(key)  # mark most-recent
            return _mcp_cache[key]
    print(f"MCP: single-source from pixel {key}...")
    mcp = MCP_Geometric(COST, fully_connected=True)
    cum, _ = mcp.find_costs([key])
    with _mcp_lock:
        _mcp_cache[key] = (mcp, cum)
        while len(_mcp_cache) > _CACHE_MAX:
            oldest = next(iter(_mcp_cache))
            del _mcp_cache[oldest]
    print("  done")
    return mcp, cum


def _frontier_snapshots(cum, n_frames: int = 30, ds: int = 16):
    """Render N RGBA PNG snapshots of the Dijkstra frontier growing from the
    origin. ``cum`` is the MCP cumulative-cost raster: pixel value = arrival
    time from the source, ``inf`` where unreachable. Downsample with block-min
    (earliest arrival per block), normalize to [0,1], then threshold at k/N
    for each frame. Returns (frames_b64, [miny, minx, maxy, maxx])."""
    cum_fin = np.where(np.isfinite(cum), cum, np.inf)
    hh = (cum.shape[0] // ds) * ds
    ww = (cum.shape[1] // ds) * ds
    block = cum_fin[:hh, :ww].reshape(hh // ds, ds, ww // ds, ds).min(axis=(1, 3))
    finite = np.isfinite(block)
    if not finite.any():
        return [], None
    vmax = float(block[finite].max()) or 1.0
    t = np.where(finite, block / vmax, np.inf)
    snapshots = []
    for k in range(1, n_frames + 1):
        threshold = k / n_frames
        visited = t <= threshold
        alpha = np.where(visited, 110, 0).astype(np.uint8)
        frontier = visited & (t > threshold - (1.0 / n_frames))
        alpha = np.where(frontier, 220, alpha).astype(np.uint8)
        rgba = np.zeros((*t.shape, 4), dtype=np.uint8)
        rgba[..., 0] = 245
        rgba[..., 1] = 158
        rgba[..., 2] = 11
        rgba[..., 3] = alpha
        img = Image.fromarray(rgba, "RGBA")
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        snapshots.append(base64.b64encode(buf.getvalue()).decode("ascii"))
    minx, maxy = TRANSFORM * (0, 0)
    maxx, miny = TRANSFORM * (ww, hh)
    return snapshots, [float(miny), float(minx), float(maxy), float(maxx)]


# -------------------------- Routes --------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/countries")
def api_countries():
    counts = (PORTS[PORTS["reachable"]]
              .groupby("COUNTRY").size().sort_index())
    return jsonify([{"code": str(k), "count": int(v)} for k, v in counts.items()])


@app.route("/api/ports")
def api_ports():
    country = request.args.get("country", "")
    sel = PORTS[(PORTS["COUNTRY"].astype(str) == country) & PORTS["reachable"]]
    rows = sel.sort_values("PORT_NAME")
    return jsonify([
        {"id": int(i), "name": str(r["PORT_NAME"]),
         "lat": float(r["lat"]), "lon": float(r["lon"])}
        for i, r in rows.iterrows()
    ])


@app.route("/api/route", methods=["POST"])
def api_route():
    data = request.get_json(force=True)
    try:
        oid = int(data["origin_id"])
        did = int(data["dest_id"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "origin_id and dest_id required."}), 400
    if oid == did:
        return jsonify({"error": "Origin and destination are the same port."}), 400
    orig = PORTS.iloc[oid]
    dest = PORTS.iloc[did]
    if not (orig["reachable"] and dest["reachable"]):
        return jsonify({"error": "One of the ports has no density-connected pixel."}), 400
    o_r, o_c = int(orig["row"]), int(orig["col"])
    d_r, d_c = int(dest["row"]), int(dest["col"])
    mcp, cum = _get_mcp_from_origin(o_r, o_c)
    c_dest = cum[d_r, d_c]
    if not np.isfinite(c_dest):
        return jsonify({"error": "No density-connected path between these ports."}), 404
    try:
        path = mcp.traceback((d_r, d_c))
    except ValueError:
        return jsonify({"error": "Traceback failed."}), 500
    if len(path) < 2:
        return jsonify({"error": "Degenerate path."}), 500
    path_arr = np.asarray(path)
    xs, ys = xy(TRANSFORM, path_arr[:, 0], path_arr[:, 1], offset="center")
    line = LineString(list(zip(xs, ys)))
    length_km = float(
        gpd.GeoSeries([line], crs=4326).to_crs(6933).length.iloc[0] / 1000.0
    )
    return jsonify({
        "type": "Feature",
        "geometry": mapping(line),
        "properties": {
            "origin": str(orig["PORT_NAME"]),
            "origin_country": str(orig["COUNTRY"]),
            "destination": str(dest["PORT_NAME"]),
            "destination_country": str(dest["COUNTRY"]),
            "origin_latlon": [float(orig["lat"]), float(orig["lon"])],
            "destination_latlon": [float(dest["lat"]), float(dest["lon"])],
            "cost": float(c_dest),
            "length_km": length_km,
        },
    })


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
