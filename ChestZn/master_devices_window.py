"""
ChestZn — Быстрая привязка «Сканер ↔ Принтер» (master_devices_window.py)
Позволяет привязать каждый сканер к принтеру за 1 клик.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QPushButton, QComboBox,
    QMessageBox, QFrame
)

from device_discovery import detect_local_printers, detect_local_scanners

logger = logging.getLogger("ChestZn.MasterDevices")
CONFIG_PATH = Path("devices_config.json")


def sync_bindings_to_system(bindings: Dict[str, str], store: Any = None):
    """Синхронизирует привязки в devices_config.json и printer.py."""
    if not bindings:
        return
    primary_printer = bindings.get("USB-HID") or next(iter(bindings.values()), "")

    try:
        from printer import load_config, save_config, detect_language, set_printer_for_point
        cfg = load_config()
        if primary_printer:
            cfg["printer_name"] = primary_printer
            cfg["printer_lang"] = detect_language(primary_printer)
            cfg["scanner_bindings"] = dict(bindings)
            for pt in ["T-01", "T-02", "T-03"]:
                set_printer_for_point(cfg, pt, primary_printer, cfg["printer_lang"])
            save_config(cfg)
    except Exception as e:
        logger.error("Ошибка сохранения привязок принтера: %s", e)


class AssignPrinterDialog(QDialog):
    """Компактное окно быстрой привязки сканера к принтеру."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Привязка оборудования: Сканер ➔ Принтер")
        self.setFixedWidth(460)
        self.bindings = self._load_bindings()
        self._init_ui()

    def _load_bindings(self) -> Dict[str, str]:
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f).get("bindings", {})
            except Exception:
                pass
        return {}

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        hdr = QLabel("Закрепление принтера за сканером")
        hdr.setStyleSheet("font-size:15px; font-weight:700; color:#f8fafc;")
        layout.addWidget(hdr)

        sub = QLabel("Выберите сканер и принтер, на который будут выходить этикетки:")
        sub.setStyleSheet("color:#94a3b8; font-size:12px;")
        layout.addWidget(sub)

        form = QFormLayout()
        form.setSpacing(10)

        self.cmb_scanner = QComboBox()
        scanners = detect_local_scanners(connected_only=False)
        for sc in scanners:
            dev = sc.get("device", "USB-HID")
            self.cmb_scanner.addItem(f"📡 {sc['name']} ({dev})", dev)
        form.addRow("Сканер:", self.cmb_scanner)

        self.cmb_printer = QComboBox()
        printers = detect_local_printers(connected_only=False)
        self.cmb_printer.addItem("— Не назначен —", "")
        for pr in printers:
            self.cmb_printer.addItem(f"🖨 {pr['name']}", pr['name'])
        form.addRow("Принтер:", self.cmb_printer)

        layout.addLayout(form)

        # Выбираем текущую привязку
        cur_sc = self.cmb_scanner.currentData()
        if cur_sc in self.bindings:
            idx = self.cmb_printer.findData(self.bindings[cur_sc])
            if idx >= 0:
                self.cmb_printer.setCurrentIndex(idx)

        self.cmb_scanner.currentIndexChanged.connect(self._on_scanner_changed)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("💾 Привязать")
        btn_save.setProperty("primary", True)
        btn_save.setStyleSheet(
            "background:#2563eb; color:#ffffff; font-weight:700; padding:7px 18px; border-radius:6px;"
        )
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)

        layout.addLayout(btn_row)

    def _on_scanner_changed(self):
        sc = self.cmb_scanner.currentData()
        bound_pr = self.bindings.get(sc, "")
        idx = self.cmb_printer.findData(bound_pr)
        self.cmb_printer.setCurrentIndex(idx if idx >= 0 else 0)

    def _save(self):
        sc = self.cmb_scanner.currentData()
        pr = self.cmb_printer.currentData()

        if pr:
            self.bindings[sc] = pr
        else:
            self.bindings.pop(sc, None)

        try:
            cfg = {}
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["bindings"] = self.bindings
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)

            sync_bindings_to_system(self.bindings)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить: {e}")