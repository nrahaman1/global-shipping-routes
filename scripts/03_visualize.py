"""Produce three visualizations:

  outputs/map_1_raster.png     global ship-density raster on dark background
  outputs/map_2_ports.png      raster + WPI ports (US = cyan, foreign = magenta)
  outputs/map_3_network.gif    animated build-up of routes over dim raster

Styling: dark background, neon colors, no decorations that distract from the
data.
"""
from pathlib import Path
import argparse
import numpy as np
import rasterio
import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.collections import LineCollection
import imageio.v2 as imageio

ROOT = Path(__file__).resolve().parent.parent
DENSITY_TIF = ROOT / "work" / "density_log_2km.tif"
PORTS_GPKG = ROOT / "world_port_index_25th.gpkg"
ROUTES_GPKG = ROOT / "outputs" / "global_shipping_network.gpkg"
OUT_DIR = ROOT / "outputs"

# Formal, restrained palette
BG = "#0e1117"                 # near-black, slight blue cast
FG_TEXT = "#e6e6e6"
US_PORT = "#ff6e40"            # warm coral — high contrast against blue raster
FOREIGN_PORT = "#26c6da"       # teal — clearly distinct from US ports
ROUTE_DIM = "#b39ddb"          # soft lavender, readable on dim raster
ROUTE_BRIGHT = "#ffd54f"       # bright gold — leading edge
TIP_DOT = "#ffffff"             # white dot at the advancing head

# Single-hue density colormap: dark navy -> light steel — formal, legible
DENSITY_CMAP = LinearSegmentedColormap.from_list(
    "mono_density",
    ["#0e1117", "#16233a", "#1f3558", "#2f4e7c", "#4b74a6", "#7fa6cc",
     "#b9d1e5", "#e6eef7"],
    N=256,
)


