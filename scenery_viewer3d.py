#!/usr/bin/env python3
"""Visual 3D AC3D/STG scenery viewer for XPlane2FlightGear.

Requires PySide6, PyOpenGL and Pillow. Does not read FlightGear BTG terrain.
"""
import argparse
import math
import sys
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QMainWindow, QDockWidget,
    QWidget, QVBoxLayout, QCheckBox, QLabel, QListWidget, QPushButton, QMessageBox)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtGui import QSurfaceFormat
from OpenGL.GL import *
from OpenGL.GLU import *
from PIL import Image

from scenery_inspector import parse_stg, read_apt


@dataclass
class Mesh:
    vertices: list
    faces: list  # (list of (vertex_index, u, v), material)
    texture: Path | None
    bounds: tuple


def load_ac3d(path):
    """Read static AC3D poly objects, including child transforms (loc/rot).

    AC3D surfaces use refs (index, u, v). Non polygonal line surfaces skipped.
    """
    lines = path.read_text(errors='replace').splitlines()
    if not lines or not lines[0].startswith('AC3D'):
        raise ValueError('not an AC3D file')
    result_v, result_f = [], []
    texture = None
    index = 1
    ident = ((1.,0.,0.), (0.,1.,0.), (0.,0.,1.))

    def matvec(m, v):
        return tuple(sum(m[r][k] * v[k] for k in range(3)) for r in range(3))

    def multiply(a,b):
        return tuple(tuple(sum(a[r][k]*b[k][c] for k in range(3)) for c in range(3)) for r in range(3))

    def node(parent_rot, parent_pos):
        nonlocal index, texture
        if index >= len(lines) or not lines[index].startswith('OBJECT '):
            raise ValueError('expected AC3D OBJECT')
        index += 1
        verts, faces = [], []
        loc = (0.,0.,0.); rot = ident
        while index < len(lines):
            line = lines[index].strip(); index += 1
            if not line: continue
            fields = line.split()
            if fields[0] == 'texture' and len(fields)>1:
                tex = path.parent / line.partition(' ')[2].strip().strip('"')
                if tex.is_file(): texture = tex
            elif fields[0] == 'loc' and len(fields)>=4:
                loc = tuple(map(float,fields[1:4]))
            elif fields[0] == 'rot' and len(fields)>=10:
                values = list(map(float,fields[1:10])); rot = tuple(tuple(values[i:i+3]) for i in (0,3,6))
            elif fields[0] == 'numvert':
                n = int(fields[1]); verts = [tuple(map(float,lines[index+j].split()[:3])) for j in range(n)]; index += n
            elif fields[0] == 'numsurf':
                count=int(fields[1])
                for _ in range(count):
                    surf_flag=0; ref=[]
                    while index < len(lines):
                        line2=lines[index].strip();index+=1
                        if line2.startswith('SURF '):surf_flag=int(line2.split()[1],0)
                        elif line2.startswith('refs '):
                            amount=int(line2.split()[1]); ref=[]
                            for _ in range(amount):
                                p=lines[index].split();index+=1
                                ref.append((int(p[0]),float(p[1]),float(p[2])))
                            break
                    if len(ref)>=3 and (surf_flag & 0xf)==0: faces.append(ref)
            elif fields[0] == 'kids':
                own_rot = multiply(parent_rot, rot)
                rotated_loc = matvec(parent_rot, loc)
                own_pos = tuple(parent_pos[i] + rotated_loc[i] for i in range(3))
                base = len(result_v)
                for v in verts:
                    moved=matvec(own_rot,v)
                    result_v.append(tuple(moved[i]+own_pos[i] for i in range(3)))
                for face in faces:
                    result_f.append([(base+i,u,v) for i,u,v in face if i<len(verts)])
                for _ in range(int(fields[1])):node(own_rot,own_pos)
                return
        raise ValueError('unfinished AC3D object')

    # MATERIAL lines, then OBJECT world
    while index < len(lines) and not lines[index].startswith('OBJECT '):index+=1
    node(ident,(0.,0.,0.))
    if not result_v or not result_f: raise ValueError('empty AC3D geometry')
    # Basic axis-aligned bound in local model space
    lo=tuple(min(v[i] for v in result_v) for i in range(3))
    hi=tuple(max(v[i] for v in result_v) for i in range(3))
    return Mesh(result_v,result_f,texture,(lo,hi))


