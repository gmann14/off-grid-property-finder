# Off-Grid Property Finder — Data Sources Reference

> Complete inventory of every dataset needed, where to get it, format, API availability, and cost.

---

## 1. Elevation / DEM Data

### 1.1 Nova Scotia Enhanced DEM (20m) ⭐ PRIMARY

- **Source:** NS Department of Natural Resources, Geoscience and Mines Branch
- **URL:** https://novascotia.ca/natr/meb/download/dp055.asp
- **Format:** GeoTIFF, 20m resolution
- **Coverage:** All of Nova Scotia
- **Cost:** Free
- **Description:** Hydrologically correct 20m Digital Elevation Model. This is purpose-built for watershed/hydro analysis — ideal for our use case.
- **Projection:** NAD83 UTM Zone 20N (EPSG:2961)
- **Quality:** Excellent for province-wide analysis. Derived from 1:10,000 topographic data.

### 1.2 Canadian Digital Elevation Model (CDEM)

- **Source:** Natural Resources Canada (NRCan)
- **URL (FTP):** https://ftp.maps.canada.ca/pub/nrcan_rncan/elevation/cdem_mnec/
- **URL (Open Data Portal):** https://open.canada.ca/data/en/dataset/7f245e4d-76c2-4caa-951a-45d1d2051333
- **URL (Google Earth Engine):** `NRCan/CDEM` — https://developers.google.com/earth-engine/datasets/catalog/NRCan_CDEM
- **Format:** GeoTIFF (FTP), ImageCollection (GEE)
- **Resolution:** ~0.75 arc-seconds (~23m at NS latitude)
- **Coverage:** All of Canada
- **Cost:** Free
- **WMS:** `https://maps.geogratis.gc.ca/wms/elevation_en?service=WMS&version=1.3.0&request=GetCapabilities`
- **Notes:** Good fallback. Available in GEE for cloud processing. Download by NTS 1:250,000 map sheet.

### 1.3 High Resolution DEM (HRDEM) — LiDAR-derived

- **Source:** NRCan, CanElevation Series
- **URL:** https://open.canada.ca/data/en/dataset/957782bf-847c-4644-a757-e383c0057995
- **Download dir:** https://ftp.maps.canada.ca/pub/elevation/dem_mne/highresolution_hauteresolution/dtm_mnt
- **Format:** GeoTIFF, 1–2m resolution
- **Coverage:** Partial NS coverage (expanding). Southern NS coastal areas well-covered.
- **Cost:** Free
- **Notes:** Amazing resolution for site-specific analysis, but incomplete coverage. Use for Phase 2 drill-down on high-scoring properties.

### 1.4 NS LiDAR Point Clouds & Derived Products

- **Source:** NSGI via DataLocator Elevation Explorer
- **URL:** https://nsgi.novascotia.ca/datalocator/elevation
- **Format:** LAZ (compressed LAS), DEM/DSM/CHM GeoTIFF per 1km² tile
- **Coverage:** Expanding — check Elevation Explorer for current availability
- **Cost:** Free (account required for DataLocator)
- **Projection:** NAD83(CSRS)v6, UTM Zone 20, CGVD2013
- **Notes:** Best available elevation data when coverage exists. 1km² tiles downloadable one at a time through web interface.

### 1.5 NSTDB Digital Terrain Model (DTM)

- **Source:** Government of Nova Scotia via GeoNova
- **URL:** https://open.canada.ca/data/en/dataset/66525e34-5020-a48f-c034-2ef237ef4a8e
- **Download:** https://nsgi.novascotia.ca/WSF_DDS/DDS.svc/DownloadFile?tkey=fhrTtdnDvfytwLz6&id=37
- **ArcGIS REST:** https://nsgiwa.novascotia.ca/arcgis/rest/services/BASE/BASE_NSTDB_10k_DTM_UT83/MapServer
- **Format:** Varies
- **Coverage:** All of Nova Scotia
- **Cost:** Free (open data)

---

## 2. Hydrology / Stream Data

### 2.1 Nova Scotia Hydrographic Network (NSHN) ⭐ PRIMARY

