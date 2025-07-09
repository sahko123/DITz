import shutil, hashlib, sys
from pathlib import Path
import psutil
import os
import json
from copyWorker import CopyWorker
import string
import re
from utils import get_base_token_keys, clean_unmatched_braces

from PySide6.QtCore import Qt, QSize, QPoint, QThread, QObject, Signal, QMimeData, QFileInfo
from PySide6.QtGui import QDrag, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QWidget, QListWidget, QListWidgetItem, QLabel,
    QVBoxLayout, QHBoxLayout, QPushButton, QFrame, QFileDialog,
    QProgressBar, QCheckBox, QMessageBox, QFileIconProvider, QLineEdit,
    QFormLayout, QComboBox, 
)

QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

log = lambda m: print(f"[DITZ] {m}", flush=True)

DISPLAY_NAME_ROLE = Qt.UserRole + 1

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI awareness
except Exception:
    pass

from ctypes import wintypes # This will break on non windows platforms

def start_native_drag(widget):
    hwnd = int(widget.winId())
    user32 = ctypes.windll.user32
    HTCAPTION = 2
    WM_NCLBUTTONDOWN = 0x00A1
    user32.ReleaseCapture()
    user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)

class BaseListWidget(QListWidget):
    """Common base for drag-and-drop-enabled drive/folder lists."""

    def __init__(self, name):
        super().__init__(objectName=name)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(48, 48) * scale)
        self.setResizeMode(QListWidget.Adjust)
        self.setSpacing(12)
    
    def _remove_from_other_lists(self, path_str):
        # Iterate all top-level widgets to find the source list
        for widget in QApplication.allWidgets():
            if isinstance(widget, BaseListWidget) and widget is not self:
                for i in range(widget.count()):
                    item = widget.item(i)
                    if item.data(Qt.UserRole) == path_str:
                        widget.takeItem(i)
                        return  # stop after removing once

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        print(f"Dropping on {self.objectName()} — formats:", e.mimeData().text())
        paths = []
        item_data = json.loads(e.mimeData().text())
        print(item_data)

        if e.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in e.mimeData().urls()]
        elif e.mimeData().hasText():
            try:
                # Try parsing structured JSON first
                data = json.loads(e.mimeData().text())
                if isinstance(data, dict) and "path" in data:
                    paths = [data["path"]]
                elif isinstance(data, list):  # handle multiple items
                    paths = [entry["path"] for entry in data if "path" in entry]
            except json.JSONDecodeError:
                # Fall back to plain text path(s)
                paths = e.mimeData().text().splitlines()

        
        for raw in paths:
            p = Path(raw)
            if not p.exists() or p.is_file():
                continue
            if not self._contains(str(p)):
                self._add_item(p, data["label"])
            
            self._remove_from_other_lists(str(p))

        e.acceptProposedAction()

    def startDrag(self, *_):
        item = self.currentItem()
        if not item:
            return
        
        payload = json.dumps({
        "path": item.data(Qt.UserRole),
        "label": item.data(Qt.UserRole + 1),
        })

        md = QMimeData()
        md.setText(payload)
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(item.icon().pixmap(48, 48))
        drag.exec()
        print(f"Dragging from {self.objectName()} — text:", md.text())

    def _contains(self, path_str: str):
        for i in range(self.count()):
            if self.item(i).data(Qt.UserRole) == path_str:
                return True
        return False

    def _add_item(self, p: Path, label):
        icon = icon_provider.icon(QFileInfo(str(p)))

        it = QListWidgetItem(icon, label)
        it.setData(Qt.UserRole, str(p))
        it.setData(Qt.UserRole + 1, label)
        it.setToolTip(str(p))
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
        self.addItem(it)

    def paths(self):
        return [self.item(i).data(Qt.UserRole) for i in range(self.count())]
    

class FileListWidget(BaseListWidget):
    def __init__(self, name):
        super().__init__(name)

class DriveListWidget(BaseListWidget):
    def __init__(self):
        super().__init__("DriveList")


