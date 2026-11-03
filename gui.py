#!/usr/bin/env python3
"""XPlane2FlightGear Desktop v0.3.14.6.

The verified v0.3.11 conversion engine is kept unchanged.
"""
import json
import os
from pathlib import Path
import shutil
import sys

try:
    from PySide6.QtCore import QProcess, QProcessEnvironment, QSettings, Qt, QTimer
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout,
        QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
        QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
        QSpinBox, QDoubleSpinBox, QTabWidget, QVBoxLayout, QWidget, QListWidget,
    )
except ImportError as exc:
    raise SystemExit('PySide6 manquant : python3 -m pip install PySide6') from exc

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
DEFAULT_SOURCE = ''
DEFAULT_OUT = str(HOME / 'Desktop' / 'FlightGear-Converted')
DEFAULT_TG = str(HOME / 'Downloads' / 'TerraGear-macOS-arm64' / 'terragear-env')
DEFAULT_DSF = str(HOME / 'Downloads' / 'xptools_mac_24-5' / 'tools' / 'DSFTool')


def pathfield(initial='', directory=True):
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(0, 0, 0, 0)
    edit = QLineEdit(initial)
    edit.setClearButtonEnabled(True)
    browse = QPushButton('Parcourir…')
    browse.setToolTip('Choisir un dossier' if directory else 'Choisir un fichier')
    def choose():
        previous = str(Path(edit.text()).expanduser()) if edit.text() else str(HOME)
        selected = (QFileDialog.getExistingDirectory(widget, 'Choisir un dossier', previous)
                    if directory else QFileDialog.getOpenFileName(widget, 'Choisir un fichier', previous)[0])
        if selected:
            edit.setText(selected)
    browse.clicked.connect(choose)
    row.addWidget(edit, 1)
    row.addWidget(browse)
    return widget, edit


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('XPlane2FlightGear — v0.3.14.6')
        self.resize(1040, 760)
        self.settings = QSettings('XPlane2FlightGear', 'Desktop')
        self.proc = None
        self.tool_processes = []

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(20, 16, 20, 14)
        outer.setSpacing(11)
        title = QLabel('XPlane2FlightGear')
        title.setStyleSheet('font-size: 24px; font-weight: 700;')
        outer.addWidget(title)
        subtitle = QLabel('Conversion X-Plane → FlightGear  ·  moteur stable v0.3.11')
        subtitle.setStyleSheet('color: #8d969e;')
        outer.addWidget(subtitle)

        self.setCentralWidget(central)
        tabs = QTabWidget()
        outer.addWidget(tabs, 1)
        # Keep QScrollArea as the tab's content, so all controls stay usable
        # on smaller Mac screens and when system font scaling is increased.
        convert = QScrollArea()
        convert.setWidgetResizable(True)
        convert.setFrameShape(QScrollArea.NoFrame)
        convert.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        convert_content = QWidget()
        convert.setWidget(convert_content)
        c = QVBoxLayout(convert_content)
        c.setContentsMargins(8, 8, 14, 16)
        c.setSpacing(12)
        files = QGroupBox('Scène et destination')
        f = QFormLayout(files)
        self.source_row, self.source = pathfield(self.load('source', DEFAULT_SOURCE))
        self.output_row, self.output = pathfield(self.load('output', DEFAULT_OUT))
        f.addRow('Scenery X-Plane :', self.source_row)
        f.addRow('Sortie FlightGear :', self.output_row)
        c.addWidget(files)
        self.icao = QComboBox()
        self.icao.setEditable(True)
        self.icao.setMinimumWidth(180)
        self.icao.addItem('')
        self.icao.setCurrentText(self.load('icao',''))
        self.icao.setToolTip('Choisir le code ICAO figurant dans apt.dat. Un seul aéroport est généré à la fois.')
        refresh = QPushButton('Détecter les aéroports dans apt.dat')
        refresh.clicked.connect(self.detect_airports)
        airport_row = QHBoxLayout()
        airport_row.addWidget(self.icao,1)
        airport_row.addWidget(refresh)
        c.addLayout(airport_row)

        bounds_group = QGroupBox('Emprise TerraGear (Airport Boundary WED facultative)')
        bounds_form = QFormLayout(bounds_group)
        self.bounds_mode = QComboBox()
        self.bounds_mode.addItem('Automatique depuis les pistes et aires apt.dat', 'auto')
        self.bounds_mode.addItem('Manuelle : bornes des tuiles en degrés', 'manual')
        if self.load('bounds_mode', 'auto') == 'manual':
            self.bounds_mode.setCurrentIndex(1)
        bounds_form.addRow('Méthode :', self.bounds_mode)
        self.margin = QDoubleSpinBox()
        self.margin.setRange(0.0, 1.0)
        self.margin.setDecimals(3)
        self.margin.setSingleStep(.01)
        self.margin.setValue(float(self.load('margin', '0.05')))
        self.margin.setSuffix(' °')
        bounds_form.addRow('Marge automatique :', self.margin)
        self.bound_edits = {}
        manual_row = QWidget()
        manual_layout = QHBoxLayout(manual_row)
        manual_layout.setContentsMargins(0,0,0,0)
        for key, label, default in [('west','Ouest','5'),('east','Est','6'),('south','Sud','45'),('north','Nord','46')]:
            manual_layout.addWidget(QLabel(label))
            edit = QSpinBox()
            edit.setRange(-180 if key in ('west','east') else -90, 180 if key in ('west','east') else 90)
            edit.setValue(int(self.load('bounds_'+key, default)))
            edit.setToolTip('Degrés entiers, limites des tuiles TerraGear / SRTM')
            manual_layout.addWidget(edit)
            self.bound_edits[key] = edit
        bounds_form.addRow('W E S N :', manual_row)
        self.manual_bounds_row = manual_row
        preview = QPushButton('Vérifier les bornes et les tuiles')
        preview.clicked.connect(self.preview_bounds)
        bounds_form.addRow(preview)
        c.addWidget(bounds_group)
        self.bounds_mode.currentIndexChanged.connect(self.update_bounds_mode)
        self.update_bounds_mode()

        tools = QGroupBox('Outils et données')
        f2 = QFormLayout(tools)
        self.terra_row, self.terra = pathfield(self.load('terra', DEFAULT_TG), False)
        self.dsf_row, self.dsf = pathfield(self.load('dsf', DEFAULT_DSF), False)
        self.hgt_row, self.hgt = pathfield(self.load('hgt', ''), False)
        self.prio_row, self.prio = pathfield(self.load('prio', ''), False)
        f2.addRow('TerraGear (terragear-env) :', self.terra_row)
        f2.addRow('DSFTool :', self.dsf_row)
        self.xplane_row, self.xplane_root = pathfield(self.load('xplane_root', ''))
        xplane_tip = (
            'Sélectionner le dossier RACINE de X-Plane 12, pas un dossier nommé Libraries.\n'
            'Exemple macOS : /Volumes/Sans titre/X-Plane 12\n\n'
            'Bibliothèques fournies avec X-Plane :\n'
            '  Resources/default scenery/ (packages avec library.txt)\n'
            'Bibliothèques tierces installées dans X-Plane :\n'
            '  Custom Scenery/<nom du package>/library.txt\n\n'
            'Le programme lit les directives EXPORT des library.txt : lib/... est une clé virtuelle, pas un dossier physique.\n'
            'X-Plane est facultatif : laisser vide sans installation.'
        )
        self.xplane_root.setToolTip(xplane_tip)
        self.xplane_root.setPlaceholderText('Ex. /Volumes/Sans titre/X-Plane 12 (facultatif)')
        self.xplane_row.setToolTip(xplane_tip)
        # The browse button and label also show the hint.
        for browse_button in self.xplane_row.findChildren(QPushButton):
            browse_button.setToolTip(xplane_tip)
        xplane_label = QLabel('Bibliothèque X-Plane (racine, facultative) :')
        xplane_label.setToolTip(xplane_tip)
        self.auto_libraries = QCheckBox('Rechercher les bibliothèques si X-Plane est installé (facultatif)')
        self.auto_libraries.setChecked(self.load('auto_libraries', 'true') == 'true')
        f2.addRow(xplane_label, self.xplane_row)
        f2.addRow(self.auto_libraries)
        # Independent libraries: support several packages or parent folders.
        lib_container = QWidget()
        lib_layout = QVBoxLayout(lib_container)
        lib_layout.setContentsMargins(0, 0, 0, 0)
        self.other_libraries = QListWidget()
        self.other_libraries.setMinimumHeight(106)
        self.other_libraries.setAlternatingRowColors(True)
        self.other_libraries.setToolTip('Un dossier par ligne : package contenant library.txt ou dossier regroupant plusieurs bibliothèques.')
        try:
            saved_libraries = json.loads(self.load('other_libraries', '[]'))
            if isinstance(saved_libraries, list):
                for lib_path in saved_libraries:
                    if isinstance(lib_path, str) and lib_path.strip():
                        self.other_libraries.addItem(lib_path)
        except (ValueError, TypeError):
            pass
        library_buttons = QHBoxLayout()
        add_library = QPushButton('Ajouter un dossier…')
        remove_library = QPushButton('Retirer la sélection')
        add_library.clicked.connect(self.add_other_library)
        remove_library.clicked.connect(self.remove_other_library)
        library_buttons.addWidget(add_library)
        library_buttons.addWidget(remove_library)
        library_buttons.addStretch()
        lib_layout.addWidget(self.other_libraries)
        lib_layout.addLayout(library_buttons)
        lib_hint = QLabel('Plusieurs dossiers possibles, indépendants de X-Plane. Les sous-dossiers avec library.txt sont détectés.')
        lib_hint.setWordWrap(True)
        lib_layout.addWidget(lib_hint)
        f2.addRow('Bibliothèques autres (facultatives) :', lib_container)
        self.other_libraries.model().rowsInserted.connect(lambda *_: self.store_other_libraries())
        self.other_libraries.model().rowsRemoved.connect(lambda *_: self.store_other_libraries())
        f2.addRow('HGT local (facultatif) :', self.hgt_row)
        f2.addRow('default_priorities.txt (facultatif) :', self.prio_row)
        c.addWidget(tools)

        options = QGroupBox('Options')
        fo = QFormLayout(options)
        self.clearance = QDoubleSpinBox()
        self.clearance.setDecimals(2)
        self.clearance.setRange(-2.0, 5.0)
        self.clearance.setSingleStep(.05)
        self.clearance.setSuffix(' m')
        self.clearance.setValue(float(self.load('clearance', '0')))
        self.edge = QDoubleSpinBox()
        self.edge.setDecimals(0)
        self.edge.setRange(5, 100)
        self.edge.setValue(float(self.load('edge', '25')))
        self.edge.setSuffix(' m')
        self.overwrite = QCheckBox('Autoriser explicitement la suppression du dossier de sortie existant')
        self.overwrite.setChecked(False)
        fo.addRow('Décalage vertical des objets :', self.clearance)
        fo.addRow('Subdivision drapage (arête max.) :', self.edge)
        fo.addRow(self.overwrite)
        c.addWidget(options)

        self.notice = QLabel('Choisissez un scenery avec apt.dat et sélectionnez son aéroport. Les bornes et tuiles SRTM seront calculées automatiquement.')
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet('color: #d5a448; font-weight: 600;')
        c.addWidget(self.notice)
        buttons = QHBoxLayout()
        self.start = QPushButton('▶  Convertir la scène complète')
        self.start.setMinimumHeight(43)
        self.start.clicked.connect(self.start_build)
        self.cancel = QPushButton('■  Arrêter')
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.stop_build)
        buttons.addWidget(self.start, 1)
        buttons.addWidget(self.cancel)
        c.addLayout(buttons)
        c.addStretch(1)
        tabs.addTab(convert, 'Conversion')

        viewers = QWidget()
        v = QVBoxLayout(viewers)
        v.addWidget(QLabel('Les utilitaires utilisent le dossier de sortie indiqué dans l’onglet Conversion.'))
        inspector = QPushButton('Ouvrir Scenery Inspector (2D)')
        inspector.setMinimumHeight(52)
        inspector.clicked.connect(lambda: self.launch_utility('scenery_inspector.py'))
        viewer3d = QPushButton('Ouvrir Scenery Viewer 3D')
        viewer3d.setMinimumHeight(52)
        viewer3d.clicked.connect(lambda: self.launch_utility('scenery_viewer3d.py'))
        openfolder = QPushButton('Ouvrir le dossier de sortie')
        openfolder.clicked.connect(self.open_output)
        v.addWidget(inspector)
        v.addWidget(viewer3d)
        v.addWidget(openfolder)
        v.addStretch()
        tabs.addTab(viewers, 'Outils de visualisation')

        log_tab = QWidget()
        logs = QVBoxLayout(log_tab)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont('Menlo')
        mono.setStyleHint(QFont.Monospace)
        self.log.setFont(mono)
        logs.addWidget(self.log)
        copy = QPushButton('Copier le journal')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.log.toPlainText()))
        logs.addWidget(copy)
        tabs.addTab(log_tab, 'Journal')
        self.tabs = tabs
        self.statusBar().showMessage('Prêt · conversion aéroport générique')
        # Capture values while widgets are alive. On macOS, closeEvent can be
        # invoked after native Qt children have already been destroyed.
        # Never access the QLineEdit C++ wrappers from closeEvent().
        self._cached_settings = {'auto_libraries': 'true' if self.auto_libraries.isChecked() else 'false', 'icao':self.selected_icao(), 'bounds_mode':self.bounds_mode.currentData(), 'margin':self.margin.value()}
        for key, widget in [
            ('source', self.source), ('output', self.output),
            ('terra', self.terra), ('dsf', self.dsf),
            ('hgt', self.hgt), ('prio', self.prio), ('xplane_root', self.xplane_root),
        ]:
            self._cached_settings[key] = widget.text()
            widget.textChanged.connect(
                lambda value, setting_key=key: self.cache_setting(setting_key, value)
            )
        for key, widget in [('clearance', self.clearance), ('edge', self.edge)]:
            self._cached_settings[key] = widget.value()
            widget.valueChanged.connect(
                lambda value, setting_key=key: self.cache_setting(setting_key, value)
            )
        self.bounds_mode.currentIndexChanged.connect(lambda _: self.cache_setting('bounds_mode',self.bounds_mode.currentData()))
        self.margin.valueChanged.connect(lambda v: self.cache_setting('margin',v))
        for key, edit in self.bound_edits.items():
            edit.valueChanged.connect(lambda value, setting_key=key: self.cache_setting('bounds_'+setting_key,value))
        self.auto_libraries.toggled.connect(lambda value: self.cache_setting('auto_libraries', 'true' if value else 'false'))
        self.source.textChanged.connect(self.update_notice)
        self.icao.currentTextChanged.connect(lambda v: self.cache_setting('icao',self.selected_icao()))
        self.update_notice()

    def library_paths(self):
        return [self.other_libraries.item(i).text() for i in range(self.other_libraries.count())]

    def store_other_libraries(self):
        self.cache_setting('other_libraries', json.dumps(self.library_paths(), ensure_ascii=False))

    def add_other_library(self):
        directory = QFileDialog.getExistingDirectory(self, 'Ajouter une bibliothèque X-Plane tierce', str(HOME))
        if not directory:
            return
        resolved = str(Path(directory).expanduser().resolve())
        if resolved not in self.library_paths():
            self.other_libraries.addItem(resolved)

    def remove_other_library(self):
        for item in self.other_libraries.selectedItems():
            self.other_libraries.takeItem(self.other_libraries.row(item))

    def load(self, key, default):
        value = self.settings.value(key, default)
        return str(value)

    def cache_setting(self, key, value):
        """Update stored value from Qt signals while the widget is alive."""
        self._cached_settings[key] = value
        self.settings.setValue(key, value)

    def save(self):
        """Persist cached values without dereferencing potentially deleted widgets."""
        for key, value in self._cached_settings.items():
            self.settings.setValue(key, value)
        self.settings.sync()

    def detect_airports(self):
        from airport import parse_airports, find_apt
        src=Path(self.source.text()).expanduser()
        apt=find_apt(src)
        if not apt:
            QMessageBox.warning(self,'apt.dat absent','Aucun apt.dat dans cette scène X-Plane.')
            return
        try:
            version,airports=parse_airports(apt)
        except Exception as exc:
            QMessageBox.warning(self,'Erreur apt.dat',str(exc))
            return
        old=self.icao.currentText()
        self.icao.clear()
        self.icao.addItem('')
        for airport in airports:
            self.icao.addItem(f"{airport['icao']} — {airport['name']}",airport['icao'])
        if len(airports)==1:self.icao.setCurrentIndex(1)
        elif old:
            for i in range(self.icao.count()):
                if self.icao.itemData(i)==old:self.icao.setCurrentIndex(i);break
        self.notice.setText(f"apt.dat v{version}: {len(airports)} aéroport(s). Sélectionner un aéroport pour la construction TerraGear.")

    def selected_icao(self):
        current=self.icao.currentData()
        if current:return str(current)
        return self.icao.currentText().split(' — ')[0].strip().upper()

    def update_notice(self):
        src=Path(self.source.text()).expanduser()
        apt=src/'Earth nav data'/'apt.dat'
        if apt.is_file():
            self.notice.setText('apt.dat trouvé. Boundary WED non nécessaire : limites automatiques depuis pistes/aires ou saisie manuelle.')
        else:
            self.notice.setText('Aucun apt.dat détecté : sélectionner un package contenant Earth nav data/apt.dat.')

    def append(self, message):
        self.log.appendPlainText(message.rstrip('\n'))
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def update_bounds_mode(self):
        manual = self.bounds_mode.currentData() == 'manual'
        self.manual_bounds_row.setEnabled(manual)
        self.margin.setEnabled(not manual)

    def chosen_bounds(self):
        if self.bounds_mode.currentData() != 'manual':
            return None
        return [self.bound_edits[k].value() for k in ('west','east','south','north')]

    def preview_bounds(self):
        from build_airport import selection, tile_name
        from terrain import airport_coordinate_diagnostics
        try:
            airport, bounds, tiles = selection(
                Path(self.source.text()).expanduser(),
                self.selected_icao() or None,
                self.chosen_bounds(),
                margin=self.margin.value())
            examples = ', '.join(tile_name(lat,lon) for lat,lon in tiles[:16])
            note = ''
            if self.chosen_bounds() is None:
                details = airport_coordinate_diagnostics(airport, self.margin.value())
                note = f" | {details['points_ignored']} coordonnee(s) eloignee(s) ignoree(s)"
            self.notice.setText(
                f"{airport['icao']} | Bornes W E S N = {bounds} | {len(tiles)} tuile(s) SRTM : {examples}{note}")
        except Exception as exc:
            QMessageBox.warning(self, 'Bornes invalides', str(exc))

    def validate(self):
        src = Path(self.source.text()).expanduser()
        out = Path(self.output.text()).expanduser()
        tg = Path(self.terra.text()).expanduser()
        dsf = Path(self.dsf.text()).expanduser()
        if not (src/'Earth nav data'/'apt.dat').is_file():
            raise ValueError('Le scenery choisi ne contient pas Earth nav data/apt.dat.')
        if not tg.is_file():
            raise ValueError('Exécutable terragear-env introuvable.')
        if not dsf.is_file():
            raise ValueError('DSFTool introuvable.')
        if out.exists() and not self.overwrite.isChecked():
            raise ValueError('La sortie existe déjà. Choisir un dossier neuf, ou cocher explicitement l’écrasement.')
        for label, value in [('HGT', self.hgt.text()), ('Priorités', self.prio.text())]:
            if value and not Path(value).expanduser().is_file():
                raise ValueError(f'{label} introuvable : {value}')
        for library in self.library_paths():
            if not Path(library).expanduser().is_dir():
                raise ValueError(f'Bibliothèque supplémentaire introuvable : {library}')
        if self.xplane_root.text().strip() and not Path(self.xplane_root.text().strip()).expanduser().is_dir():
            raise ValueError('Dossier de la bibliothèque X-Plane introuvable.')
        from build_airport import selection
        try:
            selection(src, self.selected_icao() or None, self.chosen_bounds(), margin=self.margin.value())
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        # Protect the input, the user home, volume root, and installed X-Plane tree.
        s = src.resolve()
        o = out.resolve()
        protected = [s, s.parent, HOME.resolve(), Path('/').resolve()]
        if o in protected or o == s or o in s.parents or s in o.parents or o == Path('/Volumes') or o == Path('/Users'):
            raise ValueError('Destination dangereuse : choisir un dossier de sortie indépendant du scenery source.')
        if out.exists() and self.overwrite.isChecked():
            answer = QMessageBox.warning(self, 'Suppression de la destination',
                'ATTENTION : le dossier suivant et tout son contenu seront supprimés par le constructeur :\n\n'
                f'{out}\n\nConfirmer cette opération ?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return None
        return src, out, tg, dsf

    def start_build(self):
        if self.proc is not None:
            return
        try:
            result = self.validate()
        except ValueError as exc:
            QMessageBox.warning(self, 'Vérification des chemins', str(exc))
            return
        if result is None:
            return
        src, out, tg, dsf = result
        self.save()
        args = [str(ROOT/'build_all.py'), '--icao',self.selected_icao(), '--source', str(src), '--output', str(out),
                '--terragear-env', str(tg), '--dsftool', str(dsf), '--elevation', 'apply',
                '--elevation-clearance', str(self.clearance.value()), '--drape-edge', str(self.edge.value())]
        if self.chosen_bounds() is not None:
            args += ['--bounds',*map(str,self.chosen_bounds())]
        else:
            args += ['--margin',str(self.margin.value())]
        if self.overwrite.isChecked():
            args.append('--overwrite-output')
        if self.hgt.text().strip():
            args += ['--hgt', str(Path(self.hgt.text().strip()).expanduser())]
        if self.prio.text().strip():
            args += ['--priorities', str(Path(self.prio.text().strip()).expanduser())]
        if self.xplane_root.text().strip():
            args += ['--xplane-root', str(Path(self.xplane_root.text().strip()).expanduser())]
        if not self.auto_libraries.isChecked():
            args.append('--no-auto-libraries')
        for package in self.library_paths():
            args += ['--library', str(Path(package).expanduser())]
        self.log.clear()
        self.append('Moteur OBJ8 v0.3.11 · terrain TerraGear générique · reconstruction complète')
        self.append('Commande : ' + ' '.join([sys.executable, *args]) + '\n')
        self.tabs.setCurrentIndex(2)
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(str(ROOT))
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONUNBUFFERED', '1')
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.read_output)
        self.proc.errorOccurred.connect(self.process_error)
        self.proc.finished.connect(self.finished)
        self.start.setEnabled(False)
        self.cancel.setEnabled(True)
        self.statusBar().showMessage('Conversion en cours…')
        self.proc.start(sys.executable, args)

    def read_output(self):
        if self.proc is not None:
            data = bytes(self.proc.readAllStandardOutput())
            if data:
                self.append(data.decode('utf-8', errors='replace'))

    def process_error(self, err):
        if self.proc is not None:
            self.append('Erreur de lancement du processus : ' + self.proc.errorString())

    def finished(self, exitcode, status):
        self.read_output()
        ok = exitcode == 0 and status == QProcess.NormalExit
        self.append('\n' + ('Conversion terminée avec succès.' if ok else f'Conversion interrompue / échouée (code {exitcode}).'))
        self.statusBar().showMessage('Conversion terminée' if ok else 'Échec ou interruption')
        self.proc.deleteLater()
        self.proc = None
        self.start.setEnabled(True)
        self.cancel.setEnabled(False)

    def stop_build(self):
        if self.proc is None:
            return
        reply = QMessageBox.question(self, 'Interrompre la conversion',
            'Interrompre le constructeur ? Une sortie partielle pourra subsister.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.append('Interruption demandée…')
        self.proc.terminate()
        QTimer.singleShot(3000, self.force_kill_if_running)

    def force_kill_if_running(self):
        if self.proc is not None and self.proc.state() != QProcess.NotRunning:
            self.proc.kill()

    def launch_utility(self, filename):
        scenery = Path(self.output.text()).expanduser()
        if not (scenery/'Objects').is_dir():
            QMessageBox.warning(self, 'Scenery indisponible', f'Aucun dossier Objects dans :\n{scenery}')
            return
        tool = ROOT / filename
        self.save()
        process = QProcess(self)
        process.setWorkingDirectory(str(ROOT))
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.finished.connect(lambda *_: self.cleanup_tool(process))
        process.errorOccurred.connect(lambda *_: self.utility_error(process, filename))
        self.tool_processes.append(process)
        process.start(sys.executable, [str(tool), '--scenery', str(scenery)])
        self.statusBar().showMessage(f'Ouverture de {filename}…')

    def utility_error(self, process, filename):
        output = bytes(process.readAllStandardOutput()).decode('utf-8', errors='replace')
        QMessageBox.warning(self, filename, output or process.errorString())

    def cleanup_tool(self, process):
        if process in self.tool_processes:
            self.tool_processes.remove(process)
        process.deleteLater()

    def open_output(self):
        output = Path(self.output.text()).expanduser()
        if not output.is_dir():
            QMessageBox.warning(self, 'Dossier absent', str(output))
            return
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(output)))

    def closeEvent(self, event):
        if self.proc is not None:
            QMessageBox.warning(self, 'Conversion en cours', 'Arrêter la conversion avant de fermer l’application.')
            event.ignore()
            return
        self.save()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('XPlane2FlightGear')
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
