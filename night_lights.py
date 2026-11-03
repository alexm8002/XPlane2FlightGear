#!/usr/bin/env python3
"""OBJ8 lighting to FlightGear scenery assets (v0.3.14).

No fabricated light colors for unknown light definitions: named/parameter lights
are classified conservatively. Rendered as night-only emissive crossed quads,
not actual spill illumination. This is a source-compatible first pass.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
import math

from axis_transform import xp_to_fg

@dataclass(frozen=True)
class Light:
    command: str
    name: str
    xyz: tuple[float,float,float]
    color: tuple[float,float,float] | None
    size: float = 0.22


def classify(name: str):
    """Return RGB or None rather than guessing colors of opaque XP light names."""
    n=name.lower().replace('-', '_')
    parts=n.replace('/', '_').split('_')
    if 'blue' in parts or n in ('taxi_b','taxiway_b','taxi_edge_b') or 'taxi_blue' in n:
        return (0.06,0.28,1.0)
    if 'green' in parts or 'taxi_g' in n or 'threshold' in n:
        return (0.08,1.0,0.14)
    if 'red' in parts or 'stopbar' in n or 'stop_bar' in n or 'reil_red' in n:
        return (1.0,0.06,0.035)
    if 'amber' in parts or 'yellow' in parts:
        return (1.0,0.65,0.07)
    if 'white' in parts or n in ('taxi_w','rwy_w') or 'halogen' in n or 'flood' in n:
        return (1.0,0.93,0.76)
    return None


def custom_light_color(name: str, parts: list[str]):
    """Decode RGBA encoded after xyz by X-Plane's full_custom_halo family.

    Other parameterized light names have differing schemas and remain unknown.
    This does not simulate the spill cone or its physical illumination.
    """
    if name.lower() not in ('full_custom_halo', 'full_custom_halo_night'):
        return None
    if len(parts) < 9:
        return None
    try:
        rgba = [float(v) for v in parts[5:9]]
    except ValueError:
        return None
    if not all(math.isfinite(v) for v in rgba) or rgba[3] <= 0:
        return None
    return tuple(max(0., min(1.,v)) for v in rgba[:3])


def parse_obj_lights(path: Path):
    lights=[]
    counts=Counter()
    unknown=Counter()
    lit=None
    for raw in path.read_text(encoding='utf-8-sig',errors='replace').splitlines():
        p=raw.strip().split()
        if not p:continue
        key=p[0]
        if key=='TEXTURE_LIT' and len(p)>1:
            lit=p[1]
        if key not in ('LIGHT_PARAM','LIGHT_NAMED','LIGHT_CUSTOM','LIGHT_SPILL_CUSTOM'):continue
        counts[key]+=1
        try:
            if key in ('LIGHT_PARAM','LIGHT_NAMED'):
                if len(p)<5: raise ValueError('missing coordinates')
                name=p[1]
                xyz=tuple(float(n) for n in p[2:5])
                color=classify(name) or (custom_light_color(name,p) if key=='LIGHT_PARAM' else None)
                if color is None:unknown[name]+=1
                lights.append(Light(key,name,xyz,color))
            elif key=='LIGHT_CUSTOM':
                if len(p)<9:raise ValueError('missing custom color')
                xyz=tuple(float(n) for n in p[1:4])
                rgb=tuple(max(0.,min(1.,float(n))) for n in p[4:7])
                lights.append(Light(key,'custom',xyz,rgb))
            else:
                # Spill lights affect ground illumination, not direct visible bulbs.
                # Report for later dedicated implementation; don't invent an effect.
                unknown['LIGHT_SPILL_CUSTOM']+=1
        except ValueError:
            unknown['invalid_'+key]+=1
    return lights,lit,counts,unknown


def write_lights_ac(path,lights,diameter=0.22):
    """Emissive crossed quads, positioned in converter's validated FG axes."""
    valid=[light for light in lights if light.color is not None]
    if not valid:return 0
    colors=list(dict.fromkeys(light.color for light in valid))
    mats=[]
    for i, c in enumerate(colors):
        r,g,b=c
        mats.append(f'MATERIAL "xp_light_{i}" rgb {r:.4f} {g:.4f} {b:.4f} amb 0 0 0 emis {r:.4f} {g:.4f} {b:.4f} spec 0 0 0 shi 0 trans 0')
    vertices=[]; faces=[]
    for light in valid:
        x,y,z=xp_to_fg(*light.xyz)
        # Horizontal/vertical crossed rectangles: visible from approach and taxi perspective.
        size=light.size if light.size>0 else diameter
        h=size*.5
        for axis in ('x','z'):
            if axis=='x':quad=[(x-h,y-h,z),(x+h,y-h,z),(x+h,y+h,z),(x-h,y+h,z)]
            else:quad=[(x,y-h,z-h),(x,y-h,z+h),(x,y+h,z+h),(x,y+h,z-h)]
            start=len(vertices);vertices+=quad
            mid=colors.index(light.color)
            faces.extend([(mid,(start,start+1,start+2)),(mid,(start,start+2,start+3))])
    lines=['AC3Db',*mats,'OBJECT world','kids 1','OBJECT poly','name "XP_NightLights"',f'numvert {len(vertices)}']
    lines += [f'{x:.6f} {y:.6f} {z:.6f}' for x,y,z in vertices]
    lines.append(f'numsurf {len(faces)}')
    for mat,ids in faces:
        lines+=['SURF 0x30',f'mat {mat}','refs 3']
        lines += [f'{idx} 0 0' for idx in ids]
    lines.append('kids 0')
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return len(valid)


def night_condition(tag='greater-than'):
    cond=ET.Element('condition')
    comp=ET.SubElement(cond,tag)
    ET.SubElement(comp,'property').text='/sim/time/sun-angle-rad'
    ET.SubElement(comp,'value').text='1.50'
    return cond


def write_lights_xml(xml_path, ac_filename):
    root=ET.Element('PropertyList')
    ET.SubElement(root,'path').text=ac_filename
    anim=ET.SubElement(root,'animation')
    ET.SubElement(anim,'type').text='select'
    ET.SubElement(anim,'object-name').text='XP_NightLights'
    anim.append(night_condition())
    ET.indent(root,space='  ')
    ET.ElementTree(root).write(xml_path,encoding='utf-8',xml_declaration=True)


def write_lit_xml(xml_path, ac_filename, day_texture, night_texture):
    """Use supported FlightGear material animation to switch texture at night.

    Preserve day texture and geometry; night version is an emission texture rather
    than a physically correct additive lightmap (requires more FG shader work).
    """
    root=ET.Element('PropertyList')
    ET.SubElement(root,'path').text=ac_filename
    for night in (True,False):
        anim=ET.SubElement(root,'animation')
        ET.SubElement(anim,'type').text='material'
        ET.SubElement(anim,'object-name').text='converted_rigid'
        anim.append(night_condition('greater-than' if night else 'less-than-equals'))
        em=ET.SubElement(anim,'emission')
        for k in ('red','green','blue'):ET.SubElement(em,k).text='1' if night else '0'
        ET.SubElement(anim,'texture').text=night_texture if night else day_texture
    ET.indent(root,space='  ')
    ET.ElementTree(root).write(xml_path,encoding='utf-8',xml_declaration=True)