- **Source:** Government of Nova Scotia
- **URL (Open Data Portal):** https://data.novascotia.ca/datasets/dk27-q8k2
- **URL (Canada Open Data):** https://open.canada.ca/data/en/dataset/2ed55c68-b7f8-4db0-15d9-bef40797a4c4
- **Download:** https://nsgi.novascotia.ca/WSF_DDS/DDS.svc/DownloadFile?tkey=fhrTtdnDvfytwLz6&id=XX (check DataLocator for current ID)
- **ArcGIS REST:** https://nsgiwa.novascotia.ca/arcgis/rest/services/WTR/WTR_NSHN_UT83/MapServer
- **Format:** Shapefile
- **Coverage:** All of Nova Scotia
- **Cost:** Free (open data license)
- **Contents:** Rivers, streams, lakes, swamps, water flow lines. The authoritative source for NS inland surface water.
- **Spec doc:** https://nsgc.gov.ns.ca/mappingspecs/Specifications/Compilation/NSHN/NSHN_V2.0_Spec_V1.1.htm

### 2.2 National Hydro Network (NHN)

- **Source:** Natural Resources Canada (GeoBase)
- **URL:** https://open.canada.ca/data/en/dataset/a4b190fe-e090-4e6d-881e-b87956c07977
- **WMS:** https://maps.geogratis.gc.ca/wms/hydro_network_en?request=GetCapabilities&service=WMS
- **Format:** Shapefile/GML, organized by watershed work unit
- **Coverage:** National
- **Cost:** Free
- **Notes:** NSHN is derived from NHN specs. Use NHN if you need cross-provincial consistency or if NSHN is unavailable for some reason.

### 2.3 Water Survey of Canada — HYDAT Database ⭐ CRITICAL for flow estimation

- **Source:** Environment and Climate Change Canada
- **URL:** https://wateroffice.ec.gc.ca/
- **Historical data search:** https://wateroffice.ec.gc.ca/mainmenu/historical_data_index_e.html
- **HYDAT download:** Available from https://wateroffice.ec.gc.ca/ (SQLite database, ~200MB)
- **API (real-time):** https://api.weather.gc.ca/collections/hydrometric-realtime?lang=en
- **API (stations):** https://api.weather.gc.ca/collections/hydrometric-stations?lang=en
- **API (daily means):** https://api.weather.gc.ca/collections/hydrometric-daily-mean?lang=en
- **Format:** SQLite database (HYDAT), OGC API Features (real-time)
- **Coverage:** ~80 active + ~120 discontinued stations in NS
- **Cost:** Free
- **Contents:** Daily mean flow (m³/s), water level, monthly/annual statistics
- **Notes:** Essential for building the flow regression model. The HYDAT SQLite DB is the most useful — contains decades of daily flow data for every gauged station in Canada. The `tidyhydat` R package or `hydat` Python libraries can parse it.

### 2.4 Canadian Surface Water Monitoring Stations

- **Source:** ECCC via MSC Open Data
- **URL:** https://eccc-msc.github.io/open-data/msc-data/obs_hydrometric/readme_hydrometric_en/
- **Format:** CSV (via Datamart), OGC API Features
- **Notes:** Documentation hub for all hydrometric data access methods.

---

## 3. Solar Resource Data

### 3.1 NRCan Photovoltaic Potential & Solar Resource Maps ⭐ PRIMARY

- **Source:** Natural Resources Canada
- **URL (Open Data):** https://open.canada.ca/data/en/dataset/8b434ac7-aedb-4698-90df-ba77424a551f
- **FTP (Geodatabase):** https://ftp.maps.canada.ca/pub/nrcan_rncan/Solar-energy_Energie-solaire/photovoltaic_canada_photovoltaique/PPSRMC_CEPESPC_FGP_PGF.zip
- **Municipality CSV:** Available from same Open Data page (mean daily global insolation per municipality)
- **ArcGIS REST:** https://geoappext.nrcan.gc.ca/arcgis/rest/services/Energy/clean_energy_solar_radiation_insolation/MapServer
- **NRCan page:** https://www.nrcan.gc.ca/our-natural-resources/energy-sources-distribution/renewable-energy/solar-photovoltaic-energy/tools-solar-photovoltaic-energy/photovoltaic-potential-and-solar-resource-maps-canada/18366
- **Format:** File Geodatabase (FGDB), CSV, ArcGIS MapServer
- **Coverage:** All of Canada
- **Cost:** Free
- **Contents:** Mean daily global insolation (kWh/m²), PV potential for various orientations (south-facing, latitude tilt, etc.)
- **Resolution:** Municipality-level (CSV) or ~10km grid (geodatabase)
- **Notes:** Good for regional baseline. Site-specific solar assessment comes from DEM-derived aspect/slope analysis.

