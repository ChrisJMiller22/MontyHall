import xml.etree.ElementTree as ET
import folium
import numpy as np
from pyproj import Transformer
import json
import sys
import os

NS = {'lx': 'http://www.landxml.org/schema/LandXML-1.2'}

COLOR_MAP = {
    'control':   '#e74c3c',
    'boundary':  '#2980b9',
    'sideshot':  '#e67e22',
    'traverse':  '#27ae60',
    'reference': '#8e44ad',
    'unknown':   '#95a5a6',
}

PARCEL_COLORS = [
    '#1a6faf', '#1a9f7a', '#9f5a1a', '#7a1a9f', '#9f1a3a',
    '#1a4f9f', '#2e8b57', '#8b4513', '#483d8b', '#8b008b',
]

OBS_COLORS = {
    'Connection': '#888888',
    'Road':       '#c87800',
    'Boundary':   '#1a6faf',
    'Reference':  '#8e44ad',
}

# GDA94 / MGA zone N -> EPSG:2835N  (zone 50-56)
def make_transformer(zone):
    epsg = 28300 + int(zone)
    return Transformer.from_crs(f'EPSG:{epsg}', 'EPSG:4326', always_xy=True)


def to_latlon(transformer, northing, easting):
    lon, lat = transformer.transform(easting, northing)
    return lat, lon


def parse_landxml(filepath):
    tree = ET.parse(filepath)
    root = tree.getroot()

    cgpoints_el = root.find('.//lx:CgPoints', NS)
    zone = int(cgpoints_el.get('zoneNumber', 55))
    transformer = make_transformer(zone)

    points = {}
    for cgp in root.findall('.//lx:CgPoint', NS):
        name = cgp.get('name')
        coords = cgp.text.strip().split()
        northing, easting = float(coords[0]), float(coords[1])
        lat, lon = to_latlon(transformer, northing, easting)
        points[name] = {
            'name':     name,
            'state':    cgp.get('state', 'unknown'),
            'pntSurv':  cgp.get('pntSurv', 'unknown'),
            'desc':     cgp.get('desc', ''),
            'northing': northing,
            'easting':  easting,
            'lat':      lat,
            'lon':      lon,
        }

    parcels = []
    for parcel in root.findall('.//lx:Parcel', NS):
        segments = []
        cg = parcel.find('lx:CoordGeom', NS)
        if cg is not None:
            for line in cg.findall('lx:Line', NS):
                segments.append({
                    'type':  'line',
                    'start': line.find('lx:Start', NS).get('pntRef'),
                    'end':   line.find('lx:End', NS).get('pntRef'),
                })
            for curve in cg.findall('lx:Curve', NS):
                segments.append({
                    'type':   'curve',
                    'start':  curve.find('lx:Start', NS).get('pntRef'),
                    'center': curve.find('lx:Center', NS).get('pntRef'),
                    'end':    curve.find('lx:End', NS).get('pntRef'),
                    'radius': float(curve.get('radius')),
                    'rot':    curve.get('rot', 'cw'),
                })
        parcels.append({
            'name':     parcel.get('name'),
            'state':    parcel.get('state', ''),
            'area':     parcel.get('area', ''),
            'segments': segments,
        })

    instrument_setups = {}
    for setup in root.findall('.//lx:InstrumentSetup', NS):
        inst_pt = setup.find('lx:InstrumentPoint', NS)
        if inst_pt is not None:
            instrument_setups[setup.get('id')] = inst_pt.get('pntRef')

    observations = []
    for tag in ('lx:ReducedObservation', 'lx:ReducedArcObservation'):
        for obs in root.findall(f'.//{tag}', NS):
            observations.append({
                'setupID':       obs.get('setupID'),
                'targetSetupID': obs.get('targetSetupID'),
                'desc':          obs.get('desc', 'Connection'),
            })

    return points, parcels, observations, instrument_setups, zone


def arc_latlon(transformer, p_start, p_center, p_end, radius, rot, n=40):
    cx, cy = p_center['easting'], p_center['northing']
    sa = np.arctan2(p_start['northing'] - cy, p_start['easting'] - cx)
    ea = np.arctan2(p_end['northing']   - cy, p_end['easting']   - cx)
    if rot == 'cw':
        if ea >= sa:
            ea -= 2 * np.pi
    else:
        if ea <= sa:
            ea += 2 * np.pi
    angles = np.linspace(sa, ea, n)
    coords = []
    for a in angles:
        coords.append(to_latlon(transformer, cy + radius * np.sin(a),
                                             cx + radius * np.cos(a)))
    return coords


def build_geojson_lines(features):
    return {"type": "FeatureCollection", "features": features}


