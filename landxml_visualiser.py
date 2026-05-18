import xml.etree.ElementTree as ET
import plotly.graph_objects as go
import numpy as np
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

MARKER_MAP = {
    'control':   'star',
    'boundary':  'circle',
    'sideshot':  'diamond',
    'traverse':  'square',
    'reference': 'triangle-up',
}

OBS_COLORS = {
    'Connection': 'rgba(120,120,120,0.45)',
    'Road':       'rgba(200,120,0,0.45)',
    'Boundary':   'rgba(0,120,220,0.45)',
    'Reference':  'rgba(160,0,180,0.45)',
}

PARCEL_COLORS = ['#1a6faf', '#1a9f7a', '#9f5a1a']


def parse_landxml(filepath):
    tree = ET.parse(filepath)
    root = tree.getroot()

    points = {}
    for cgp in root.findall('.//lx:CgPoint', NS):
        name = cgp.get('name')
        coords = cgp.text.strip().split()
        points[name] = {
            'name':    name,
            'state':   cgp.get('state', 'unknown'),
            'pntSurv': cgp.get('pntSurv', 'unknown'),
            'desc':    cgp.get('desc', ''),
            'northing': float(coords[0]),
            'easting':  float(coords[1]),
        }

    parcels = []
    for parcel in root.findall('.//lx:Parcel', NS):
        segments = []
        coord_geom = parcel.find('lx:CoordGeom', NS)
        if coord_geom is not None:
            for line in coord_geom.findall('lx:Line', NS):
                segments.append({
                    'type':  'line',
                    'start': line.find('lx:Start', NS).get('pntRef'),
                    'end':   line.find('lx:End', NS).get('pntRef'),
                })
            for curve in coord_geom.findall('lx:Curve', NS):
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

    return points, parcels, observations, instrument_setups


def arc_points(cx, cy, r, start_angle, end_angle, rot, n=60):
    """Return (x_arr, y_arr) for an arc. x=Easting, y=Northing."""
    if rot == 'cw':
        if end_angle >= start_angle:
            end_angle -= 2 * np.pi
    else:
        if end_angle <= start_angle:
            end_angle += 2 * np.pi
    angles = np.linspace(start_angle, end_angle, n)
    return cx + r * np.cos(angles), cy + r * np.sin(angles)


