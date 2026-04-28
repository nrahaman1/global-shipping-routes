/* Global Shipping Route Finder — Leaflet front-end */
(function () {
  const $ = (id) => document.getElementById(id);

  const api = (path, opts) =>
    fetch(path, opts).then(async (r) => {
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw j;
      return j;
    });

  const escapeHtml = (s) =>
    String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

  const getCSS = (name) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // ---- Map ----
  const map = L.map("map", {
    center: [22, 0],
    zoom: 2,
    worldCopyJump: true,
    attributionControl: true,
    zoomControl: true,
  });

  L.tileLayer(
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    {
      maxZoom: 10,
      subdomains: "abcd",
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
    }
  ).addTo(map);

  // Layer groups: unselected country-level ports (small dots) vs.
  // the selected origin / destination (highlighted).
  const originCountryLayer = L.layerGroup().addTo(map);
  const destCountryLayer = L.layerGroup().addTo(map);

  const makeIcon = (kind, size) =>
    L.divIcon({
      className: "",
      html: `<div class="port-marker ${kind}" style="width:${size}px;height:${size}px;"></div>`,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    });

  const iconOriginSmall = makeIcon("origin small", 8);
  const iconOriginLarge = makeIcon("origin selected", 16);
  const iconDestSmall = makeIcon("destination small", 8);
  const iconDestLarge = makeIcon("destination selected", 16);

  let originMarker = null;
  let destMarker = null;
  let routeLayer = null;

  // ---- State helpers ----
  function setStatus(text, isErr = false) {
    const el = $("status");
    el.textContent = text || "";
    el.classList.toggle("err", !!isErr);
  }

  function updateComputeBtn() {
    const o = $("origin-port").value;
    const d = $("dest-port").value;
    $("compute").disabled = !(o && d && o !== d);
  }

  function clearPorts(id) {
    const sel = $(id);
    sel.innerHTML = '<option value="">—</option>';
    sel.disabled = true;
  }

  // ---- Country / port data loaders ----
  async function loadCountries() {
    setStatus("Loading countries…");
    try {
      const data = await api("/api/countries");
      const opts =
        '<option value="">—</option>' +
        data
          .map(
            (c) =>
              `<option value="${c.code}">${c.code} — ${c.count} port${
                c.count === 1 ? "" : "s"
              }</option>`
          )
          .join("");
      $("origin-country").innerHTML = opts;
      $("dest-country").innerHTML = opts;
      setStatus("");
    } catch (err) {
      setStatus("Failed to load countries.", true);
    }
  }

  /**
   * Load ports for a country, populate the <select>, and plot them on the
   * map as clickable/hoverable markers. side = "origin" | "dest".
   */
  async function loadPortsForSide(country, side) {
    const selId = side === "origin" ? "origin-port" : "dest-port";
    const layer = side === "origin" ? originCountryLayer : destCountryLayer;
    const smallIcon = side === "origin" ? iconOriginSmall : iconDestSmall;
    layer.clearLayers();
    clearPorts(selId);
    if (side === "origin" && originMarker) {
      map.removeLayer(originMarker);
      originMarker = null;
    }
    if (side === "dest" && destMarker) {
      map.removeLayer(destMarker);
      destMarker = null;
    }
    if (routeLayer) {
      map.removeLayer(routeLayer);
      routeLayer = null;
      $("result").innerHTML = "";
    }
    if (!country) {
      updateComputeBtn();
      return;
    }

    const sel = $(selId);
    sel.innerHTML = '<option value="">loading…</option>';
    sel.disabled = true;
    try {
      const data = await api("/api/ports?country=" + encodeURIComponent(country));
      sel.innerHTML =
        '<option value="">—</option>' +
        data
          .map(
            (p) =>
              `<option value="${p.id}" data-lat="${p.lat}" data-lon="${p.lon}">${escapeHtml(
                p.name
              )}</option>`
          )
          .join("");
      sel.disabled = false;

      // Plot all ports of the country on the map.
      const latlngs = [];
      data.forEach((p) => {
        const m = L.marker([p.lat, p.lon], {
          icon: smallIcon,
          riseOnHover: true,
          title: p.name,
        }).bindTooltip(p.name, {
          direction: "top",
          offset: [0, -6],
          opacity: 0.95,
        });
        m.on("click", () => {
          sel.value = String(p.id);
          sel.dispatchEvent(new Event("change"));
        });
        m.addTo(layer);
        latlngs.push([p.lat, p.lon]);
      });
      if (latlngs.length) {
        map.fitBounds(L.latLngBounds(latlngs), {
          padding: [60, 60],
          maxZoom: 6,
        });
      }
      setStatus(
        `${data.length} port${data.length === 1 ? "" : "s"} in ${country}. ` +
          "Click a marker or use the dropdown."
      );
    } catch (err) {
      clearPorts(selId);
      setStatus("Failed to load ports.", true);
    }
    updateComputeBtn();
  }

  /**
   * Move the highlighted marker for a side to the currently selected port.
   */
  function refreshSelectedMarker(side) {
    const selId = side === "origin" ? "origin-port" : "dest-port";
    const prev = side === "origin" ? originMarker : destMarker;
    const largeIcon = side === "origin" ? iconOriginLarge : iconDestLarge;
    if (prev) {
      map.removeLayer(prev);
      if (side === "origin") originMarker = null;
      else destMarker = null;
    }
    const sel = $(selId);
    const opt = sel.selectedOptions[0];
    if (!opt || !opt.dataset.lat) return;
    const lat = parseFloat(opt.dataset.lat);
    const lon = parseFloat(opt.dataset.lon);
    const m = L.marker([lat, lon], {
      icon: largeIcon,
      zIndexOffset: 1000,
      riseOnHover: true,
    }).bindTooltip(opt.textContent, {
      direction: "top",
      offset: [0, -10],
      permanent: false,
    });
    m.addTo(map);
    if (side === "origin") originMarker = m;
    else destMarker = m;
  }

  // ---- Great-circle interpolation (placeholder path while MCP computes) ----
  function greatCircle(a, b, n = 96) {
    const toRad = Math.PI / 180;
    const toDeg = 180 / Math.PI;
    const lat1 = a[0] * toRad, lon1 = a[1] * toRad;
    const lat2 = b[0] * toRad, lon2 = b[1] * toRad;
    const d =
      2 *
      Math.asin(
        Math.sqrt(
          Math.sin((lat2 - lat1) / 2) ** 2 +
            Math.cos(lat1) * Math.cos(lat2) * Math.sin((lon2 - lon1) / 2) ** 2
        )
      );
    if (d === 0) return [a, b];
    const pts = [];
    for (let i = 0; i <= n; i++) {
      const f = i / n;
      const A = Math.sin((1 - f) * d) / Math.sin(d);
      const B = Math.sin(f * d) / Math.sin(d);
      const x = A * Math.cos(lat1) * Math.cos(lon1) + B * Math.cos(lat2) * Math.cos(lon2);
      const y = A * Math.cos(lat1) * Math.sin(lon1) + B * Math.cos(lat2) * Math.sin(lon2);
      const z = A * Math.sin(lat1) + B * Math.sin(lat2);
      const lat = Math.atan2(z, Math.sqrt(x * x + y * y));
      const lon = Math.atan2(y, x);
      pts.push([lat * toDeg, lon * toDeg]);
    }
    return pts;
  }

  // Looping placeholder: a dashed great-circle redraws from origin to
  // destination every ~2.5 s while we wait for the server.
  function startPlaceholderRoute(originLL, destLL) {
    const coords = greatCircle(
      [originLL.lat, originLL.lng],
      [destLL.lat, destLL.lng]
    );
    const style = {
      color: getCSS("--muted") || "#94a3b8",
      weight: 2,
      opacity: 0.8,
      dashArray: "6 8",
      lineJoin: "round",
      lineCap: "round",
    };
    const poly = L.polyline([coords[0]], style).addTo(map);
    const n = coords.length;
    const duration = 2500;
    let start = performance.now();
    let raf = 0;
    let stopped = false;
    const tick = (now) => {
      if (stopped) return;
      const t = ((now - start) % duration) / duration;
      const idx = Math.max(1, Math.floor(t * (n - 1)));
      poly.setLatLngs(coords.slice(0, idx + 1));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return {
      stop() {
        stopped = true;
        cancelAnimationFrame(raf);
        if (map.hasLayer(poly)) map.removeLayer(poly);
      },
    };
  }

  // ---- Route animation: progressive polyline from origin to destination ----
  function animateRoute(coords, style, durationMs = 2200) {
    return new Promise((resolve) => {
      if (!coords || coords.length < 2) {
        resolve(null);
        return;
      }
      const poly = L.polyline([coords[0]], style).addTo(map);
      const headIcon = L.divIcon({
        className: "",
        html: `<div class="route-head"></div>`,
        iconSize: [14, 14],
        iconAnchor: [7, 7],
      });
      const head = L.marker(coords[0], {
        icon: headIcon,
        interactive: false,
        zIndexOffset: 1500,
      }).addTo(map);

      const n = coords.length;
      const start = performance.now();
      const tick = (now) => {
        const t = Math.min(1, (now - start) / durationMs);
        const idx = Math.max(1, Math.floor(t * (n - 1)));
        const slice = coords.slice(0, idx + 1);
        poly.setLatLngs(slice);
        head.setLatLng(coords[idx]);
        if (t < 1) {
          requestAnimationFrame(tick);
        } else {
          setTimeout(() => {
            if (map.hasLayer(head)) map.removeLayer(head);
          }, 300);
          resolve(poly);
        }
      };
      requestAnimationFrame(tick);
    });
  }

  // ---- Event wiring ----
  $("origin-country").addEventListener("change", (e) => {
    loadPortsForSide(e.target.value, "origin");
  });
  $("dest-country").addEventListener("change", (e) => {
    loadPortsForSide(e.target.value, "dest");
  });

  $("origin-port").addEventListener("change", () => {
    refreshSelectedMarker("origin");
    if (originMarker) map.panTo(originMarker.getLatLng());
    if (routeLayer) {
      map.removeLayer(routeLayer);
      routeLayer = null;
      $("result").innerHTML = "";
    }
    updateComputeBtn();
  });
  $("dest-port").addEventListener("change", () => {
    refreshSelectedMarker("dest");
    if (routeLayer) {
      map.removeLayer(routeLayer);
      routeLayer = null;
      $("result").innerHTML = "";
    }
    updateComputeBtn();
  });

  $("compute").addEventListener("click", async () => {
    if (routeLayer) {
      map.removeLayer(routeLayer);
      routeLayer = null;
    }
    $("result").innerHTML = "";
    $("compute").disabled = true;

    // Auto-zoom to cover both origin and destination on click.
    if (originMarker && destMarker) {
      const b = L.latLngBounds([
        originMarker.getLatLng(),
        destMarker.getLatLng(),
      ]);
      map.fitBounds(b, { padding: [80, 80], maxZoom: 6 });
    }

    setStatus(
      "Exploring density-weighted lanes from the origin port… first request for a new origin takes 30–60 s. (Dashed line is a great-circle placeholder.)"
    );
    const placeholder =
      originMarker && destMarker
        ? startPlaceholderRoute(originMarker.getLatLng(), destMarker.getLatLng())
        : null;
    try {
      const body = JSON.stringify({
        origin_id: $("origin-port").value,
        dest_id: $("dest-port").value,
      });
      const data = await api("/api/route", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      if (placeholder) placeholder.stop();
      // Convert GeoJSON coords (lon, lat) -> Leaflet latlngs (lat, lon)
      const coords = data.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
      const style = {
        color: getCSS("--route") || "#f59e0b",
        weight: 3.5,
        opacity: 0.95,
        lineJoin: "round",
        lineCap: "round",
      };
      setStatus("Tracing route from origin to destination…");
      routeLayer = await animateRoute(coords, style, 2400);
      const p = data.properties;
      $("result").innerHTML = `
        <div class="route-summary">
          <div class="row"><span class="lbl">Origin</span>
            <span class="val">${escapeHtml(p.origin)} (${escapeHtml(p.origin_country)})</span></div>
          <div class="row"><span class="lbl">Destination</span>
            <span class="val">${escapeHtml(p.destination)} (${escapeHtml(p.destination_country)})</span></div>
          <div class="row"><span class="lbl">Geodesic length</span>
            <span class="val mono">${p.length_km.toLocaleString(undefined, {
              maximumFractionDigits: 0,
            })} km</span></div>
          <div class="row"><span class="lbl">Path cost</span>
            <span class="val mono">${p.cost.toLocaleString(undefined, {
              maximumFractionDigits: 0,
            })}</span></div>
        </div>`;
      setStatus("");
    } catch (err) {
      if (placeholder) placeholder.stop();
      setStatus((err && err.error) || "Route computation failed.", true);
    } finally {
      updateComputeBtn();
    }
  });

  loadCountries();
})();