def build_map(points, parcels, observations, instrument_setups, zone):
    transformer = make_transformer(zone)

    lats = [p['lat'] for p in points.values()]
    lons = [p['lon'] for p in points.values()]
    centre = [np.mean(lats), np.mean(lons)]

    m = folium.Map(location=centre, zoom_start=15, max_zoom=22, tiles=None)

    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri World Imagery',
        name='Satellite',
        overlay=False,
        control=True,
        max_zoom=22,
        max_native_zoom=18,
    ).add_to(m)

    folium.TileLayer(
        tiles='OpenStreetMap',
        name='Street Map',
        max_zoom=22,
        max_native_zoom=19,
    ).add_to(m)

    # --- Parcel boundaries as GeoJSON (one layer per unique colour bucket) ---
    parcel_features = []
    for i, parcel in enumerate(parcels):
        color = PARCEL_COLORS[i % len(PARCEL_COLORS)]
        coords_list = []
        for seg in parcel['segments']:
            if seg['type'] == 'line':
                p1 = points.get(seg['start'])
                p2 = points.get(seg['end'])
                if p1 and p2:
                    coords_list.append([[p1['lon'], p1['lat']], [p2['lon'], p2['lat']]])
            elif seg['type'] == 'curve':
                ps = points.get(seg['start'])
                pc = points.get(seg['center'])
                pe = points.get(seg['end'])
                if ps and pc and pe:
                    latlons = arc_latlon(transformer, ps, pc, pe,
                                         seg['radius'], seg['rot'])
                    coords_list.append([[lon, lat] for lat, lon in latlons])

        for coords in coords_list:
            parcel_features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {
                    "name":  parcel['name'],
                    "area":  parcel['area'],
                    "state": parcel['state'],
                    "color": color,
                },
            })

    parcel_layer = folium.FeatureGroup(name='Parcel Boundaries', show=True)
    folium.GeoJson(
        build_geojson_lines(parcel_features),
        style_function=lambda f: {
            'color':   f['properties']['color'],
            'weight':  2.5,
            'opacity': 0.9,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=['name', 'area', 'state'],
            aliases=['Lot', 'Area (m²)', 'State'],
        ),
    ).add_to(parcel_layer)
    parcel_layer.add_to(m)

    # --- Observations as GeoJSON (hidden by default — large dataset) ---
    obs_features_by_desc = {}
    for obs in observations:
        p1_ref = instrument_setups.get(obs['setupID'])
        p2_ref = instrument_setups.get(obs['targetSetupID'])
        if not (p1_ref and p2_ref and p1_ref in points and p2_ref in points):
            continue
        p1, p2 = points[p1_ref], points[p2_ref]
        desc = obs['desc']
        obs_features_by_desc.setdefault(desc, []).append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[p1['lon'], p1['lat']], [p2['lon'], p2['lat']]],
            },
            "properties": {"from": p1_ref, "to": p2_ref, "desc": desc},
        })

    for desc, features in obs_features_by_desc.items():
        color = OBS_COLORS.get(desc, '#aaaaaa')
        obs_layer = folium.FeatureGroup(name=f'Observations: {desc}', show=False)
        folium.GeoJson(
            build_geojson_lines(features),
            style_function=lambda f, c=color: {
                'color':     c,
                'weight':    1.2,
                'opacity':   0.6,
                'dashArray': '5 4',
            },
            tooltip=folium.GeoJsonTooltip(
                fields=['from', 'to', 'desc'],
                aliases=['From', 'To', 'Type'],
            ),
        ).add_to(obs_layer)
        obs_layer.add_to(m)

    # --- Points as GeoJSON CircleMarkers grouped by type ---
    show_labels = len(points) <= 200

    for ptype, color in COLOR_MAP.items():
        group_pts = [p for p in points.values() if p['pntSurv'] == ptype]
        if not group_pts:
            continue
        layer = folium.FeatureGroup(name=f'Points: {ptype.capitalize()}', show=True)
        for pt in group_pts:
            popup_html = (
                f"<b>Point {pt['name']}</b><br>"
                f"Type: {ptype}<br>State: {pt['state']}<br>"
                + (f"Desc: {pt['desc']}<br>" if pt['desc'] else "")
                + f"E: {pt['easting']:.3f}<br>N: {pt['northing']:.3f}"
            )
            folium.CircleMarker(
                location=[pt['lat'], pt['lon']],
                radius=5 if ptype == 'control' else 4,
                color='white',
                weight=1,
                fill=True,
                fill_color=color,
                fill_opacity=0.9,
                tooltip=f"Pt {pt['name']} ({ptype})",
                popup=folium.Popup(popup_html, max_width=200),
            ).add_to(layer)

            if show_labels:
                folium.Marker(
                    location=[pt['lat'], pt['lon']],
                    icon=folium.DivIcon(
                        html=(f'<div style="font-size:9px;font-weight:bold;color:{color};'
                              f'text-shadow:0 0 2px white,0 0 2px white;white-space:nowrap;'
                              f'margin-left:7px;margin-top:-6px">{pt["name"]}</div>'),
                        icon_size=(30, 14),
                        icon_anchor=(0, 7),
                    ),
                ).add_to(layer)
        layer.add_to(m)

    # Legend
    legend_html = '''
    <div style="position:fixed;bottom:30px;left:30px;z-index:9999;background:white;
         padding:12px 16px;border-radius:8px;border:1px solid #ccc;
         font-family:sans-serif;font-size:12px;box-shadow:2px 2px 6px rgba(0,0,0,0.2)">
      <b style="font-size:13px">Point Types</b><br><br>
      <span style="color:#e74c3c">&#9679;</span> Control<br>
      <span style="color:#2980b9">&#9679;</span> Boundary<br>
      <span style="color:#e67e22">&#9679;</span> Sideshot<br>
      <span style="color:#27ae60">&#9679;</span> Traverse<br>
      <span style="color:#8e44ad">&#9679;</span> Reference<br>
    </div>
    '''
    m.get_root().html.add_child(folium.Element(legend_html))
    folium.LayerControl(collapsed=False).add_to(m)

    return m


def main(filepath):
    points, parcels, observations, instrument_setups, zone = parse_landxml(filepath)
    print(f"Parsed: {len(points)} points, {len(parcels)} parcels, "
          f"{len(observations)} observations (zone {zone})")
    m = build_map(points, parcels, observations, instrument_setups, zone)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'landxml_map.html')
    m.save(out)
    print(f"Saved: {out}")
    return out


if __name__ == '__main__':
    fp = sys.argv[1] if len(sys.argv) > 1 else (
        '/root/.claude/uploads/3b608fd8-ce2c-4529-92aa-967c2e7d50df/8472a2a9-DP1228369p_V1.xml'
    )
    main(fp)