### 3.2 DEM-Derived Solar Analysis (computed)

Not a data source per se — we derive this from the DEM:
- **Aspect raster:** Generated from DEM using GDAL `gdaldem aspect`
- **Slope raster:** Generated from DEM using GDAL `gdaldem slope`
- **Hillshade:** Optional, for visualization
- **Tools:** `gdaldem`, `rasterio`, `richdem`, or GEE `ee.Terrain`

---

## 4. Crown Land

### 4.1 NS Crown Land Dataset ⭐ PRIMARY

- **Source:** Nova Scotia Department of Natural Resources and Renewables
- **URL (Open Data):** https://data.novascotia.ca/Lands-Forests-and-Wildlife/Crown-Land/3nka-59nz
- **URL (Map view):** https://data.novascotia.ca/Lands-Forests-and-Wildlife/Crown-Land-Map/sqec-gjbw
- **URL (Canada Open Data):** https://open.canada.ca/data/en/dataset/faef7b10-6357-8918-ad93-26d64ad82c83
- **Download (GeoNova):** https://nsgi.novascotia.ca/WSF_DDS/DDS.svc/DownloadFile?tkey=fhrTtdnDvfytwLz6&id=87
- **ArcGIS REST:** https://nsgiwa.novascotia.ca/arcgis/rest/services/PLAN/PLANCrownLand.../MapServer
- **ArcGIS Item:** https://www.arcgis.com/home/item.html?id=e1245f034994416f834647956dea7d85
- **Format:** Shapefile (via GeoNova download), GeoJSON/CSV (via Open Data API)
- **Coverage:** All of Nova Scotia
- **Cost:** Free
- **Description:** Spatial dataset of all Crown lands under administration of the Minister of Natural Resources and Renewables, per the Crown Lands Act.

---

## 5. Property / Parcel Data

### 5.1 NSGI Property Parcels (via GeoNova) ⚠️ KEY CONSTRAINT

- **Source:** Nova Scotia Geomatics Infrastructure (NSGI)
- **URL:** https://nsgi.novascotia.ca/gdd/ (DataLocator)
- **Access:** Requires free NSGI account registration
- **Format:** Shapefile/GDB (via DataLocator download)
- **Coverage:** All of Nova Scotia (cadastral data)
- **Cost:** Account required; basic access is free. Some datasets may have license restrictions.
- **Notes:** This is the authoritative source for NS property parcel boundaries. The "Planning" theme on GeoNova includes cadastral/parcel data. Must register for an account to access.
- **ArcGIS Services:** Various NSGI ArcGIS REST services may expose parcel layers.

### 5.2 NS Property Online

- **Source:** Service Nova Scotia
- **URL:** https://www.novascotia.ca/sign-property-online
- **Access:** Subscription required ($)
- **Cost:** Pay-per-search or subscription plans
- **Format:** Web interface only (no API, no bulk download)
- **Contents:** Land ownership, assessment info, deeds, plans
- **Notes:** Good for individual property lookup. NOT suitable for bulk analysis. Could be used to validate individual high-scoring properties.

### 5.3 PVSC (Property Valuation Services Corporation)

- **Source:** PVSC
- **URL:** https://www.pvsc.ca/find-assessment
- **Data Requests:** https://www.pvsc.ca/data-disclosure
- **Access:** Bulk data requests directed to NSGI GIS Division
- **Format:** Web lookup (individual), bulk via NSGI
- **Coverage:** All ~400K NS properties
- **Cost:** Individual lookups free on website; bulk data may have fees
- **Contents:** Assessment values, property classification, AAN (Assessment Account Number)
- **Notes:** PVSC directs bulk/GIS data requests to NSGI. Individual lookups work for validation.

### 5.4 ViewPoint.ca

- **Source:** ViewPoint Inc. (private company)
- **URL:** https://www.viewpoint.ca
- **Access:** Free basic access, premium features require account
- **API:** No public API
- **Format:** Web interface only
- **Contents:** MLS listings, property boundaries, assessment data, sale history, aerial imagery
- **Notes:** Best single source for NS property research in a browser. No API or bulk data access. Could potentially be scraped (TOS considerations). Shows property boundaries overlaid on map.

### 5.5 Regrid (formerly Loveland Technologies)

