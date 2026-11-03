#!/usr/bin/env python3
"""XPlane2FlightGear Scenery Inspector v0.3.4 (PySide6).

Overlay apt.dat runway/taxiway geometry with FlightGear STG placement points.
Apply a uniform east/north translation to existing STG entries with a backup.
No terrain BTG is altered. All coordinates stay georeferenced WGS84.
"""
import argparse
import datetime
import json
import math
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from PySide6.QtCore import Qt, QPointF, QRectF, Signal
    from PySide6.QtGui import QColor, QBrush, QPen, QPainter, QFont, QTransform
    from PySide6.QtWidgets import (QApplication, QMainWindow, QGraphicsView, QGraphicsScene,
        QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsLineItem, QGraphicsItem,
        QFileDialog, QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
        QPushButton, QDoubleSpinBox, QMessageBox, QCheckBox, QListWidget,
        QFormLayout, QGroupBox, QGraphicsSimpleTextItem)
    from PySide6.QtGui import QPainterPath
except ImportError as exc:
    raise SystemExit('PySide6 manquant : python3 -m pip install PySide6') from exc

@dataclass
class Placement:
    stg: Path
    line: int
    keyword: str
    model: str
    lon: float
    lat: float
    altitude: float
    heading: float
    extra: str = ''


def parse_stg(root):
    result = []
    for path in sorted((root / 'Objects').rglob('*.stg')):
        for i, raw in enumerate(path.read_text(errors='replace').splitlines()):
            p = raw.split()
            if len(p) >= 6 and p[0] in ('OBJECT_STATIC', 'OBJECT_STATIC_AGL', 'OBJECT_SHARED'):
                try:
                    result.append(Placement(path, i, p[0], p[1], float(p[2]), float(p[3]),
                                            float(p[4]), float(p[5]), ' '.join(p[6:])))
                except ValueError:
                    continue
    return result


def read_apt(path):
    """Extract runway 100/101 and visible taxiway polygon 110/111/112/113/114 records."""
    runways, pavements = [], []
    if not path or not path.is_file():
        return runways, pavements
    lines = path.read_text(encoding='utf-8-sig', errors='replace').splitlines()
    current = None
    for line in lines:
        p = line.strip().split()
        if not p: continue
        kind = p[0]
        if kind == '100' and len(p) >= 20:
            try:
                # X-Plane apt.dat 100: width surface shoulder smooth centerline edge lights...
                # runway endpoint 1 at fields 9 and 10; endpoint 2 at 18 and 19
                a = (float(p[9]), float(p[10])); b = (float(p[18]), float(p[19])); w = float(p[1]);
                if -90 < a[0] < 90 and -180 < a[1] < 180 and -90 < b[0] < 90 and -180 < b[1] < 180:
                    runways.append((a, b, w))
            except (ValueError, IndexError): pass
            current = None
        elif kind == '101' and len(p) >= 9:
            # Water runway: coordinate indices per apt.dat 101 record
            try:
                a = (float(p[4]), float(p[5])); b = (float(p[7]), float(p[8])); w = float(p[1]); runways.append((a,b,w))
            except (ValueError, IndexError): pass
            current = None
        elif kind == '110':
            current = []
        elif kind in ('111','112','113','114') and current is not None:
            try:
                lat, lon = float(p[1]), float(p[2])
                if -90 <= lat <= 90 and -180 <= lon <= 180: current.append((lat, lon))
                if kind in ('113','114'):
                    if len(current) >= 3: pavements.append(current)
                    current = None
            except (ValueError, IndexError): pass
        elif kind in ('1','16','17','99'):
            current = None
    return runways, pavements


def move_geodesic(lon, lat, east, north):
    """Small WGS84 displacement in meters (longitude scaled at latitude)."""
    phi = math.radians(lat)
    r1 = 111132.92 - 559.82 * math.cos(2*phi) + 1.175 * math.cos(4*phi)
    r2 = 111412.84 * math.cos(phi) - 93.5 * math.cos(3*phi) + 0.118 * math.cos(5*phi)
    if abs(r2) < 1: raise ValueError('Position trop proche du pole')
    return lon + east/r2, lat + north/r1