def load_density(downsample=3, bbox=None):
    """Load the density raster.

    If ``bbox`` = (lon_min, lon_max, lat_min, lat_max) is given, only the
    window covering that bounding box is read (at the requested downsample).
    """
    with rasterio.open(DENSITY_TIF) as src:
        if bbox is None:
            arr = src.read(1, out_shape=(src.height // downsample,
                                          src.width // downsample))
            bounds = src.bounds
        else:
            from rasterio.windows import from_bounds
            lon_min, lon_max, lat_min, lat_max = bbox
            window = from_bounds(lon_min, lat_min, lon_max, lat_max,
                                 transform=src.transform)
            row_off = max(0, int(window.row_off))
            col_off = max(0, int(window.col_off))
            h = min(src.height - row_off, int(window.height))
            w = min(src.width - col_off, int(window.width))
            from rasterio.windows import Window
            window = Window(col_off, row_off, w, h)
            out_h = max(1, h // downsample)
            out_w = max(1, w // downsample)
            arr = src.read(1, window=window, out_shape=(out_h, out_w))
            win_transform = src.window_transform(window)
            left = win_transform.c
            top = win_transform.f
            right = left + w * src.transform.a
            bottom = top + h * src.transform.e
            class _B: pass
            bounds = _B()
            bounds.left, bounds.right = left, right
            bounds.bottom, bounds.top = bottom, top
    arr = np.clip(arr, 0, 18)
    extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
    return arr, extent


def setup_figure(extent, figsize=(16, 8)):
    fig, ax = plt.subplots(figsize=figsize, facecolor=BG)
    ax.set_facecolor(BG)
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    return fig, ax


def add_title(ax, text, color="white"):
    ax.text(0.5, 0.97, text, transform=ax.transAxes, ha="center", va="top",
            color=color, fontsize=14, fontweight="bold",
            path_effects=[])


def map_1_raster(arr, extent):
    fig, ax = setup_figure(extent)
    ax.imshow(arr, extent=extent, origin="upper", cmap=DENSITY_CMAP,
              norm=Normalize(vmin=0, vmax=18), interpolation="nearest")
    add_title(ax, "Global Ship Density (log AIS hours)  —  World Bank / IMF")
    out = OUT_DIR / "map_1_raster.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"wrote {out}")


def map_2_ports(arr, extent):
    ports = gpd.read_file(PORTS_GPKG).to_crs("EPSG:4326")
    ports = ports[ports.geometry.notna()]
    us = ports[ports["COUNTRY"].astype(str) == "US"]
    foreign = ports[ports["COUNTRY"].astype(str) != "US"]

    fig, ax = setup_figure(extent)
    ax.imshow(arr, extent=extent, origin="upper", cmap=DENSITY_CMAP,
              norm=Normalize(vmin=0, vmax=18), interpolation="nearest",
              alpha=0.85)
    ax.scatter(foreign.geometry.x, foreign.geometry.y, s=4,
               c=FOREIGN_PORT, edgecolors="none", alpha=0.85,
               label=f"Non-US ports ({len(foreign):,})")
    ax.scatter(us.geometry.x, us.geometry.y, s=10,
               c=US_PORT, edgecolors="none", alpha=1.0,
               label=f"US ports ({len(us):,})")
    leg = ax.legend(loc="lower left", frameon=True, facecolor=BG,
                    edgecolor="#3a4452", labelcolor="white", fontsize=10)
    for text in leg.get_texts():
        text.set_color("white")
    add_title(ax, "World Port Index (US vs. Non-US)")
    out = OUT_DIR / "map_2_ports.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"wrote {out}")


def split_on_antimeridian(coords):
    """Break a coord list wherever two consecutive points jump >180° in
    longitude; return a list of sub-lines. Keeps GIF lines from painting
    a straight bar across the whole map."""
    if len(coords) < 2:
        return []
    segs = []
    cur = [coords[0]]
    for i in range(1, len(coords)):
        lon_prev = cur[-1][0]
        lon_cur = coords[i][0]
        if abs(lon_cur - lon_prev) > 180:
            if len(cur) >= 2:
                segs.append(cur)
            cur = [coords[i]]
        else:
            cur.append(coords[i])
    if len(cur) >= 2:
        segs.append(cur)
    return segs


def _split_polyline_by_fraction(coords, fraction):
    """Return the first ``fraction`` of a polyline (by cumulative Euclidean
    length in lon/lat). Used to grow each route from its foreign-port start
    toward the US-port end over several animation frames."""
    if fraction >= 1.0:
        return coords
    if fraction <= 0.0 or len(coords) < 2:
        return coords[:1]
    pts = np.asarray(coords)
    # cumulative distance with antimeridian-safe skip
    seg = np.diff(pts, axis=0)
    dlon = seg[:, 0]
    # if a single segment spans > 180° treat as zero-length (dateline jump)
    dlon[np.abs(dlon) > 180] = 0
    seg_len = np.sqrt(dlon ** 2 + seg[:, 1] ** 2)
    cum = np.concatenate([[0], np.cumsum(seg_len)])
    total = cum[-1]
    if total == 0:
        return coords[:1]
    target = fraction * total
    idx = int(np.searchsorted(cum, target))
    if idx <= 0:
        return coords[:1]
    if idx >= len(coords):
        return coords
    # interpolate the final partial segment
    t = (target - cum[idx - 1]) / max(seg_len[idx - 1], 1e-12)
    px = coords[idx - 1][0] + t * (coords[idx][0] - coords[idx - 1][0])
    py = coords[idx - 1][1] + t * (coords[idx][1] - coords[idx - 1][1])
    return list(coords[:idx]) + [(px, py)]


def _coords_to_plain_segments(coords):
    """Break a polyline into 2-point sub-segments, skipping dateline jumps.
    Returns a flat list of segments (no per-segment colors — the collection
    uses a single color)."""
    if len(coords) < 2:
        return []
    chunks = split_on_antimeridian(coords)
    segs = []
    for chunk in chunks:
        for i in range(len(chunk) - 1):
            segs.append([chunk[i], chunk[i + 1]])
    return segs


def map_3_gif(arr, extent, n_frames=45, fps=10, max_routes=None,
              growth_frames=4, arrowhead_size=6, bbox=None,
              out_name="map_3_network.gif",
              title="Shipping Routes to U.S. Ports  —  least-cost paths "
                    "over global AIS density"):
    ports = gpd.read_file(PORTS_GPKG).to_crs("EPSG:4326")
    ports = ports[ports.geometry.notna()]
    if bbox is not None:
        lon_min, lon_max, lat_min, lat_max = bbox
        ports = ports.cx[lon_min:lon_max, lat_min:lat_max]
    us = ports[ports["COUNTRY"].astype(str) == "US"]
    foreign = ports[ports["COUNTRY"].astype(str) != "US"]

    routes = gpd.read_file(ROUTES_GPKG, layer="routes")
    if bbox is not None:
        # Keep routes whose bounding box intersects the zoom window
        lon_min, lon_max, lat_min, lat_max = bbox
        routes = routes.cx[lon_min:lon_max, lat_min:lat_max].reset_index(drop=True)
        print(f"  zoom: kept {len(routes):,} routes intersecting bbox")
    # Shuffle with a fixed seed so the build-up is distributed from frame 1.
    routes = routes.sample(frac=1.0, random_state=42).reset_index(drop=True)
    if max_routes:
        routes = routes.head(max_routes)
    print(f"GIF: animating {len(routes):,} routes in {n_frames} frames "
          f"@ {fps} fps (growth {growth_frames} frames per route)")

    # Coords run foreign -> US. Animation grows each line from its foreign
    # end toward its US end; a bright tip dot rides the leading edge.
    all_coords = [list(geom.coords) for geom in routes.geometry]

    fig, ax = setup_figure(extent, figsize=(12, 6))
    fig.set_dpi(90)
    ax.imshow(arr, extent=extent, origin="upper", cmap=DENSITY_CMAP,
              norm=Normalize(vmin=0, vmax=18), interpolation="nearest",
              alpha=0.5)
    # Static ports at 50% alpha
    ax.scatter(foreign.geometry.x, foreign.geometry.y, s=4,
               c=FOREIGN_PORT, edgecolors="none", alpha=0.5, zorder=2)
    ax.scatter(us.geometry.x, us.geometry.y, s=14,
               c=US_PORT, edgecolors="none", alpha=0.5, zorder=6)
    title_txt = ax.text(0.5, 0.965, "", transform=ax.transAxes,
                         ha="center", va="top", color=FG_TEXT, fontsize=13,
                         fontweight="bold")
    counter_txt = ax.text(0.99, 0.02, "", transform=ax.transAxes,
                           ha="right", va="bottom",
                           color=FG_TEXT, fontsize=10)
    dir_txt = ax.text(0.01, 0.02,
                       "routes advancing from foreign ports \u2192 U.S. ports",
                       transform=ax.transAxes,
                       ha="left", va="bottom",
                       color=FG_TEXT, fontsize=9, alpha=0.85)

    # lc_done   = completed routes, thin dim steel-blue
    # lc_grow   = routes currently growing, brighter amber (leading edge)
    # tip_scat  = moving amber dot at the advancing head of each growing line
    lc_done = LineCollection([], linewidths=0.5, alpha=0.55,
                              colors=ROUTE_DIM,
                              zorder=4, capstyle="round")
    lc_grow = LineCollection([], linewidths=1.2, alpha=0.95,
                              colors=ROUTE_BRIGHT,
                              zorder=5, capstyle="round")
    ax.add_collection(lc_done)
    ax.add_collection(lc_grow)
    tip_scat = ax.scatter([], [], s=10, c=TIP_DOT, edgecolors="none",
                           alpha=1.0, zorder=7)

    print("  precomputing route segments...")
    full_segs = [_coords_to_plain_segments(c) for c in all_coords]

    per_frame = max(1, int(np.ceil(len(routes) / (n_frames - growth_frames))))
    release_frame = (np.arange(len(routes)) // per_frame).astype(int)

    done_segs = []
    frames = []
    for f in range(n_frames):
        grow_segs = []
        tip_xy = []
        just_finished_mask = (release_frame + growth_frames - 1 == f - 1)
        for r_idx in np.where(just_finished_mask)[0]:
            done_segs.extend(full_segs[r_idx])
        currently_growing = np.where(
            (release_frame <= f) & (release_frame + growth_frames > f)
        )[0]
        for r_idx in currently_growing:
            sub_step = f - release_frame[r_idx] + 1
            fraction = sub_step / growth_frames
            partial = _split_polyline_by_fraction(all_coords[r_idx], fraction)
            grow_segs.extend(_coords_to_plain_segments(partial))
            if len(partial) >= 1:
                tip_xy.append(partial[-1])

        lc_done.set_segments(done_segs)
        lc_grow.set_segments(grow_segs)
        tip_scat.set_offsets(np.asarray(tip_xy) if tip_xy
                             else np.empty((0, 2)))

        n_drawn = int(np.sum(release_frame + growth_frames - 1 <= f))
        n_drawn = min(n_drawn, len(routes))
        title_txt.set_text(title)
        counter_txt.set_text(f"{n_drawn:,} / {len(routes):,} routes")

        fig.canvas.draw()
        img = np.frombuffer(fig.canvas.buffer_rgba(), dtype=np.uint8)
        img = img.reshape(fig.canvas.get_width_height()[::-1] + (4,))[..., :3]
        frames.append(img.copy())
        if (f + 1) % 10 == 0 or f == n_frames - 1:
            print(f"  frame {f+1}/{n_frames}  drawn={n_drawn:,}")

    # Hold the final frame for 2 s
    frames.extend([frames[-1]] * int(fps * 2))

    out = OUT_DIR / out_name
    imageio.mimsave(out, frames, duration=1.0 / fps, loop=0)
    plt.close(fig)
    print(f"wrote {out}  ({len(frames)} frames)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--downsample", type=int, default=4,
                    help="Raster downsample factor for display")
    ap.add_argument("--n-frames", type=int, default=45)
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--max-routes", type=int, default=None)
    ap.add_argument("--skip-maps", action="store_true",
                    help="Skip maps 1 & 2 (already written) and only make GIF")
    ap.add_argument("--bbox", type=str, default=None,
                    help="Zoom bbox 'lon_min,lon_max,lat_min,lat_max' — when "
                         "set, only a zoom GIF is produced (map_4_zoom.gif)")
    ap.add_argument("--zoom-name", type=str, default="map_4_zoom.gif",
                    help="Output filename for zoom GIF")
    ap.add_argument("--zoom-title", type=str,
                    default="Shipping-density raster \u2192 least-cost network"
                            "  (zoom)")
    args = ap.parse_args()

    if args.bbox:
        bbox = tuple(float(x) for x in args.bbox.split(","))
        assert len(bbox) == 4, "bbox needs 4 numbers"
        print(f"Loading density raster (zoom bbox={bbox})...")
        # Use a finer downsample for zoom so ridges are crisp
        arr, extent = load_density(max(1, args.downsample // 2), bbox=bbox)
        print(f"  display shape {arr.shape}  extent={extent}")
        print("\nZoom GIF")
        map_3_gif(arr, extent, n_frames=args.n_frames, fps=args.fps,
                  max_routes=args.max_routes, bbox=bbox,
                  out_name=args.zoom_name, title=args.zoom_title)
    else:
        print("Loading density raster...")
        arr, extent = load_density(args.downsample)
        print(f"  display shape {arr.shape}")

        if not args.skip_maps:
            print("\n[1/3] Map 1 — raster")
            map_1_raster(arr, extent)
            print("\n[2/3] Map 2 — ports")
            map_2_ports(arr, extent)
        print("\n[3/3] Map 3 — GIF")
        map_3_gif(arr, extent, n_frames=args.n_frames, fps=args.fps,
                  max_routes=args.max_routes)
