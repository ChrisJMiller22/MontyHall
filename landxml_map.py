import xml.etree.ElementTree as ET
import folium
from folium import plugins
import numpy as np
from pyproj import Transformer
import sys
import os

NS = {'lx': 'http://www.landxml.org/schema/LandXML-1.2'}

# GDA94 / MGA Zone 55 (EPSG:28355) -> WGS84 (EPSG:4326)
# EPSG:7856 (GDA2020) has a bad central meridian in older pyproj databases;
# GDA94 and GDA2020 differ by <5 cm in NSW so EPSG:28355 is correct here.
TRANSFORMER = Transformer.from_crs('EPSG:28355', 'EPSG:4326', always_xy=True)

COLOR_MAP = {
    'control':   '#e74c3c',
    'boundary':  '#2980b9',
    'sideshot':  '#e67e22',
    'traverse':  '#27ae60',
    'reference': '#8e44ad',
    'unknown':   '#95a5a6',
}

PARCEL_COLORS = ['#1a6faf', '#1a9f7a']

OBS_COLORS = {
    'Connection': '#888888',
    'Road':       '#c87800',
    'Boundary':   '#1a6faf',
    'Reference':  '#8e44ad',
}


def to_latlon(northing, easting):
    lon, lat = TRANSFORMER.transform(easting, northing)
    return lat, lon


def parse_landxml(filepath):
    tree = ET.parse(filepath)
    root = tree.getroot()

    zone = int(root.find('.//lx:CgPoints', NS).get('zoneNumber', 55))

    points = {}
    for cgp in root.findall('.//lx:CgPoint', NS):
        name = cgp.get('name')
        coords = cgp.text.strip().split()
        northing, easting = float(coords[0]), float(coords[1])
        lat, lon = to_latlon(northing, easting)
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

    return points, parcels, observations, instrument_setups


def arc_latlon(p_start, p_center, p_end, radius, rot, n=80):
    """Generate arc as lat/lon pairs, computing in projected space then converting."""
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
        e = cx + radius * np.cos(a)
        n_ = cy + radius * np.sin(a)
        coords.append(to_latlon(n_, e))
    return coords


def point_icon(ptype, color):
    icons = {
        'control':   'star',
        'boundary':  'circle',
        'sideshot':  'diamond',
        'traverse':  'square',
        'reference': 'triangle-up',
    }
    return folium.CircleMarker, color