- **Source:** Regrid
- **URL:** https://app.regrid.com/ca/ns
- **API:** https://regrid.com/api (paid)
- **Format:** GeoJSON, Shapefile, API
- **Coverage:** NS parcel data available
- **Cost:** Paid API plans. Free browse on web map.
- **Notes:** Third-party commercial parcel data aggregator. Has NS coverage. Most reliable way to get bulk parcel data programmatically. API pricing varies.

### 5.6 OpenStreetMap (Landuse Polygons) — Fallback

- **Source:** OpenStreetMap contributors
- **URL:** https://download.geofabrik.de/north-america/canada/nova-scotia-latest.osm.pbf
- **Format:** PBF (OSM), convertible to Shapefile
- **Coverage:** Variable — some rural parcels mapped, many are not
- **Cost:** Free (ODbL license)
- **Notes:** NOT a reliable source for property boundaries, but may have some landuse polygons. Better for roads.

---

## 6. Road Network

### 6.1 OpenStreetMap Roads ⭐ PRIMARY

- **Source:** OpenStreetMap
- **URL:** https://download.geofabrik.de/north-america/canada/nova-scotia-latest.osm.pbf
- **Format:** PBF → convert to Shapefile with `ogr2ogr` or `osmium`
- **Coverage:** Excellent for NS — all public roads mapped
- **Cost:** Free (ODbL)
- **Notes:** Best free source for road network. Includes road classification (highway, residential, track, etc.). Rural/forest roads well-represented.

### 6.2 National Road Network (NRN)

- **Source:** Statistics Canada / NRCan
- **URL:** https://open.canada.ca/data/en/dataset/3d282116-e556-400c-9306-ca1a3cada77f
- **Format:** Shapefile/GML
- **Coverage:** All of Canada
- **Cost:** Free
- **Notes:** Official government road data. May be less current than OSM for minor rural roads.

### 6.3 NS Road Network (NSTDB)

- **Source:** NSGI
- **URL:** Via GeoNova DataLocator
- **Format:** Shapefile
- **Coverage:** Nova Scotia
- **Cost:** Free (with NSGI account)

---

## 7. Land Cover / Forest

### 7.1 NSTDB Land Cover (Poly)

- **Source:** Government of Nova Scotia
- **URL:** https://open.canada.ca/data/en/dataset/1c5afbb5-0a23-3d0e-54c4-744aec42a69b
- **Download:** https://nsgi.novascotia.ca/WSF_DDS/DDS.svc/DownloadFile?tkey=fhrTtdnDvfytwLz6&id=13
- **ArcGIS REST:** https://nsgiwa.novascotia.ca/arcgis/rest/services/BASE/BASE_NSTDB_10k_Land_Cover_UT83/MapServer
- **Format:** Shapefile
- **Coverage:** Nova Scotia
- **Cost:** Free
- **Notes:** Useful for identifying forested vs. cleared areas (affects solar scoring). 1:10,000 scale.

### 7.2 Canada Landcover (30m)

- **Source:** NRCan
- **URL:** Available via GEE or Open Data
- **Format:** GeoTIFF
- **Resolution:** 30m
- **Notes:** Less detailed than NSTDB but useful for quick classification.

---

## 8. Property Listings (MLS / Active Sales)

### 8.1 Realtor.ca / CREA DDF API

- **Source:** Canadian Real Estate Association (CREA)
- **API Docs:** https://ddfapi-docs.realtor.ca/
- **Board API:** https://boardapi-docs.realtor.ca/
- **Access:** Restricted to licensed REALTORs and approved third-party websites
- **Format:** RESO Web API (replacing RETS)
- **Coverage:** ~65% of Canadian listings nationally
- **Cost:** Subscription-based for approved integrations
- **Notes:** Not available for personal/hobbyist use. Requires business relationship with CREA or a regional board (NSAR in NS). DDF coverage in NS is good since NSAR participates.

### 8.2 Realtor.ca Web Scraping (unofficial)

- **URL:** https://www.realtor.ca/
- **Method:** HTTP requests to their internal API endpoints
- **Legal:** Against TOS. Use at own risk.
- **Notes:** Realtor.ca has a search API that returns JSON. Well-documented in various GitHub repos. Filter by property type, price, location. For personal research use only.

### 8.3 Nova Scotia Association of REALTORS (NSAR)

- **URL:** https://www.nsar.ca/
- **Access:** Member access only for MLS data
- **Notes:** Local board. All NS MLS listings flow through NSAR.

### 8.4 Kijiji / Facebook Marketplace / LandWatch