# ─────────────── Main UI ───────────────
class Ditz_ui(QWidget):
    def __init__(self):
        super().__init__()

        # App window config
        self.setWindowTitle("DITz – Don't Imagine The Zebras")
        self.setMinimumSize(1000, 1000)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)  # needed for rounded corners
        self.setObjectName("MainWindow")

        self._offset = QPoint()  # for dragging

        # Outer wrapper frame that receives border and rounding
        outer_frame = QFrame(self)
        outer_frame.setObjectName("MainFrame")
        outer_layout = QVBoxLayout(outer_frame)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # App content layout inside the frame
        root = QVBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)

        root.addWidget(self._title_bar())  # Your custom title bar

        # Center layout
        center = QHBoxLayout()
        center.setContentsMargins(10, 10, 10, 0)
        center.setSpacing(10)

        self.input_list = FileListWidget("InputList")
        self.output_list = FileListWidget("OutputList")

        center.addWidget(self._side_panel("Input", self.input_list,
                                          QPushButton("Add Input", clicked=self._pick_input)))

        self.drive_list = DriveListWidget()
        center.addWidget(self._drive_panel(), 2)

        center.addWidget(self._side_panel("Output", self.output_list,
                                          QPushButton("Add Output", clicked=self._pick_output)))

        root.addLayout(center, 1)

        # Bottom bar
        bottom = QHBoxLayout()
        bottom.setContentsMargins(10, 6, 10, 10)

        self.verify_chk = QCheckBox("Verify checksum (SHA-256)")
        self.go_btn = QPushButton("Ingest Files", clicked=self._start_copy)
        self.go_btn.setEnabled(False)
        self.pb = QProgressBar()
        self.pb.setValue(0)
        

        bottom.addWidget(self.verify_chk)
        bottom.addStretch()
        bottom.addWidget(self.go_btn)
        bottom.addWidget(self.pb, 2)
        

        root.addLayout(bottom)


        copy_config = QFormLayout()
        copy_config.setContentsMargins(10, 6, 10, 10)

        self.structure_presets = QComboBox()
        self.structure_presets.addItems(['default', 'blank'])
        self.video_folder_template = QLineEdit("FOOTAGE/{type}")
        self.video_folder_template.setPlaceholderText("Video folder structure e.g. 'FOOTAGE/{type}'")
        self.audio_folder_template = QLineEdit("FOOTAGE/{type}")
        self.audio_folder_template.setPlaceholderText("Audio folder structure e.g. 'FOOTAGE/{type}'")
        self.photo_folder_template = QLineEdit("FOOTAGE/{type}")
        self.photo_folder_template.setPlaceholderText("Photo folder structure e.g. 'FOOTAGE/{type}'")
        self.filename_template = QLineEdit("{file_year}_{file_month}_{file_day}_{file_date}")
        self.filename_template.setPlaceholderText("Filename Template e.g. '{project}-{date}-{camera_letter}-{notes}'")

        copy_config.addRow("Presets: ", self.structure_presets)
        copy_config.addRow("Filename Template", self.filename_template)
        copy_config.addRow("Video Folder Structure", self.video_folder_template)
        copy_config.addRow("Audio Folder Structure", self.audio_folder_template)
        copy_config.addRow("Photo Folder Structure", self.photo_folder_template)

        self.video_folder_template.textChanged.connect(self.on_template_changed)
        self.audio_folder_template.textChanged.connect(self.on_template_changed)
        self.photo_folder_template.textChanged.connect(self.on_template_changed)
        self.filename_template.textChanged.connect(self.on_template_changed)
        
        root.addLayout(copy_config)

        self.token_container = QWidget()
        self.token_section = QFormLayout(self.token_container)  # Set layout on widget
        self.token_section.setContentsMargins(10, 6, 10, 10)
        
        root.addWidget(self.token_container)

        self.update_token_section()

        # Place inner layout inside the styled frame
        outer_layout.addLayout(root)

        # Set frame as main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(outer_frame)

        # Apply stylesheet
        with open("style.qss", encoding="utf-8") as f:
            self.setStyleSheet(f.read())

        # Enable/disable copy button
        for lst in (self.input_list, self.output_list):
            lst.model().rowsInserted.connect(self._update_ready)
            lst.model().rowsRemoved.connect(self._update_ready)

        self._refresh_drives()

    def on_template_changed(self):
        video_tpl = self.video_folder_template.text()
        audio_tpl = self.audio_folder_template.text()
        photo_tpl = self.photo_folder_template.text()
        filename_tpl = self.filename_template.text()

        print("Updated Templates:")
        print("Video:   ", video_tpl)
        print("Audio:   ", audio_tpl)
        print("Photo:   ", photo_tpl)
        print("Filename:", filename_tpl)

        # You can also store them in an attribute:
        self.current_templates = {
            "video": video_tpl,
            "audio": audio_tpl,
            "photo": photo_tpl,
            "other": "Misc"  # optional default
        }
        self.filename_template_test = filename_tpl
        self.update_token_section()

    # ---------- UI helpers ----------
    def get_token_list(self):
        video_tpl = self.video_folder_template.text()
        audio_tpl = self.audio_folder_template.text()
        photo_tpl = self.photo_folder_template.text()
        filename_tpl = self.filename_template.text()
        template = clean_unmatched_braces(video_tpl) + clean_unmatched_braces(audio_tpl) + clean_unmatched_braces(photo_tpl) + clean_unmatched_braces(filename_tpl)

        formatter = string.Formatter()
        return {field_name for _, field_name, _, _ in formatter.parse(template) if field_name}
    
    def update_token_section(self):
        self.clear_token_section()
        base_token_keys = get_base_token_keys()
        # Example — you can build this dynamically
        self.token_list = self.get_token_list()
        for token in self.token_list:
            if token not in base_token_keys:
                self.token_section.layout().addRow(token, QLineEdit())

    def clear_token_section(self):
        form = self.token_section.layout()
        while form.count():
            item = form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._delete_layout_recursive(item.layout())

    def _delete_layout_recursive(self, layout):
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                self._delete_layout_recursive(child.layout())

    def _title_bar(self):
        bar = QWidget(objectName="TitleBar")
        hl  = QHBoxLayout(bar); hl.setContentsMargins(10,4,10,4)
        hl.addWidget(QLabel("DITz - Dont Imagine The zebra", objectName="TitleLabel"))
        hl.addStretch()
        hl.addWidget(QPushButton("-", objectName="TitleButton",
                                 clicked=self.showMinimized, fixedWidth=30))
        hl.addWidget(QPushButton("✕", objectName="TitleButton",
                                 clicked=self.close, fixedWidth=30))
        return bar

    def _side_panel(self, title, list_widget, button):
        frame = QFrame(objectName="SidePanel")
        v = QVBoxLayout(frame); v.setSpacing(6)
        v.addWidget(QLabel(title, objectName="PanelLabel", alignment=Qt.AlignHCenter))
        v.addWidget(list_widget, 1); v.addWidget(button)
        return frame

    def _drive_panel(self):
        frame = QFrame(objectName="CenterPanel")
        v = QVBoxLayout(frame); v.setSpacing(6)
        v.addWidget(self.drive_list, 1)
        v.addWidget(QPushButton("Refresh Drives", clicked=self._refresh_drives), alignment=Qt.AlignHCenter)
        return frame

    # ---------- drive enumeration ----------
    def _refresh_drives(self):
        self.drive_list.clear()
        self.input_list.clear()
        self.output_list.clear()
        for part in psutil.disk_partitions(all=False):
            try:
                print(part)
                usage = psutil.disk_usage(part.mountpoint)
                used_gb = usage.used // (1024**3)
                total_gb = usage.total // (1024**3)
                percent = usage.percent
                removable = ""
                if "removable" in part.opts:
                    removable = "(Removable)"

                label = f"{part.device} {removable}\n{used_gb} / {total_gb} GB ({percent}%)"
                it = QListWidgetItem(icon_provider.icon(QFileInfo(part.device)), label)
                it.setData(Qt.UserRole, part.mountpoint)
                it.setData(Qt.UserRole + 1, label)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
                self.drive_list.addItem(it)
            except PermissionError:
                continue

    # ---------- manual add ----------
    def _pick_input(self):
        path = QFileDialog.getExistingDirectory(self, "Select Input Folder / Drive")
        if path: self.input_list._add_item(Path(path), path)

    def _pick_output(self):
        path = QFileDialog.getExistingDirectory(self, "Select Output Folder / Drive")
        if path: self.output_list._add_item(Path(path), path)

    # ---------- ingest workflow ----------
    def _update_ready(self):
        self.go_btn.setEnabled(self.input_list.count() > 0 and self.output_list.count() > 0)

    def _start_copy(self):
        self.pb.setValue(0); self.go_btn.setEnabled(False)

        self.thread = QThread()
        self.worker = CopyWorker(
            self.input_list.paths(),
            self.output_list.paths(),
            self.verify_chk.isChecked(),
            filename_template=self.filename_template.text() + "{ext}", # Force Extension on end
            folder_templates={
               "video": self.video_folder_template.text(),
               "audio": self.audio_folder_template.text(),
               "photo": self.photo_folder_template.text(),
               "other": "misc"
               },
            custom_tokens={}
            )
        
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.pb.setValue)
        self.worker.done.connect(self._copy_done)
        self.worker.error.connect(self._copy_error)
        self.worker.done.connect(self.thread.quit)
        self.worker.error.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()

    def _copy_done(self):
        QMessageBox.information(self, "Ingest Complete", "All data copied successfully!")
        self.go_btn.setEnabled(True); self.pb.setValue(100)

    def _copy_error(self, msg):
        QMessageBox.critical(self, "Ingest Error", msg)
        self.go_btn.setEnabled(True)

    # ---------- title-bar drag ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            child = self.childAt(e.position().toPoint())
            if child and child.objectName() == "TitleBar":
                self._offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
                e.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            child = self.childAt(e.position().toPoint())
            if child and child.objectName() == "TitleBar":
                start_native_drag(self)

# ─────────────── app entry ───────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    scale = QGuiApplication.primaryScreen().devicePixelRatio()
    app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    icon_provider = QFileIconProvider()
    win = Ditz_ui(); win.show()
    sys.exit(app.exec())