def build_map(points, parcels, observations, instrument_setups):
    centre_lat = np.mean([p['lat'] for p in points.values()])
    centre_lon = np.mean([p['lon'] for p in points.values()])

    m = folium.Map(
        location=[centre_lat, centre_lon],
        zoom_start=18,
        tiles='OpenStreetMap',
    )

    # Satellite layer option
    folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Esri World Imagery',
        name='Satellite',
        overlay=False,
        control=True,
    ).add_to(m)

    folium.TileLayer('OpenStreetMap', name='Street Map').add_to(m)

    # --- Layer groups ---
    obs_layers = {
        desc: folium.FeatureGroup(name=f'Observations: {desc}', show=True)
        for desc in ('Connection', 'Road', 'Boundary', 'Reference')
    }
    parcel_layer = folium.FeatureGroup(name='Parcel Boundaries', show=True)
    point_layers = {
        ptype: folium.FeatureGroup(name=f'Points: {ptype.capitalize()}', show=True)
        for ptype in COLOR_MAP
    }

    # --- Observation lines ---
    for obs in observations:
        p1_ref = instrument_setups.get(obs['setupID'])
        p2_ref = instrument_setups.get(obs['targetSetupID'])
        if not (p1_ref and p2_ref and p1_ref in points and p2_ref in points):
            continue
        p1, p2 = points[p1_ref], points[p2_ref]
        desc  = obs['desc']
        color = OBS_COLORS.get(desc, '#aaaaaa')
        layer = obs_layers.get(desc, obs_layers['Connection'])
        folium.PolyLine(
            locations=[[p1['lat'], p1['lon']], [p2['lat'], p2['lon']]],
            color=color,
            weight=1.5,
            opacity=0.6,
            dash_array='6 4',
            tooltip=f"Obs ({desc}): {p1_ref} → {p2_ref}",
        ).add_to(layer)

    # --- Parcel boundaries ---
    for i, parcel in enumerate(parcels):
        color = PARCEL_COLORS[i % len(PARCEL_COLORS)]
        label = f"Lot {parcel['name']} ({parcel['area']} m²)"
        for seg in parcel['segments']:
            if seg['type'] == 'line':
                p1 = points.get(seg['start'])
                p2 = points.get(seg['end'])
                if not (p1 and p2):
                    continue
                locs = [[p1['lat'], p1['lon']], [p2['lat'], p2['lon']]]
                tip  = f"{label}: {seg['start']} → {seg['end']}"
            elif seg['type'] == 'curve':
                ps = points.get(seg['start'])
                pc = points.get(seg['center'])
                pe = points.get(seg['end'])
                if not (ps and pc and pe):
                    continue
                locs = arc_latlon(ps, pc, pe, seg['radius'], seg['rot'])
                tip  = f"{label}: Arc {seg['start']} → {seg['end']} (r={seg['radius']} m)"
            else:
                continue

            folium.PolyLine(
                locations=locs,
                color=color,
                weight=3,
                opacity=0.9,
                tooltip=tip,
            ).add_to(parcel_layer)

    # --- Points ---
    RADIUS = {'control': 7, 'boundary': 6, 'traverse': 5, 'reference': 5, 'sideshot': 5}
    for pt in points.values():
        ptype  = pt['pntSurv']
        color  = COLOR_MAP.get(ptype, '#95a5a6')
        radius = RADIUS.get(ptype, 5)
        layer  = point_layers.get(ptype, point_layers.get('unknown'))

        popup_html = (
            f"<b>Point {pt['name']}</b><br>"
            f"Type: {ptype}<br>"
            f"State: {pt['state']}<br>"
            + (f"Desc: {pt['desc']}<br>" if pt['desc'] else "")
            + f"E: {pt['easting']:.3f}<br>"
            f"N: {pt['northing']:.3f}<br>"
            f"Lat: {pt['lat']:.7f}<br>"
            f"Lon: {pt['lon']:.7f}"
        )

        folium.CircleMarker(
            location=[pt['lat'], pt['lon']],
            radius=radius,
            color='white',
            weight=1,
            fill=True,
            fill_color=color,
            fill_opacity=0.95,
            tooltip=f"Pt {pt['name']} ({ptype})",
            popup=folium.Popup(popup_html, max_width=220),
        ).add_to(layer)

        # Point label
        folium.Marker(
            location=[pt['lat'], pt['lon']],
            icon=folium.DivIcon(
                html=f'<div style="font-size:9px;font-weight:bold;color:{color};'
                     f'text-shadow:0 0 2px white,0 0 2px white;white-space:nowrap;'
                     f'margin-left:8px;margin-top:-6px">{pt["name"]}</div>',
                icon_size=(30, 14),
                icon_anchor=(0, 7),
            ),
        ).add_to(layer)

    # Add all layers to map
    for layer in obs_layers.values():
        layer.add_to(m)
    parcel_layer.add_to(m)
    for layer in point_layers.values():
        layer.add_to(m)

    # Legend
    legend_html = '''
    <div style="position:fixed;bottom:30px;left:30px;z-index:9999;background:white;
         padding:12px 16px;border-radius:8px;border:1px solid #ccc;
         font-family:sans-serif;font-size:12px;box-shadow:2px 2px 6px rgba(0,0,0,0.2)">
      <b style="font-size:13px">Point Types</b><br><br>
      <span style="color:#e74c3c">&#9733;</span> Control<br>
      <span style="color:#2980b9">&#9679;</span> Boundary<br>
      <span style="color:#e67e22">&#9670;</span> Sideshot<br>
      <span style="color:#27ae60">&#9632;</span> Traverse<br>
      <span style="color:#8e44ad">&#9650;</span> Reference<br>
      <br><b>Parcels</b><br>
      <span style="color:#1a6faf">&#9644;</span> Lot 4271<br>
      <span style="color:#1a9f7a">&#9644;</span> Lot 4272<br>
    </div>
    '''
    m.get_root().html.add_child(folium.Element(legend_html))

    folium.LayerControl(collapsed=False).add_to(m)

    return m


def main(filepath):
    points, parcels, observations, instrument_setups = parse_landxml(filepath)
    m = build_map(points, parcels, observations, instrument_setups)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'landxml_map.html')
    m.save(out)
    print(f"Saved: {out}")
    return out


if __name__ == '__main__':
    fp = sys.argv[1] if len(sys.argv) > 1 else (
        '/root/.claude/uploads/886cbe7d-613c-4f5f-8466-cb4ec0bbc492/8d6373ea-DP1307492_V2.xml'
    )
    main(fp)
