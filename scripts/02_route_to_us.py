"""Least-cost routing on the ship-density raster: for every non-US WPI port
find the shortest path (following density) to the nearest US port.

Cost model
    cost_per_pixel = (max_log_density + 1) - log_density   (nonzero pixels)
                   = +inf                                    (zero pixels)

so high-density pixels are cheap and empty ocean is impassable. The
``skimage.graph.MCP_Geometric`` solver then yields geodesic-like shortest
paths that hug the brightest lanes.

Sources = all US ports (multi-source Dijkstra).
Targets = all non-US ports; each is traced back to its cheapest US port.
Ports whose snapped pixel is unreachable (no density path exists) are
skipped entirely.

Output: ``outputs/global_shipping_network.gpkg``, layer ``routes``
    geometry   LineString in EPSG:4326 from foreign port to its US endpoint
    from_port  origin port name
    from_ctry  origin country
    to_port    US destination name
    cost       accumulated path cost (low = strong density route)
    length_km  geodesic length
"""
from pathlib import Path
import argparse
import numpy as np
import rasterio
from rasterio.transform import xy, rowcol
import geopandas as gpd
from shapely.geometry import LineString, Point
from skimage.graph import MCP_Geometric
from scipy.ndimage import distance_transform_edt

ROOT = Path(__file__).resolve().parent.parent
DENSITY_TIF = ROOT / "work" / "density_log_2km.tif"
PORTS_GPKG = ROOT / "world_port_index_25th.gpkg"
OUT_GPKG = ROOT / "outputs" / "global_shipping_network.gpkg"
OUT_GPKG.parent.mkdir(parents=True, exist_ok=True)


def snap_ports_to_density(ports_xy: np.ndarray, density: np.ndarray,
                          inv_transform, max_snap_km: float = 50.0,
                          px_km: float = 2.0):
    """Snap each (lon, lat) port to the nearest nonzero-density pixel using a
    local window search. Returns (rows, cols, ok_mask).

    ``ok_mask[i]`` is False if no nonzero pixel is found within ``max_snap_km``.
    """
    H, W = density.shape
    xs = ports_xy[:, 0]
    ys = ports_xy[:, 1]
    cols, rows = inv_transform * (xs, ys)
    rows = rows.astype(np.int64)
    cols = cols.astype(np.int64)
    # clip to bounds
    rows = np.clip(rows, 0, H - 1)
    cols = np.clip(cols, 0, W - 1)

    win = int(np.ceil(max_snap_km / px_km))
    out_rows = np.empty(len(ports_xy), dtype=np.int64)
    out_cols = np.empty(len(ports_xy), dtype=np.int64)
    ok = np.zeros(len(ports_xy), dtype=bool)

    for i in range(len(ports_xy)):
        r0, c0 = rows[i], cols[i]
        # Direct hit?
        if density[r0, c0] > 0:
            out_rows[i] = r0
            out_cols[i] = c0
            ok[i] = True
            continue
        rlo, rhi = max(0, r0 - win), min(H, r0 + win + 1)
        clo, chi = max(0, c0 - win), min(W, c0 + win + 1)
        patch = density[rlo:rhi, clo:chi]
        if not (patch > 0).any():
            continue
        # nearest nonzero by Euclidean pixel distance
        pr, pc = np.where(patch > 0)
        dr = pr + rlo - r0
        dc = pc + clo - c0
        d2 = dr * dr + dc * dc
        j = int(np.argmin(d2))
        out_rows[i] = pr[j] + rlo
        out_cols[i] = pc[j] + clo
        ok[i] = True
    return out_rows, out_cols, ok


