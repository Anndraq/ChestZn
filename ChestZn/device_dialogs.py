"""
device_dialogs.py — Диалоговые окна создания и редактирования оборудования в разделе «Точки и ПК»:
- EditComputerDialog: настройка компьютера поста (имя, IP, роль, узел обмена, статус).
- EditPrinterDialog: настройка принтера (только само устройство: выбор принтера Windows / IP / порт / язык команд / копий).
  Ширина, высота и DPI этикетки теперь берутся автоматически из активного макета во вкладке «Шаблоны».
- EditScannerDialog: настройка сканера штрихкодов и его привязки к принтеру.
"""

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from printer import detect_language, list_printers


class EditComputerDialog(QDialog):
    """Диалог добавления / редактирования компьютера рабочей точки."""

    def __init__(self, comp: Dict[str, Any], point_name: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.comp = dict(comp or {})
        self.point_name = point_name
        self.result_data: Optional[Dict[str, Any]] = None
        self._is_edit = "id" in self.comp and self.comp["id"] is not None

        self.setWindowTitle(
            f"Dotmark · {'Редактирование ПК' if self._is_edit else 'Добавление ПК'}"
            + (f" ({point_name})" if point_name else "")
        )
        self.setMinimumWidth(460)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)

        hdr = QLabel("Параметры компьютера рабочей точки")
        hdr.setStyleSheet("font-size: 16px; font-weight: 700;")
        lay.addWidget(hdr)

        card = QFrame()
        card.setObjectName("card")
        form = QFormLayout(card)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(10)

        self.name_edit = QLineEdit(str(self.comp.get("name") or "ПК-01"))
        self.ip_edit = QLineEdit(str(self.comp.get("ip") or ""))
        self.ip_edit.setPlaceholderText("Например: 192.168.1.50 (пусто для локального ПК)")
        self.role_edit = QLineEdit(str(self.comp.get("role") or "Основной оператор"))
        self.operator_edit = QLineEdit(str(self.comp.get("operator") or ""))
        self.sync_node_edit = QLineEdit(str(self.comp.get("sync_node") or self.comp.get("name") or "ПК-01"))

        form.addRow("Имя компьютера:", self.name_edit)
        form.addRow("IP-адрес:", self.ip_edit)
        form.addRow("Роль / пост:", self.role_edit)
        form.addRow("Оператор:", self.operator_edit)
        form.addRow("Код узла обмена:", self.sync_node_edit)
        lay.addWidget(card)

        btns = QHBoxLayout()
        if self._is_edit:
            btn_del = QPushButton("🗑 Удалить ПК")
            btn_del.setObjectName("btn_danger")
            btn_del.clicked.connect(self._on_delete)
            btns.addWidget(btn_del)

        btns.addStretch(1)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("💾 Сохранить")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(self._on_save)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_save)
        lay.addLayout(btns)

    def _on_delete(self):
        if QMessageBox.question(
            self, "Удаление ПК",
            f"Удалить компьютер «{self.comp.get('name', '')}»?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes:
            self.result_data = {"action": "delete"}
            self.accept()

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Внимание", "Введите имя компьютера.")
            return
        self.result_data = {
            "name": name,
            "ip": self.ip_edit.text().strip(),
            "role": self.role_edit.text().strip() or "Рабочий пост",
            "operator": self.operator_edit.text().strip() or "—",
            "sync_node": self.sync_node_edit.text().strip() or name,
            "sync_enabled": True,
            "online": True,
        }
        self.accept()


class EditPrinterDialog(QDialog):
    """
    Диалог добавления / редактирования принтера в «Точки и ПК».
    Содержит ТОЛЬКО настройки самого устройства (имя принтера Windows, IP/порт, язык команд, копии).
    Размеры этикетки (ширина, высота) и DPI берутся автоматически из активного макета во вкладке «Шаблоны».
    """

    def __init__(self, printer: Dict[str, Any], comp_name: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.printer = dict(printer or {})
        self.comp_name = comp_name
        self.result_data: Optional[Dict[str, Any]] = None
        self._is_edit = "id" in self.printer and self.printer["id"] is not None

        self.setWindowTitle(
            f"Dotmark · {'Настройка принтера' if self._is_edit else 'Добавление принтера'}"
            + (f" ({comp_name})" if comp_name else "")
        )
        self.setMinimumWidth(480)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)

        hdr = QLabel("Устройство печати этикеток")
        hdr.setStyleSheet("font-size: 16px; font-weight: 700;")
        lay.addWidget(hdr)

        sub = QLabel(
            "💡 Ширина, высота, DPI и расположение полей берутся автоматически из вашего макета во вкладке «Шаблоны»."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #64748b; font-size: 12px;")
        lay.addWidget(sub)

        card = QFrame()
        card.setObjectName("card")
        form = QFormLayout(card)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(10)

        self.name_combo = QComboBox()
        self.name_combo.setEditable(True)
        cur_name = str(self.printer.get("name") or "").strip()

        found_names: List[str] = []
        try:
            for p in (list_printers() or []):
                p_name = p.get("name", "") if isinstance(p, dict) else str(p)
                if p_name and p_name not in found_names:
                    found_names.append(p_name)
        except Exception:
            pass

        if cur_name and cur_name not in found_names:
            found_names.insert(0, cur_name)
        if not found_names:
            found_names = ["Принтер этикеток"]

        self.name_combo.addItems(found_names)
        if cur_name:
            self.name_combo.setCurrentText(cur_name)

        self.ip_edit = QLineEdit(str(self.printer.get("ip") or ""))
        self.ip_edit.setPlaceholderText("Пусто для USB / локального принтера Windows, либо IP (напр. 192.168.1.200)")

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(self.printer.get("port") or 9100))

        self.lang_combo = QComboBox()
        self.lang_combo.addItem("auto — Авто (макет через драйвер Windows / по модели)", "auto")
        self.lang_combo.addItem("windows — Графическая печать макета 1-в-1 (Windows GDI)", "windows")
        self.lang_combo.addItem("tspl — Термопринтер TSC / Xprinter / АТОЛ (TSPL)", "tspl")
        self.lang_combo.addItem("zpl — Термопринтер Zebra (ZPL II)", "zpl")
        self.lang_combo.addItem("ezpl — Термопринтер Godex (EZPL)", "ezpl")

        cur_lang = str(self.printer.get("lang") or "auto").lower()
        lang_idx = self.lang_combo.findData(cur_lang)
        if lang_idx >= 0:
            self.lang_combo.setCurrentIndex(lang_idx)

        self.name_combo.currentTextChanged.connect(self._on_name_changed)

        self.copies_spin = QSpinBox()
        self.copies_spin.setRange(1, 20)
        self.copies_spin.setValue(int(self.printer.get("copies") or 1))

        form.addRow("Принтер в системе:", self.name_combo)
        form.addRow("IP-адрес (для сети):", self.ip_edit)
        form.addRow("TCP-порт (RAW):", self.port_spin)
        form.addRow("Режим / язык печати:", self.lang_combo)
        form.addRow("Копий при печати:", self.copies_spin)
        lay.addWidget(card)

        btns = QHBoxLayout()
        if self._is_edit:
            btn_del = QPushButton("🗑 Удалить принтер")
            btn_del.setObjectName("btn_danger")
            btn_del.clicked.connect(self._on_delete)
            btns.addWidget(btn_del)

        btns.addStretch(1)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("💾 Сохранить")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(self._on_save)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_save)
        lay.addLayout(btns)

    def _on_name_changed(self, text: str):
        if self.lang_combo.currentData() == "auto":
            return
        detected = detect_language(text)
        if detected in ("zpl", "tspl", "ezpl"):
            idx = self.lang_combo.findData(detected)
            if idx >= 0:
                self.lang_combo.setCurrentIndex(idx)

    def _on_delete(self):
        if QMessageBox.question(
            self, "Удаление принтера",
            f"Удалить принтер «{self.printer.get('name', '')}»?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes:
            self.result_data = {"action": "delete"}
            self.accept()

    def _on_save(self):
        name = self.name_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "Внимание", "Укажите название принтера.")
            return

        # Сохраняем обратную совместимость со схемой БД store (width_mm, height_mm, dpi),
        # подтягивая значения из шаблона по умолчанию (или прежние значения записи)
        w_mm = float(self.printer.get("width_mm") or 58)
        h_mm = float(self.printer.get("height_mm") or 40)
        dpi_val = int(self.printer.get("dpi") or 203)
        try:
            from printer import get_active_template_spec
            spec = get_active_template_spec()
            if spec:
                w_mm = float(spec.get("w") or w_mm)
                h_mm = float(spec.get("h") or h_mm)
                dpi_val = int(spec.get("dpi") or dpi_val)
        except Exception:
            pass

        self.result_data = {
            "name": name,
            "ip": self.ip_edit.text().strip(),
            "port": int(self.port_spin.value()),
            "lang": str(self.lang_combo.currentData() or "auto"),
            "width_mm": int(round(w_mm)),
            "height_mm": int(round(h_mm)),
            "dpi": dpi_val,
            "copies": int(self.copies_spin.value()),
            "online": True,
        }
        self.accept()