def bucket_index(lon, lat):
    # Keep in sync with xp2fg.bucket, without importing conversion dependencies.
    ay = abs(lat)
    span = next((s for cutoff, s in [(89,12),(86,4),(83,2),(76,1),(62,.5),(22,.25),(0,.125)] if ay >= cutoff), .125)
    by = math.floor(lat); lb = math.floor(lon/span)*span; bx = math.floor(lb)
    y = max(0,min(7,int((lat-by)*8))); x = int(round((lb-bx)*8))
    return ((bx+180)<<14)+((by+90)<<6)+(y<<3)+x


def apply_translation(root, placements, east, north):
    if not placements: raise ValueError('Aucun objet a corriger')
    by_path = {}
    for pl in placements:
        lon, lat = move_geodesic(pl.lon, pl.lat, east, north)
        if bucket_index(lon, lat) != int(pl.stg.stem):
            raise ValueError(f'Deplacement depassant une tuile STG ({pl.model}, {pl.stg.name}). '
                             'Cette version refuse une ecriture dans la mauvaise tuile.')
        by_path.setdefault(pl.stg, {})[pl.line] = (pl,lon,lat)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = root / 'InspectorBackups' / stamp
    for path in by_path:
        dest = backup / path.relative_to(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path,dest)
    # Read all first; write temp and replace after backup.
    for path, values in by_path.items():
        lines = path.read_text(errors='replace').splitlines(keepends=True)
        for lineno,(pl,lon,lat) in values.items():
            ending = '\n' if lines[lineno].endswith('\n') else ''
            lines[lineno] = f'{pl.keyword} {pl.model} {lon:.8f} {lat:.8f} {pl.altitude:.2f} {pl.heading:.3f}' + (f' {pl.extra}' if pl.extra else '') + ending
        temp = path.with_name(path.name+'.inspector-tmp')
        temp.write_text(''.join(lines),encoding='utf-8')
        temp.replace(path)
    manifest = {'version':'0.3.4','east_m':east,'north_m':north,'placements':len(placements), 'files':len(by_path),'backup':str(backup)}
    (backup/'translation.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest


class MapView(QGraphicsView):
    selectedObject = Signal(object)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setViewportUpdateMode(QGraphicsView.BoundingRectViewportUpdate)
        self.setBackgroundBrush(QColor('#1e2b30'))
        self.setScene(QGraphicsScene(self))
        self.points = []
        self.scale_factor = 1.0
        self.object_rect = None
    def wheelEvent(self,event):
        self.scale(1.20 if event.angleDelta().y()>0 else 1/1.20,1.20 if event.angleDelta().y()>0 else 1/1.20)
    def fit_all(self):
        rect=self.object_rect if self.object_rect is not None else self.scene().itemsBoundingRect()
        if rect.isValid():
            pad=max(rect.width(),rect.height())*.08+30
            self.fitInView(rect.adjusted(-pad,-pad,pad,pad),Qt.KeepAspectRatio)
    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:
            item=self.itemAt(event.position().toPoint())
            if item is not None:
                data=item.data(0)
                if isinstance(data,Placement): self.selectedObject.emit(data)
        super().mousePressEvent(event)


class Inspector(QMainWindow):
    def __init__(self, root, apt=None):
        super().__init__()
        self.root=Path(root).expanduser().resolve()
        self.apt=Path(apt).expanduser() if apt else self.root/'AirportBuild'/'apt.dat'
        self.placements=[]; self.runways=[]; self.pavements=[]; self.markers=[]
        self.setWindowTitle('XPlane2FlightGear — Scenery Inspector v0.3.4')
        self.resize(1250,850)
        self.map=MapView(self); self.setCentralWidget(self.map)
        self.map.selectedObject.connect(self.select_placement)
        dock=QDockWidget('Inspection / alignement',self)
        dock.setFeatures(QDockWidget.DockWidgetMovable|QDockWidget.DockWidgetFloatable)
        pane=QWidget(); layout=QVBoxLayout(pane)
        self.status=QLabel(); self.status.setWordWrap(True);layout.addWidget(self.status)
        self.detail=QLabel('Cliquer sur un point pour inspecter un objet.');self.detail.setWordWrap(True);layout.addWidget(self.detail)
        self.show_objs=QCheckBox('Afficher les objets');self.show_objs.setChecked(True);layout.addWidget(self.show_objs)
        self.show_apts=QCheckBox('Afficher pistes et taxiways');self.show_apts.setChecked(True);layout.addWidget(self.show_apts)
        group=QGroupBox('Translation globale (metres)')
        form=QFormLayout(group)
        self.east=QDoubleSpinBox();self.north=QDoubleSpinBox()
        for w in (self.east,self.north):w.setRange(-5000,5000);w.setDecimals(2);w.setSingleStep(1.0)
        form.addRow('Est (+) / Ouest (-)',self.east)
        form.addRow('Nord (+) / Sud (-)',self.north)
        layout.addWidget(group)
        self.show_objs.toggled.connect(self.render_map);self.show_apts.toggled.connect(self.render_map)
        self.east.valueChanged.connect(self.render_map);self.north.valueChanged.connect(self.render_map)
        btn=QPushButton('Appliquer la correction aux STG'); btn.clicked.connect(self.commit);layout.addWidget(btn)
        re=QPushButton('Recharger les fichiers');re.clicked.connect(self.reload);layout.addWidget(re)
        fit=QPushButton('Cadrer les objets');fit.clicked.connect(self.map.fit_all);layout.addWidget(fit)
        load=QPushButton('Choisir un autre apt.dat');load.clicked.connect(self.choose_apt);layout.addWidget(load)
        note=QLabel('Couleurs : pistes = blanc, taxiways = gris, objets originaux = cyan, positions corrigees = orange.\n'
                   'Le glissement est un apercu : les fichiers ne changent que lorsque tu cliques sur Appliquer.\n'
                   'Une sauvegarde des STG est creee automatiquement.')
        note.setWordWrap(True); layout.addWidget(note);layout.addStretch()
        dock.setWidget(pane);self.addDockWidget(Qt.RightDockWidgetArea,dock)
        self.reload()
    def reload(self):
        self.placements=parse_stg(self.root)
        self.runways,self.pavements=read_apt(self.apt)
        self.status.setText(f'{len(self.placements)} objets · {len(self.runways)} pistes · {len(self.pavements)} surfaces taxiway\n{self.root}')
        self.render_map();self.map.fit_all()
    def choose_apt(self):
        result,_=QFileDialog.getOpenFileName(self,'Choisir apt.dat',str(self.apt),'apt.dat (*.dat);;Tous (*)')
        if result:self.apt=Path(result);self.reload()
    def select_placement(self,pl):
        self.detail.setText(f'Modele : {pl.model}\nLongitude : {pl.lon:.8f}\nLatitude : {pl.lat:.8f}\n'
                            f'Altitude : {pl.altitude:.2f} m ({pl.keyword})\nCap : {pl.heading:.3f}°\n{pl.stg.name}:{pl.line+1}')
    def render_map(self):
        scene=self.map.scene();scene.clear();self.map.object_rect=None
        geographic=[(p.lon,p.lat) for p in self.placements]
        for a,b,_ in self.runways: geographic.extend([(a[1],a[0]),(b[1],b[0])])
        for poly in self.pavements: geographic.extend((lon,lat) for lat,lon in poly)
        if not geographic: return
        lon0=sum(p[0] for p in geographic)/len(geographic);lat0=sum(p[1] for p in geographic)/len(geographic)
        east_per_deg=111412.84*math.cos(math.radians(lat0))-93.5*math.cos(3*math.radians(lat0))
        north_per_deg=111132.92-559.82*math.cos(2*math.radians(lat0))
        def xy(lon,lat):return QPointF((lon-lon0)*east_per_deg,-(lat-lat0)*north_per_deg)
        if self.show_apts.isChecked():
            for poly in self.pavements:
                if len(poly)<3:continue
                path=QPainterPath(xy(poly[0][1],poly[0][0]))
                for lat,lon in poly[1:]:path.lineTo(xy(lon,lat))
                path.closeSubpath()
                scene.addPath(path,QPen(QColor('#81959c'),1),QBrush(QColor(99,116,121,95)))
            for a,b,width in self.runways:
                pa,pb=xy(a[1],a[0]),xy(b[1],b[0]);pen=QPen(QColor('#e9eeee'),max(3,width));pen.setCapStyle(Qt.FlatCap)
                scene.addLine(pa.x(),pa.y(),pb.x(),pb.y(),pen)
                center=QPen(QColor('#535c62'),1,Qt.DashLine)
                scene.addLine(pa.x(),pa.y(),pb.x(),pb.y(),center)
        if self.placements:
            object_positions=[xy(pl.lon,pl.lat) for pl in self.placements]
            minx=min(pt.x() for pt in object_positions);maxx=max(pt.x() for pt in object_positions)
            miny=min(pt.y() for pt in object_positions);maxy=max(pt.y() for pt in object_positions)
            self.map.object_rect=QRectF(minx,miny,max(maxx-minx,1),max(maxy-miny,1))
        if self.show_objs.isChecked():
            for pl in self.placements:
                p=xy(pl.lon,pl.lat)
                item=scene.addEllipse(-4,-4,8,8,QPen(QColor('#072832'),1),QBrush(QColor('#54dafa')))
                item.setFlag(QGraphicsItem.ItemIgnoresTransformations,True)
                item.setPos(p);item.setData(0,pl);item.setZValue(5)
                if self.east.value() or self.north.value():
                    q=QPointF(p.x()+self.east.value(),p.y()-self.north.value())
                    ghost=scene.addEllipse(-4,-4,8,8,QPen(QColor('#5b350a'),1),QBrush(QColor('#ffb65f')))
                    ghost.setFlag(QGraphicsItem.ItemIgnoresTransformations,True)
                    ghost.setPos(q);ghost.setData(0,pl);ghost.setZValue(6)
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-100,-100,100,100))
    def commit(self):
        east,north=self.east.value(),self.north.value()
        if east==0 and north==0:
            QMessageBox.information(self,'Aucune correction','Indique une translation en metres.');return
        choice=QMessageBox.question(self,'Confirmer',f'Deplacer {len(self.placements)} objets de {east:+.2f} m est / {north:+.2f} m nord ?\nUne sauvegarde sera creee.',QMessageBox.Yes|QMessageBox.No)
        if choice!=QMessageBox.Yes:return
        try:
            manifest=apply_translation(self.root,self.placements,east,north)
        except Exception as exc:
            QMessageBox.critical(self,'Translation impossible',str(exc));return
        self.east.setValue(0);self.north.setValue(0);self.reload()
        QMessageBox.information(self,'STG mis a jour',f'{manifest["placements"]} objets corriges.\nSauvegarde : {manifest["backup"]}\nRedemarrer FlightGear pour tester.')


def main():
    parser=argparse.ArgumentParser(description='Visual inspector for XPlane2FlightGear generated scenery')
    parser.add_argument('--scenery',type=Path,default=Path.home()/'Desktop/TestAirport')
    parser.add_argument('--apt',type=Path,help='apt.dat extracted from source airport')
    args=parser.parse_args()
    if not (args.scenery/'Objects').is_dir():parser.error(f'Objects directory missing: {args.scenery / "Objects"}')
    app=QApplication(sys.argv[:1]); win=Inspector(args.scenery,args.apt);win.show();return app.exec()
if __name__=='__main__':sys.exit(main())
