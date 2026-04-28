# Global Shipping Route Finder

Interactive web app that picks a **least-cost maritime route** between any two
World Port Index ports, following the World Bank / IMF global shipping-traffic
density raster. The algorithm hugs the brightest lanes on the AIS density
surface, so the paths are plausible real-world shipping routes — not
great-circles.

![screenshot placeholder](outputs/map_3_network.gif)

## Quick start

```bash
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5000
```

On first load the app reads the 2-km density raster (~600 MB in memory), snaps
every WPI port to its nearest non-zero density pixel, and pre-renders a PNG
overlay. The UI is a standard Leaflet map with a sidebar: **origin country →
origin port → destination country → destination port → Compute route**.

The first route request for a *new* origin port takes **30–60 s** while a
single-source `MCP_Geometric` (Dijkstra with diagonal connectivity) explores
the raster from that port. Subsequent requests from the same origin are
instant — the MCP result is cached (LRU, max 2 origins ≈ 2.5 GB RAM).

## Required data (not checked in)

Place these files under the project root:

```
work/density_log_2km.tif          # produced by scripts/01_preprocess.py
world_port_index_25th.gpkg        # World Port Index (NGA)
```

`density_log_2km.tif` is the 2-km log-density raster generated from the World
Bank / IMF `shipdensity_global.tif` — see `scripts/01_preprocess.py`. The
original source raster is too large for GitHub; download it from
[World Bank Open Data](https://datacatalog.worldbank.org/search/dataset/0037580).

## Project layout

```
app.py                      # Flask server (routing API + MCP cache)
templates/index.html        # Leaflet UI
static/style.css            # Formal dark palette
static/app.js               # Front-end logic
static/density_overlay.png  # Generated on first run (RGBA)
scripts/
  01_preprocess.py          # Full raster -> 2-km log-density GeoTIFF
  02_route_to_us.py         # Batch top-K routing, writes GeoPackage
  03_visualize.py           # Static maps + animated GIFs
outputs/
  global_shipping_network.gpkg
  map_1_raster.png          # Raster visual
  map_2_ports.png           # Ports visual
  map_3_network.gif         # Animated network build-up
requirements.txt
```

## Notes

- **Memory**: ~2 GB resident while idle, ~3.5 GB with two cached origin MCPs.
- **Accuracy**: routes are constrained to the density raster, so very-low-
  traffic legs (e.g. Arctic, remote islands) may fail with
  `No density-connected path`. Increase the 2-km raster coverage by lowering
  the downsample factor in `scripts/01_preprocess.py` if you need finer lanes.
- **Antimeridian**: Leaflet's `worldCopyJump` is enabled; route polylines use
  plain lon/lat and may appear as a single span across the dateline. A split
  at `|Δlon| > 180°` can be added client-side if desired.

## Data & references

- World Port Index (WPI), NGA — <https://msi.nga.mil/Publications/WPI>
- Global Shipping Traffic Density, World Bank / IMF —
  <https://datacatalog.worldbank.org/search/dataset/0037580>
- `skimage.graph.MCP_Geometric` for multi-source / single-source Dijkstra on
  anisotropic cost rasters.