def build_figure(points, parcels, observations, instrument_setups, title):
    fig = go.Figure()

    # --- Observation lines (drawn first, behind everything) ---
    shown_obs_groups = set()
    for obs in observations:
        from_ref = instrument_setups.get(obs['setupID'])
        to_ref   = instrument_setups.get(obs['targetSetupID'])
        if not (from_ref and to_ref and from_ref in points and to_ref in points):
            continue
        p1, p2 = points[from_ref], points[to_ref]
        desc    = obs['desc']
        color   = OBS_COLORS.get(desc, 'rgba(150,150,150,0.4)')
        group   = f'obs_{desc}'
        fig.add_trace(go.Scatter(
            x=[p1['easting'], p2['easting'], None],
            y=[p1['northing'], p2['northing'], None],
            mode='lines',
            line=dict(color=color, width=1, dash='dot'),
            name=f'Obs: {desc}',
            legendgroup=group,
            showlegend=(group not in shown_obs_groups),
            hoverinfo='skip',
        ))
        shown_obs_groups.add(group)

    # --- Parcel boundaries ---
    for i, parcel in enumerate(parcels):
        color = PARCEL_COLORS[i % len(PARCEL_COLORS)]
        label = f"Lot {parcel['name']} ({parcel['area']} m²)"
        group = f"parcel_{parcel['name']}"
        first = True
        for seg in parcel['segments']:
            if seg['type'] == 'line':
                p1 = points.get(seg['start'])
                p2 = points.get(seg['end'])
                if not (p1 and p2):
                    continue
                xs = [p1['easting'],  p2['easting']]
                ys = [p1['northing'], p2['northing']]
                ht = f"{label}<br>{seg['start']} → {seg['end']}"
            elif seg['type'] == 'curve':
                pc = points.get(seg['center'])
                ps = points.get(seg['start'])
                pe = points.get(seg['end'])
                if not (pc and ps and pe):
                    continue
                cx, cy = pc['easting'], pc['northing']
                sa = np.arctan2(ps['northing'] - cy, ps['easting'] - cx)
                ea = np.arctan2(pe['northing'] - cy, pe['easting'] - cx)
                xs, ys = arc_points(cx, cy, seg['radius'], sa, ea, seg['rot'])
                ht = f"{label}<br>Arc {seg['start']} → {seg['end']}<br>r={seg['radius']} m"
            else:
                continue

            fig.add_trace(go.Scatter(
                x=xs, y=ys,
                mode='lines',
                line=dict(color=color, width=2.5),
                name=label,
                legendgroup=group,
                showlegend=first,
                hovertext=ht,
                hoverinfo='text',
            ))
            first = False

    # --- Points by survey type ---
    groups = {}
    for pt in points.values():
        ptype = pt['pntSurv']
        groups.setdefault(ptype, {'x': [], 'y': [], 'names': [], 'hover': []})
        groups[ptype]['x'].append(pt['easting'])
        groups[ptype]['y'].append(pt['northing'])
        groups[ptype]['names'].append(pt['name'])
        hover = (
            f"<b>Point {pt['name']}</b><br>"
            f"Type: {ptype}<br>"
            f"State: {pt['state']}"
        )
        if pt['desc']:
            hover += f"<br>Desc: {pt['desc']}"
        hover += f"<br>E: {pt['easting']:.3f}<br>N: {pt['northing']:.3f}"
        groups[ptype]['hover'].append(hover)

    for ptype, g in groups.items():
        color  = COLOR_MAP.get(ptype, '#95a5a6')
        symbol = MARKER_MAP.get(ptype, 'circle')
        size   = 12 if ptype == 'control' else 9
        fig.add_trace(go.Scatter(
            x=g['x'], y=g['y'],
            mode='markers+text',
            marker=dict(color=color, size=size, symbol=symbol,
                        line=dict(color='white', width=1)),
            text=g['names'],
            textposition='top right',
            textfont=dict(size=9, color=color),
            name=f'{ptype.capitalize()} points',
            legendgroup=f'ptype_{ptype}',
            hovertext=g['hover'],
            hoverinfo='text',
        ))

    fig.update_layout(
        title=dict(text=title, font=dict(size=15)),
        xaxis=dict(title='Easting (m MGA2020)', scaleanchor='y', scaleratio=1,
                   gridcolor='#ebebeb'),
        yaxis=dict(title='Northing (m MGA2020)', gridcolor='#ebebeb'),
        plot_bgcolor='white',
        paper_bgcolor='white',
        legend=dict(groupclick='toggleitem', bordercolor='#ccc', borderwidth=1,
                    font=dict(size=11)),
        hovermode='closest',
        width=1100,
        height=820,
    )
    return fig


def main(filepath):
    points, parcels, observations, instrument_setups = parse_landxml(filepath)

    # Derive a title from the file or survey header
    title = (
        'LandXML Survey — DP1307492<br>'
        '<sup>Plan of Subdivision of Lot 427 in DP1278557 · Llanarth NSW · MGA2020 Zone 55</sup>'
    )

    fig = build_figure(points, parcels, observations, instrument_setups, title)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'landxml_visualisation.html')
    fig.write_html(out, include_plotlyjs='cdn')
    print(f"Saved: {out}")
    return out


if __name__ == '__main__':
    fp = sys.argv[1] if len(sys.argv) > 1 else (
        '/root/.claude/uploads/886cbe7d-613c-4f5f-8466-cb4ec0bbc492/8d6373ea-DP1307492_V2.xml'
    )
    main(fp)