def airport_runway_corners(a,b,width,project):
    ax,az=project(a[1],a[0]);bx,bz=project(b[1],b[0])
    dx=bx-ax; dz=bz-az; length=math.hypot(dx,dz)
    if length<1:return []
    nx=-dz/length*width/2;nz=dx/length*width/2
    return [(ax+nx,az+nz),(bx+nx,bz+nz),(bx-nx,bz-nz),(ax-nx,az-nz)]


class SceneGL(QOpenGLWidget):
    picked = Signal(int)
    def __init__(self, root, parent=None, axis_mode="legacy"):
        super().__init__(parent)
        self.root=root
        self.axis_mode=axis_mode
        self.placements=parse_stg(root)
        self.runways,self.pavements=read_apt(root/'AirportBuild'/'apt.dat')
        if not self.placements: raise ValueError('No STATIC STG placements found')
        self.lon0=sum(p.lon for p in self.placements)/len(self.placements)
        self.lat0=sum(p.lat for p in self.placements)/len(self.placements)
        self.em=111412.84*math.cos(math.radians(self.lat0))-93.5*math.cos(3*math.radians(self.lat0))
        self.nm=111132.92-559.82*math.cos(2*math.radians(self.lat0))
        self.meshes={};self.failures=[]
        self.instances=[]
        for idx,p in enumerate(self.placements):
            path=p.stg.parent/p.model
            if path not in self.meshes:
                try:self.meshes[path]=load_ac3d(path)
                except Exception as e:self.failures.append(f'{path.name}: {e}');self.meshes[path]=None
            if self.meshes[path] is not None:
                x,z=self.project(p.lon,p.lat)
                self.instances.append((idx,p,x,z,self.meshes[path]))
        self.texture_ids={}
        self.show_airport=True;self.show_models=True;self.show_wire=False
        self.selected=-1
        self.yaw=0.;self.pitch=75.;self.distance=2200.;self.tx=0.;self.tz=0.
        self.last=None
        self.setFocusPolicy(Qt.StrongFocus)
    def project(self,lon,lat):return ((lon-self.lon0)*self.em,-(lat-self.lat0)*self.nm)
    def initializeGL(self):
        glClearColor(.10,.16,.18,1)
        glEnable(GL_DEPTH_TEST);glEnable(GL_CULL_FACE);glCullFace(GL_BACK)
        glEnable(GL_TEXTURE_2D);glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_COLOR_MATERIAL)
        glEnable(GL_NORMALIZE)
        for mesh in self.meshes.values():
            if mesh is None or mesh.texture is None or mesh.texture in self.texture_ids:continue
            try:
                im=Image.open(mesh.texture).convert('RGBA').transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                # Avoid massive textures consuming many gigabytes of GPU RAM
                im.thumbnail((2048,2048))
                tid=glGenTextures(1);glBindTexture(GL_TEXTURE_2D,tid)
                glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_REPEAT)
                glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_REPEAT)
                glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,im.width,im.height,0,GL_RGBA,GL_UNSIGNED_BYTE,im.tobytes())
                self.texture_ids[mesh.texture]=tid
            except Exception as e:self.failures.append(f'texture {mesh.texture}: {e}')
    def resizeGL(self,w,h):glViewport(0,0,max(w,1),max(h,1))
    def camera(self):
        glMatrixMode(GL_PROJECTION);glLoadIdentity()
        gluPerspective(48., max(self.width(),1)/max(self.height(),1),1.,100000.)
        glMatrixMode(GL_MODELVIEW);glLoadIdentity()
        a=math.radians(self.pitch);b=math.radians(self.yaw)
        cx=self.tx+self.distance*math.cos(a)*math.sin(b)
        cy=self.distance*math.sin(a)
        cz=self.tz+self.distance*math.cos(a)*math.cos(b)
        gluLookAt(cx,cy,cz,self.tx,0.,self.tz,0.,1.,0.)
    def draw_airport(self):
        glDisable(GL_TEXTURE_2D)
        glColor3f(.22,.28,.27)
        glBegin(GL_QUADS)
        for x,z in [(-25000,-25000),(25000,-25000),(25000,25000),(-25000,25000)]:glVertex3f(x,-.3,z)
        glEnd()
        if self.show_airport:
            glColor3f(.36,.39,.40)
            for poly in self.pavements:
                if len(poly)<3:continue
                # Triangle fan is an approximate preview for potentially concave taxiways.
                glBegin(GL_TRIANGLE_FAN)
                for lat,lon in poly:
                    x,z=self.project(lon,lat);glVertex3f(x,-.10,z)
                glEnd()
            glColor3f(.68,.72,.73)
            for a,b,w in self.runways:
                corners=airport_runway_corners(a,b,w,self.project)
                if not corners:continue
                glBegin(GL_QUADS)
                for x,z in corners:glVertex3f(x,-.06,z)
                glEnd()
        glEnable(GL_TEXTURE_2D)
    def draw_model(self,mesh,highlight=False):
        tid=self.texture_ids.get(mesh.texture)
        if tid and not highlight:
            glEnable(GL_TEXTURE_2D);glBindTexture(GL_TEXTURE_2D,tid);glColor3f(1,1,1)
        else:
            glDisable(GL_TEXTURE_2D);glColor3f(1.,.67,.18) if highlight else glColor3f(.78,.80,.77)
        glBegin(GL_TRIANGLES)
        for face in mesh.faces:
            # AC3D polygons triangulated as fan for current static exporter (TRIS)
            for j in range(1,len(face)-1):
                for i in (0,j,j+1):
                    k,u,v=face[i]
                    glTexCoord2f(u,v)
                    glVertex3f(*mesh.vertices[k])
        glEnd()
        if highlight or self.show_wire:
            glDisable(GL_TEXTURE_2D)
            glColor3f(1,.45,.05) if highlight else glColor3f(.1,.1,.1)
            for face in mesh.faces:
                glBegin(GL_LINE_LOOP)
                for k,_,_ in face:glVertex3f(*mesh.vertices[k])
                glEnd()
    def paintGL(self):
        glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT)
        self.camera();self.draw_airport()
        if self.show_models:
            for idx,pl,x,z,mesh in self.instances:
                glPushMatrix();glTranslatef(x,pl.altitude,z)
                # STG heading is counterclockwise; OpenGL positive Y rotates CCW from above
                glRotatef(pl.heading,0,1,0)
                # Models exported by v0.3.6 are rotated +90 degrees around local Y
                # for FlightGear. Undo that rotation in this X-Plane-style preview,
                # whose horizontal world coordinates are East (+X), South (+Z).
                # For old unconverted assets, select the legacy mode on the CLI.
                if self.axis_mode == 'flightgear':
                    glRotatef(-90,0,1,0)
                self.draw_model(mesh,idx==self.selected)
                glPopMatrix()
        glBindTexture(GL_TEXTURE_2D,0)
    def mousePressEvent(self,e):
        self.last=e.position()
        if e.button()==Qt.LeftButton:
            # Select closest object whose projected origin is near cursor.
            self.makeCurrent();self.camera()
            model=glGetDoublev(GL_MODELVIEW_MATRIX)
            proj=glGetDoublev(GL_PROJECTION_MATRIX)
            viewport=glGetIntegerv(GL_VIEWPORT)
            candidate=(-1,24.*24.)
            for idx,p,x,z,_ in self.instances:
                sx,sy,depth=gluProject(x,p.altitude,z,model,proj,viewport)
                if depth<0 or depth>1:continue
                d=(sx-e.position().x())**2+(self.height()-sy-e.position().y())**2
                if d<candidate[1]:candidate=(idx,d)
            if candidate[0]>=0:
                self.selected=candidate[0];self.picked.emit(self.selected);self.update()
    def mouseMoveEvent(self,e):
        if self.last is None:return
        delta=e.position()-self.last;self.last=e.position()
        if e.buttons() & Qt.RightButton:
            self.yaw+=delta.x()*.35;self.pitch=max(8.,min(89.5,self.pitch+delta.y()*.3))
        elif e.buttons() & Qt.MiddleButton or e.buttons() & Qt.LeftButton:
            speed=self.distance/max(self.height(),1)*1.6
            theta=math.radians(self.yaw)
            self.tx+=(-delta.x()*math.cos(theta)+delta.y()*math.sin(theta))*speed
            self.tz+=(delta.x()*math.sin(theta)+delta.y()*math.cos(theta))*speed
        self.update()
    def mouseReleaseEvent(self,e):self.last=None
    def wheelEvent(self,e):self.distance=max(5.,min(40000.,self.distance*(.82 if e.angleDelta().y()>0 else 1.22)));self.update()
    def focus_object(self,index):
        for idx,p,x,z,mesh in self.instances:
            if idx==index:
                self.selected=idx;self.tx=x;self.tz=z
                lo,hi=mesh.bounds
                self.distance=max(35.,min(600.,math.hypot(hi[0]-lo[0],hi[2]-lo[2])*2.5))
                self.update();return
    def top_view(self):self.pitch=89.5;self.yaw=0.;self.distance=2300.;self.update()