def main(max_snap_km: float, max_cost_ratio: float, topk: int = 5):
    print("Reading density raster...")
    with rasterio.open(DENSITY_TIF) as src:
        density = src.read(1).astype(np.float32)
        transform = src.transform
        crs = src.crs
    print(f"  {density.shape}  nonzero={np.count_nonzero(density):,}")

    # Cost raster: cheap on bright lanes, +inf elsewhere
    max_d = float(density.max())
    cost = np.where(density > 0, (max_d + 1.0) - density, np.inf).astype(np.float32)
    print(f"  cost: min={cost[np.isfinite(cost)].min():.3f} "
          f"max_finite={cost[np.isfinite(cost)].max():.3f} "
          f"impassable={np.isinf(cost).sum():,} pixels")

    print("Reading ports...")
    ports = gpd.read_file(PORTS_GPKG).to_crs("EPSG:4326")
    ports = ports[ports.geometry.notna()].reset_index(drop=True)
    ports["lon"] = ports.geometry.x
    ports["lat"] = ports.geometry.y
    us_mask = ports["COUNTRY"].astype(str) == "US"
    print(f"  total {len(ports):,} ports ({us_mask.sum():,} US, "
          f"{(~us_mask).sum():,} non-US)")

    inv = ~transform
    xy_arr = ports[["lon", "lat"]].values.astype(np.float64)

    print(f"Snapping ports to density (within {max_snap_km} km)...")
    rows, cols, ok = snap_ports_to_density(xy_arr, density, inv, max_snap_km)
    print(f"  snapped OK: {ok.sum():,}/{len(ports):,}")

    ports["row"] = rows
    ports["col"] = cols
    ports["snap_ok"] = ok

    us_ports = ports[us_mask & ports["snap_ok"]].reset_index(drop=True)
    foreign_ports = ports[(~us_mask) & ports["snap_ok"]].reset_index(drop=True)
    print(f"  usable US ports: {len(us_ports):,}")
    print(f"  usable foreign ports: {len(foreign_ports):,}")

    # Collapse duplicate US pixel starts (many ports share pixels)
    starts = list({(int(r), int(c)) for r, c in
                   zip(us_ports["row"], us_ports["col"])})
    print(f"  unique US start pixels: {len(starts):,}")

    # US port lookup for labeling: map (r,c) -> (port_name)
    # multiple US ports may share a pixel; take the first
    us_pixel_to_name = {}
    for _, row in us_ports.iterrows():
        key = (int(row["row"]), int(row["col"]))
        us_pixel_to_name.setdefault(key, row["PORT_NAME"])

    # --------------------------------------------------------------
    # Iterative top-K forward passes. In pass k, sources = US pixels
    # that have NOT yet been chosen by any foreign port in earlier
    # passes. Each foreign port accumulates up to K distinct US
    # destinations, cheapest first.
    # --------------------------------------------------------------
    rows_out = []
    remaining_us = set(starts)
    fp_costs_all = None
    for k in range(1, topk + 1):
        if not remaining_us:
            print(f"  no US sources remain; stopping after pass {k - 1}")
            break
        print(f"\nPass {k}/{topk}: MCP from {len(remaining_us):,} US pixels...")
        mcp_k = MCP_Geometric(cost, fully_connected=True)
        cum_k, _ = mcp_k.find_costs(list(remaining_us))
        print(f"  finite={np.isfinite(cum_k).mean():.1%}")

        fp_costs = np.array([cum_k[int(r), int(c)] for r, c in
                             zip(foreign_ports["row"], foreign_ports["col"])])
        finite = fp_costs[np.isfinite(fp_costs)]
        if finite.size == 0:
            print("  no foreign port reachable in this pass")
            break
        cap = float(np.median(finite)) * max_cost_ratio
        if k == 1:
            fp_costs_all = fp_costs
        print(f"  foreign-port cost: median={np.median(finite):,.0f}  "
              f"cap={cap:,.0f}  "
              f"unreachable={(~np.isfinite(fp_costs)).sum():,}")

        chosen_this_pass = set()
        added_this_pass = 0
        for i, row in foreign_ports.iterrows():
            pr, pc = int(row["row"]), int(row["col"])
            c = cum_k[pr, pc]
            if not np.isfinite(c) or c > cap:
                continue
            try:
                path = mcp_k.traceback((pr, pc))
            except ValueError:
                continue
            if len(path) < 2:
                continue
            path_arr = np.asarray(path)[::-1]   # foreign -> US
            xs, ys = xy(transform, path_arr[:, 0], path_arr[:, 1],
                        offset="center")
            line = LineString(list(zip(xs, ys)))
            end_key = (int(path_arr[-1, 0]), int(path_arr[-1, 1]))
            to_name = us_pixel_to_name.get(end_key, "")
            rows_out.append({
                "from_port": row["PORT_NAME"],
                "from_ctry": row["COUNTRY"],
                "to_port": to_name,
                "rank": k,
                "cost": float(c),
                "geometry": line,
            })
            chosen_this_pass.add(end_key)
            added_this_pass += 1
        remaining_us -= chosen_this_pass
        print(f"  pass {k}: added {added_this_pass:,} routes to "
              f"{len(chosen_this_pass):,} distinct US pixels; "
              f"{len(remaining_us):,} US pixels remain")

    fp_costs = fp_costs_all if fp_costs_all is not None else np.array([])
    cost_cap = float(np.median(fp_costs[np.isfinite(fp_costs)]) * max_cost_ratio) \
        if fp_costs.size else np.inf

    # --------------------------------------------------------------
    # Reverse pass: guarantee every reachable US port gets an inbound
    # route. Sources = foreign port pixels; traceback each US port to
    # its cheapest foreign origin. Many US ports would otherwise be
    # missed by the forward pass, which concentrates on a few "gateway"
    # US ports (e.g. Eastport ME, Massacre Bay AK).
    # --------------------------------------------------------------
    # US ports already covered by any forward pass — skip them in reverse
    covered_us_pixels = {(int(round((r["geometry"].coords[-1][1] - transform.f) / transform.e)),
                          int(round((r["geometry"].coords[-1][0] - transform.c) / transform.a)))
                         for r in rows_out}
    # simpler: just collect destination port names seen so far
    covered_us_names = {r["to_port"] for r in rows_out if r["to_port"]}
    print(f"\nForward passes total: {len(rows_out):,} routes to "
          f"{len(covered_us_names):,} distinct US ports")

    print("Reverse pass for still-uncovered US ports ...")
    foreign_starts = list({(int(r), int(c)) for r, c in
                           zip(foreign_ports["row"], foreign_ports["col"])})
    print(f"  unique foreign start pixels: {len(foreign_starts):,}")
    mcp_rev = MCP_Geometric(cost, fully_connected=True)
    cum_costs_rev, _ = mcp_rev.find_costs(foreign_starts)

    foreign_pixel_to = {}
    for _, row in foreign_ports.iterrows():
        key = (int(row["row"]), int(row["col"]))
        foreign_pixel_to.setdefault(key, (row["PORT_NAME"], row["COUNTRY"]))

    us_costs = np.array([cum_costs_rev[int(r), int(c)] for r, c in
                         zip(us_ports["row"], us_ports["col"])])
    finite_us = us_costs[np.isfinite(us_costs)]
    us_cost_cap = (float(np.median(finite_us)) * max_cost_ratio
                   if finite_us.size else np.inf)
    print(f"  US-port cost: median={np.median(finite_us):,.0f}  "
          f"cap={us_cost_cap:,.0f}  "
          f"unreachable={(~np.isfinite(us_costs)).sum():,}")

    added = 0
    skipped_us_unreach = 0
    skipped_already_covered = 0
    for i, row in us_ports.iterrows():
        if row["PORT_NAME"] in covered_us_names:
            skipped_already_covered += 1
            continue
        pr, pc = int(row["row"]), int(row["col"])
        c = cum_costs_rev[pr, pc]
        if not np.isfinite(c) or c > us_cost_cap:
            skipped_us_unreach += 1
            continue
        try:
            path = mcp_rev.traceback((pr, pc))
        except ValueError:
            skipped_us_unreach += 1
            continue
        if len(path) < 2:
            continue
        path_arr = np.asarray(path)
        xs, ys = xy(transform, path_arr[:, 0], path_arr[:, 1], offset="center")
        line = LineString(list(zip(xs, ys)))
        start_key = (int(path_arr[0, 0]), int(path_arr[0, 1]))
        from_name, from_ctry = foreign_pixel_to.get(start_key, ("", ""))
        rows_out.append({
            "from_port": from_name,
            "from_ctry": from_ctry,
            "to_port": row["PORT_NAME"],
            "rank": 0,
            "cost": float(c),
            "geometry": line,
        })
        added += 1
    print(f"  reverse-pass added {added:,} routes  "
          f"(skipped {skipped_already_covered:,} already-covered, "
          f"{skipped_us_unreach:,} unreachable US ports)")

    gdf = gpd.GeoDataFrame(rows_out, crs="EPSG:4326")
    gdf["length_km"] = gdf.to_crs(6933).length / 1000.0
    # drop dateline artefacts from plain lon/lat LineStrings
    before = len(gdf)
    gdf = gdf[gdf["length_km"] <= 25000].reset_index(drop=True)
    print(f"  dropped {before - len(gdf):,} dateline artefacts")

    if OUT_GPKG.exists():
        OUT_GPKG.unlink()
    gdf.to_file(OUT_GPKG, layer="routes", driver="GPKG")
    print(f"\nWrote {OUT_GPKG}")
    print(f"  routes: {len(gdf):,}")
    print(f"  length stats (km): "
          f"median={gdf['length_km'].median():,.0f} "
          f"p95={gdf['length_km'].quantile(0.95):,.0f} "
          f"max={gdf['length_km'].max():,.0f}")

    # Also save unreachable ports as a separate layer for diagnostics
    fp_with_cost = foreign_ports.copy()
    fp_with_cost["path_cost"] = fp_costs
    fp_with_cost["reachable"] = np.isfinite(fp_costs) & (fp_costs <= cost_cap)
    unreach = fp_with_cost[~fp_with_cost["reachable"]][
        ["PORT_NAME", "COUNTRY", "path_cost", "geometry"]
    ].rename(columns={"PORT_NAME": "port_name", "COUNTRY": "country"})
    unreach.to_file(OUT_GPKG, layer="unreachable_ports", driver="GPKG")
    print(f"  unreachable ports layer: {len(unreach):,}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-snap-km", type=float, default=50.0)
    ap.add_argument("--max-cost-ratio", type=float, default=5.0,
                    help="Skip routes whose cost exceeds median * this")
    ap.add_argument("--topk", type=int, default=5,
                    help="For each foreign port, keep up to K cheapest "
                         "distinct US destinations")
    args = ap.parse_args()
    main(args.max_snap_km, args.max_cost_ratio, args.topk)
