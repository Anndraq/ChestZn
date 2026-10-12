"""Диалоги редактирования устройств: ПК, принтеры, сканеры."""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox,
    QPushButton, QMessageBox
)


class _BaseDialog(QDialog):
    """Общая база: заголовок, форма, кнопки."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.result_data = None

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(24, 20, 24, 20)
        self._root.setSpacing(14)

        self._title = QLabel(title)
        self._title.setStyleSheet("font-size:16px; font-weight:600;")
        self._root.addWidget(self._title)

        self._form = QFormLayout()
        self._form.setSpacing(10)
        self._root.addLayout(self._form)

    def add_buttons(self, allow_delete: bool = False):
        row = QHBoxLayout()
        row.addStretch(1)

        if allow_delete:
            btn_del = QPushButton("🗑 Удалить")
            btn_del.setStyleSheet("color:#fca5a5;")
            btn_del.clicked.connect(self._on_delete)
            row.addWidget(btn_del)

        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(btn_cancel)

        btn_save = QPushButton("💾 Сохранить")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(self._on_save)
        row.addWidget(btn_save)

        self._root.addLayout(row)

    def _on_save(self):
        raise NotImplementedError

    def _on_delete(self):
        self.result_data = {"action": "delete"}
        self.accept()


# ==========================================================
# Компьютер
# ==========================================================
class EditComputerDialog(_BaseDialog):

    def __init__(self, computer: dict, point_name: str, parent=None):
        is_new = not computer.get("id")
        title = "Новый компьютер" if is_new else f"Компьютер · {point_name}"
        super().__init__(title, parent)
        self._is_new = is_new
        self._c = dict(computer)

        self.name_edit = QLineEdit(self._c.get("name", ""))
        self.name_edit.setPlaceholderText("ПК-01")
        self._form.addRow("Имя:", self.name_edit)

        self.ip_edit = QLineEdit(self._c.get("ip", ""))
        self.ip_edit.setPlaceholderText("10.0.1.10")
        self._form.addRow("IP-адрес:", self.ip_edit)

        self.role_edit = QLineEdit(self._c.get("role", ""))
        self.role_edit.setPlaceholderText("Основной оператор")
        self._form.addRow("Роль:", self.role_edit)

        self.sync_node_edit = QLineEdit(self._c.get("sync_node", ""))
        self.sync_node_edit.setPlaceholderText("COMPUTERNAME")
        self._form.addRow("Узел обмена:", self.sync_node_edit)

        self.sync_enabled_chk = QCheckBox("Участвует в обмене с мастером")
        self.sync_enabled_chk.setChecked(
            bool(self._c.get("sync_enabled", True))
        )
        self._form.addRow("", self.sync_enabled_chk)

        self.online_chk = QCheckBox("Компьютер онлайн")
        self.online_chk.setChecked(bool(self._c.get("online", True)))
        self._form.addRow("", self.online_chk)

        hint = QLabel(
            "«Узел обмена» — имя папки, через которую этот ПК обменивается "
            "с мастером. Обычно = имя компьютера в Windows."
        )
        hint.setStyleSheet("color:#8b97a6; font-size:11px;")
        hint.setWordWrap(True)
        self._root.addWidget(hint)

        self.add_buttons(allow_delete=not is_new)

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        self.result_data = {
            "action": "save",
            "name": name,
            "ip": self.ip_edit.text().strip(),
            "role": self.role_edit.text().strip(),
            "sync_node": self.sync_node_edit.text().strip() or name,
            "sync_enabled": self.sync_enabled_chk.isChecked(),
            "online": self.online_chk.isChecked(),
        }
        self.accept()


# ==========================================================
# Принтер
# ==========================================================
class EditPrinterDialog(_BaseDialog):

    def __init__(self, printer: dict, computer_name: str, parent=None):
        is_new = not printer.get("id")
        title = "Новый принтер" if is_new else f"Принтер · {computer_name}"
        super().__init__(title, parent)
        self._is_new = is_new
        self._p = dict(printer)

        self.name_edit = QLineEdit(self._p.get("name", ""))
        self.name_edit.setPlaceholderText("Zebra ZD421")
        self._form.addRow("Имя:", self.name_edit)

        self.ip_edit = QLineEdit(self._p.get("ip", ""))
        self.ip_edit.setPlaceholderText("10.0.1.20 или пусто (USB)")
        self._form.addRow("IP-адрес:", self.ip_edit)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(self._p.get("port", 9100)))
        self._form.addRow("Порт:", self.port_spin)

        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["auto", "zpl", "tspl", "ezpl", "windows"])
        self.lang_combo.setCurrentText(self._p.get("lang", "auto"))
        self._form.addRow("Язык:", self.lang_combo)

        self.w_spin = QDoubleSpinBox()
        self.w_spin.setRange(10, 500)
        self.w_spin.setDecimals(0)
        self.w_spin.setSuffix(" мм")
        self.w_spin.setValue(float(self._p.get("width_mm", 58)))
        self._form.addRow("Ширина:", self.w_spin)

        self.h_spin = QDoubleSpinBox()
        self.h_spin.setRange(10, 500)
        self.h_spin.setDecimals(0)
        self.h_spin.setSuffix(" мм")
        self.h_spin.setValue(float(self._p.get("height_mm", 40)))
        self._form.addRow("Высота:", self.h_spin)

        self.dpi_combo = QComboBox()
        self.dpi_combo.addItems(["203", "300", "600"])
        self.dpi_combo.setCurrentText(str(self._p.get("dpi", 203)))
        self._form.addRow("DPI:", self.dpi_combo)

        self.copies_spin = QSpinBox()
        self.copies_spin.setRange(1, 100)
        self.copies_spin.setValue(int(self._p.get("copies", 1)))
        self._form.addRow("Копий:", self.copies_spin)

        self.online_chk = QCheckBox("Принтер онлайн")
        self.online_chk.setChecked(bool(self._p.get("online", True)))
        self._form.addRow("", self.online_chk)

        self.add_buttons(allow_delete=not is_new)

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        self.result_data = {
            "action": "save",
            "name": name,
            "ip": self.ip_edit.text().strip(),
            "port": self.port_spin.value(),
            "lang": self.lang_combo.currentText(),
            "width_mm": self.w_spin.value(),
            "height_mm": self.h_spin.value(),
            "dpi": int(self.dpi_combo.currentText()),
            "copies": self.copies_spin.value(),
            "online": self.online_chk.isChecked(),
        }
        self.accept()


# ==========================================================
# Сканер
# ==========================================================
class EditScannerDialog(_BaseDialog):

    def __init__(self, scanner: dict, computer_name: str,
                 printers: list = None, parent=None):
        is_new = not scanner.get("id")
        title = "Новый сканер" if is_new else f"Сканер · {computer_name}"
        super().__init__(title, parent)
        self._is_new = is_new
        self._s = dict(scanner)
        self._printers = printers or []

        self.name_edit = QLineEdit(self._s.get("name", ""))
        self.name_edit.setPlaceholderText("Сканер M1")
        self._form.addRow("Имя:", self.name_edit)

        self.operator_edit = QLineEdit(self._s.get("operator", ""))
        self.operator_edit.setPlaceholderText("Иванов И.И.")
        self._form.addRow("Оператор:", self.operator_edit)

        self.type_combo = QComboBox()
        self.type_combo.addItem("USB-HID (клавиатура)", "usb")
        self.type_combo.addItem("USB-COM", "usb-com")
        self.type_combo.addItem("Bluetooth SPP (COM)", "bt-spp")
        self.type_combo.addItem("Bluetooth HID", "bt-hid")
        self.type_combo.addItem("Сетевой (TCP/IP)", "tcp")
        idx = self.type_combo.findData(self._s.get("connect_type", "usb"))
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        self._form.addRow("Тип подключения:", self.type_combo)

        self.com_edit = QLineEdit(self._s.get("com_port", ""))
        self.com_edit.setPlaceholderText("COM3")
        self._form.addRow("COM-порт:", self.com_edit)

        self.bt_edit = QLineEdit(self._s.get("bt_address", ""))
        self.bt_edit.setPlaceholderText("AA:BB:CC:DD:EE:FF")
        self._form.addRow("BT-адрес:", self.bt_edit)

        self.ip_edit = QLineEdit(self._s.get("ip", ""))
        self.ip_edit.setPlaceholderText("10.0.1.30")
        self._form.addRow("IP-адрес:", self.ip_edit)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(self._s.get("port", 9100)))
        self._form.addRow("Порт:", self.port_spin)

        self.printer_combo = QComboBox()
        self.printer_combo.addItem("— не привязан —", None)
        for p in self._printers:
            self.printer_combo.addItem(p["name"], p["id"])
        current_printer = self._s.get("printer_id")
        if current_printer:
            pi = self.printer_combo.findData(current_printer)
            if pi >= 0:
                self.printer_combo.setCurrentIndex(pi)
        self._form.addRow("Принтер:", self.printer_combo)

        self.enabled_chk = QCheckBox("Активен")
        self.enabled_chk.setChecked(bool(self._s.get("enabled", True)))
        self._form.addRow("", self.enabled_chk)

        hint = QLabel(
            "Для Bluetooth-сканера сначала выполните сопряжение в Windows "
            "(Параметры → Bluetooth → Добавить устройство). После этого "
            "в списке COM-портов появятся входящие порты SPP."
        )
        hint.setStyleSheet("color:#8b97a6; font-size:11px;")
        hint.setWordWrap(True)
        self._root.addWidget(hint)

        self.add_buttons(allow_delete=not is_new)

    def _on_save(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        self.result_data = {
            "action": "save",
            "name": name,
            "operator": self.operator_edit.text().strip(),
            "connect_type": self.type_combo.currentData(),
            "com_port": self.com_edit.text().strip(),
            "bt_address": self.bt_edit.text().strip(),
            "ip": self.ip_edit.text().strip(),
            "port": self.port_spin.value(),
            "printer_id": self.printer_combo.currentData(),
            "enabled": self.enabled_chk.isChecked(),
        }
        self.accept()