class Window(QMainWindow):
    def __init__(self,root,axis_mode):
        super().__init__();self.resize(1500,900)
        self.setWindowTitle('XPlane2FlightGear - Scenery Viewer 3D v0.3.6')
        self.view=SceneGL(root, axis_mode=axis_mode);self.setCentralWidget(self.view)
        dock=QDockWidget('Objects / View',self);self.side_panel=QWidget(dock);layout=QVBoxLayout(self.side_panel)
        info=QLabel(f'{len(self.view.instances)} visible instances / {len(self.view.meshes)} model paths\n'
                    f'{len(self.view.failures)} load errors.\n3D preview: AC3D + STG + apt.dat. BTG terrain not loaded.')
        info.setWordWrap(True);layout.addWidget(info)
        self.details=QLabel('Click a model, or select it from the list.');self.details.setWordWrap(True);layout.addWidget(self.details)
        self.search=QListWidget();layout.addWidget(self.search,1)
        for idx,placement,x,z,mesh in self.view.instances:
            self.search.addItem(f'{idx:04d} | {placement.model} | {placement.lon:.6f}, {placement.lat:.6f}')
        self.indexes=[row[0] for row in self.view.instances]
        self.search.currentRowChanged.connect(lambda row:self.select_item(self.indexes[row]) if row>=0 else None)
        self.view.picked.connect(self.show_info)
        for label,attr in [('Display 3D models','show_models'),('Airport surfaces','show_airport'),('Wireframe outlines','show_wire')]:
            cb=QCheckBox(label);cb.setChecked(getattr(self.view,attr));cb.toggled.connect(lambda flag,a=attr:(setattr(self.view,a,flag),self.view.update()));layout.addWidget(cb)
        btn=QPushButton('Top view / reset');btn.clicked.connect(self.view.top_view);layout.addWidget(btn)
        note=QLabel('Left-drag: pan | Right-drag: orbit | Wheel: zoom | Click a model: select.\n'
                    'The runway and taxiway preview is flat; it is not a BTG terrain renderer.\n'
                    'Models keep their actual AC3D geometry and locally referenced textures.')
        note.setWordWrap(True);layout.addWidget(note)
        dock.setWidget(self.side_panel);self.addDockWidget(Qt.RightDockWidgetArea,dock)
        if self.view.failures:
            self.statusBar().showMessage('Some meshes were not loaded. See terminal for errors.')
            for msg in self.view.failures[:30]:print('WARN:',msg,file=sys.stderr)
    def select_item(self,idx):self.view.focus_object(idx);self.show_info(idx)
    def show_info(self,idx):
        p=self.view.placements[idx]
        self.details.setText(f'Model: {p.model}\nSTG: {p.stg.name} line {p.line+1}\n'
                             f'GPS: {p.lat:.8f}, {p.lon:.8f}\nHeading: {p.heading:.2f}\n'
                             f'Altitude: {p.altitude:.2f} ({p.keyword})')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--scenery',type=Path,default=Path.home()/'Desktop/TestAirport')
    ap.add_argument('--axis-mode', choices=['auto','legacy','flightgear'], default='auto',
                    help='Rendering of AC3D vertices: auto detects v0.3.6 conversion report')
    args=ap.parse_args();root=args.scenery.expanduser().resolve()
    axis_mode=args.axis_mode
    if axis_mode=='auto':
        try:
            import json
            report=json.loads((root/'report.json').read_text(encoding='utf-8'))
            axis_mode='flightgear' if report.get('axis_transform')=='xp_eus_to_fg_suw_v036' else 'legacy'
        except (OSError,ValueError):
            axis_mode='legacy'
    if not (root/'Objects').is_dir():ap.error(f'Missing Objects directory: {root}')
    fmt=QSurfaceFormat()
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
    QSurfaceFormat.setDefaultFormat(fmt)
    app=QApplication(sys.argv[:1]);window=Window(root,axis_mode);window.show();return app.exec()
if __name__=='__main__':sys.exit(main())