class EditScannerDialog(QDialog):
    """Диалог добавления / редактирования сканера штрихкодов."""

    def __init__(
        self,
        scanner: Dict[str, Any],
        comp_name: str = "",
        printers: Optional[List[Dict[str, Any]]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.scanner = dict(scanner or {})
        self.comp_name = comp_name
        self.printers = list(printers or [])
        self.result_data: Optional[Dict[str, Any]] = None
        self._is_edit = "id" in self.scanner and self.scanner["id"] is not None

        self.setWindowTitle(
            f"Dotmark · {'Настройка сканера' if self._is_edit else 'Добавление сканера'}"
            + (f" ({comp_name})" if comp_name else "")
        )
        self.setMinimumWidth(460)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)

        hdr = QLabel("Параметры сканера штрихкодов")
        hdr.setStyleSheet("font-size: 16px; font-weight: 700;")
        lay.addWidget(hdr)

        card = QFrame()
        card.setObjectName("card")
        form = QFormLayout(card)
        form.setContentsMargins(16, 14, 16, 14)
        form.setSpacing(10)

        self.name_edit = QLineEdit(str(self.scanner.get("name") or "Сканер ШК"))

        self.type_combo = QComboBox()
        self.type_combo.addItem("USB-HID (режим клавиатуры)", "usb")
        self.type_combo.addItem("USB-COM (виртуальный COM-порт)", "usb-com")
        self.type_combo.addItem("Bluetooth SPP (COM-порт)", "bt-spp")
        self.type_combo.addItem("TCP/IP (сетевой сканер)", "tcp")

        cur_type = str(self.scanner.get("connect_type") or "usb").lower()
        t_idx = self.type_combo.findData(cur_type)
        if t_idx >= 0:
            self.type_combo.setCurrentIndex(t_idx)

        self.com_label = QLabel("COM-порт:")
        self.com_edit = QLineEdit(str(self.scanner.get("com_port") or ""))
        self.com_edit.setPlaceholderText("Например: COM3")

        self.printer_combo = QComboBox()
        self.printer_combo.addItem("— Автоматически (основной принтер точки) —", None)
        cur_pr_id = self.scanner.get("printer_id")
        for pr in self.printers:
            self.printer_combo.addItem(f"🖨 {pr.get('name', 'Принтер')}", pr.get("id"))
            if cur_pr_id is not None and pr.get("id") == cur_pr_id:
                self.printer_combo.setCurrentIndex(self.printer_combo.count() - 1)

        form.addRow("Название сканера:", self.name_edit)
        form.addRow("Тип подключения:", self.type_combo)
        form.addRow(self.com_label, self.com_edit)
        form.addRow("Привязанный принтер:", self.printer_combo)

        def _toggle_com_visibility():
            ctype = str(self.type_combo.currentData() or "usb")
            show_com = ctype in ("usb-com", "bt-spp")
            self.com_label.setVisible(show_com)
            self.com_edit.setVisible(show_com)

        self.type_combo.currentIndexChanged.connect(_toggle_com_visibility)
        _toggle_com_visibility()
        lay.addWidget(card)

        btns = QHBoxLayout()
        if self._is_edit:
            btn_del = QPushButton("🗑 Удалить сканер")
            btn_del.setObjectName("btn_danger")
            btn_del.clicked.connect(self._on_delete)
            btns.addWidget(btn_del)

        btns.addStretch(1)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("💾 Сохранить")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(self._on_save)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_save)
        lay.addLayout(btns)

    def _on_delete(self):
        if QMessageBox.question(
            self, "Удаление сканера",
            f"Удалить сканер «{self.scanner.get('name', '')}»?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes:
            self.result_data = {"action": "delete"}
            self.accept()

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Внимание", "Введите название сканера.")
            return
        ctype = str(self.type_combo.currentData() or "usb")
        self.result_data = {
            "name": name,
            "operator": str(self.scanner.get("operator") or ""),
            "connect_type": ctype,
            "com_port": self.com_edit.text().strip().upper() if ctype in ("usb-com", "bt-spp") else "",
            "bt_address": str(self.scanner.get("bt_address") or ""),
            "ip": str(self.scanner.get("ip") or ""),
            "port": int(self.scanner.get("port") or 9100),
            "printer_id": self.printer_combo.currentData(),
            "enabled": True,
        }
        self.accept()