- **Kijiji:** https://www.kijiji.ca/b-real-estate/nova-scotia/
- **LandWatch:** https://www.landwatch.com/nova-scotia-land-for-sale
- **Notes:** Some rural/off-grid properties are listed here rather than MLS. Manual monitoring or scraping required.

---

## 9. Additional / Bonus Data Sources

### 9.1 NS Open Data Portal

- **URL:** https://data.novascotia.ca/
- **Notes:** Central hub. Search for additional datasets. Socrata-powered, has API access for most datasets.

### 9.2 Protected Areas

- **Source:** NS Environment
- **URL:** Check NS Open Data
- **Notes:** Useful as a disqualifier — can't develop in protected areas.

### 9.3 Geology / Groundwater

- **Source:** NS Department of Natural Resources
- **URL:** https://novascotia.ca/natr/meb/download/gis-data-maps.asp
- **Format:** Shapefile
- **Cost:** Free
- **Notes:** Surficial geology, bedrock, groundwater atlas. Useful for well drilling assessment.

### 9.4 Climate Normals

- **Source:** Environment Canada
- **URL:** https://climate.weather.gc.ca/climate_normals/
- **Notes:** Precipitation normals useful for flow estimation.

### 9.5 Building Footprints

- **Source:** NRCan / Microsoft
- **Notes:** >95% of NS building footprints available on Open Government. Useful for identifying already-developed vs. undeveloped parcels.

---

## 10. Data Access Summary

### Freely Available (no account needed)
- ✅ NS Enhanced DEM (20m)
- ✅ CDEM (via FTP or GEE)
- ✅ HRDEM (via FTP)
- ✅ NSHN (stream network)
- ✅ NHN (national hydro)
- ✅ Crown Land polygons
- ✅ NRCan Solar Resource data
- ✅ OpenStreetMap roads
- ✅ HYDAT database
- ✅ NSTDB Land Cover
- ✅ NS Geology data

### Account Required (free)
- ⚠️ NSGI DataLocator (property parcels, LiDAR tiles, NSTDB layers)

### Paid / Restricted
- 💰 NS Property Online (subscription for title searches)
- 💰 Regrid API (parcel data, paid plans)
- 🔒 CREA DDF / Realtor.ca API (REALTOR members only)
- 🔒 PVSC bulk assessment data (via NSGI, may have fees)

### MVP-Critical Downloads

For the MVP, download these first:
1. **NS Enhanced DEM** (dp055.asp) — single file, covers all NS
2. **NSHN** (stream network shapefile) — from data.novascotia.ca or GeoNova
3. **Crown Land** (shapefile) — from data.novascotia.ca
4. **OpenStreetMap NS extract** — from Geofabrik
5. **HYDAT database** — from wateroffice.ec.gc.ca

Total download size: ~2–5GB estimated.

---

## 11. Key URLs Quick Reference

| Resource | URL |
|---|---|
| GeoNova (NS GIS hub) | https://geonova.novascotia.ca/ |
| NS Open Data Portal | https://data.novascotia.ca/ |
| NSGI DataLocator | https://nsgi.novascotia.ca/gdd/ |
| NSGI Elevation Explorer | https://nsgi.novascotia.ca/datalocator/elevation |
| Canada Open Data | https://open.canada.ca/ |
| NRCan FTP (elevation) | https://ftp.maps.canada.ca/pub/nrcan_rncan/elevation/ |
| NRCan FTP (solar) | https://ftp.maps.canada.ca/pub/nrcan_rncan/Solar-energy_Energie-solaire/ |
| Water Office (HYDAT) | https://wateroffice.ec.gc.ca/ |
| ECCC Hydrometric API | https://api.weather.gc.ca/collections/hydrometric-stations |
| Geofabrik (OSM data) | https://download.geofabrik.de/north-america/canada/ |
| ViewPoint.ca | https://www.viewpoint.ca/ |
| PVSC | https://www.pvsc.ca/ |
| NS Surface Water Approval | https://novascotia.ca/nse/water/withdrawalApproval.asp |
| NRCan Solar Maps | https://www.nrcan.gc.ca/.../photovoltaic-potential-and-solar-resource-maps-canada/18366 |
| CDEM on GEE | https://developers.google.com/earth-engine/datasets/catalog/NRCan_CDEM |
| Cyr et al. 2011 (NB SHP) | https://www.sciencedirect.com/science/article/abs/pii/S0960148111001753 |
