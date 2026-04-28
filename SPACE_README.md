---
title: Global Shipping Routes
emoji: 🚢
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# Global Shipping Route Finder

Interactive Flask + Leaflet app: pick any two World Port Index ports and get
the least-cost maritime route along the World Bank / IMF AIS shipping-density
raster. See the [GitHub repo](https://github.com/nrahaman1/global-shipping-routes)
for source and docs.

The first request for a new origin port runs a single-source Dijkstra over the
2 km density raster (~30–60 s) and is then cached.
