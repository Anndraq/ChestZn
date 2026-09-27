"""Диалоги редактирования точки и компьютера."""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QComboBox, QFormLayout
)
from PySide6.QtCore import Qt


class EditPointDialog(QDialog):
    """Редактирование точки."""

    def __init__(self, point: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Точка: {point.get('id', '')}")
        self.setMinimumWidth(520)
        self._point = dict(point)
        self.result_data = None

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        title = QLabel(f"Точка {point.get('id', '')}")
        title.setStyleSheet("font-size:16px; font-weight:600;")
        v.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit(self._point.get("name", ""))
        form.addRow("Название:", self.name_edit)

        self.address_edit = QLineEdit(self._point.get("address", ""))
        form.addRow("Адрес / расположение:", self.address_edit)

        self.ip_edit = QLineEdit(self._point.get("ip", ""))
        form.addRow("IP-адрес:", self.ip_edit)

        self.printer_edit = QLineEdit(self._point.get("printer", ""))
        form.addRow("Принтер:", self.printer_edit)

        self.operator_edit = QLineEdit(self._point.get("operator", ""))
        form.addRow("Основной оператор:", self.operator_edit)

        self.template_edit = QLineEdit(self._point.get("template", ""))
        form.addRow("Шаблон по умолчанию:", self.template_edit)

        self.online_chk = QCheckBox("Точка онлайн")
        self.online_chk.setChecked(bool(self._point.get("online", True)))
        form.addRow("", self.online_chk)

        v.addLayout(form)

        hint = QLabel(
            "Компьютеры точки редактируются отдельно — "
            "выберите ПК в дереве и нажмите «Редактировать»."
        )
        hint.setStyleSheet("color:#8b97a6; font-size:11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("💾 Сохранить")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)

        v.addLayout(btn_row)

    def _save(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        self.result_data = {
            "name": name,
            "address": self.address_edit.text().strip(),
            "ip": self.ip_edit.text().strip(),
            "printer": self.printer_edit.text().strip(),
            "operator": self.operator_edit.text().strip(),
            "template": self.template_edit.text().strip(),
            "online": self.online_chk.isChecked(),
        }
        self.accept()


class EditComputerDialog(QDialog):
    """Редактирование компьютера (ПК) точки."""

    def __init__(self, computer: dict, point_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Компьютер")
        self.setMinimumWidth(460)
        self._c = dict(computer)
        self.result_data = None

        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 20)
        v.setSpacing(14)

        title = QLabel(f"Компьютер · {point_name}")
        title.setStyleSheet("font-size:16px; font-weight:600;")
        v.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit(self._c.get("name", ""))
        form.addRow("Имя:", self.name_edit)

        self.ip_edit = QLineEdit(self._c.get("ip", ""))
        form.addRow("IP-адрес:", self.ip_edit)

        self.role_edit = QLineEdit(self._c.get("role", ""))
        form.addRow("Роль / назначение:", self.role_edit)

        self.operator_edit = QLineEdit(self._c.get("operator", ""))
        form.addRow("Оператор:", self.operator_edit)

        self.online_chk = QCheckBox("Компьютер онлайн")
        self.online_chk.setChecked(bool(self._c.get("online", True)))
        form.addRow("", self.online_chk)

        v.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        btn_del = QPushButton("🗑 Удалить")
        btn_del.setStyleSheet("color:#fca5a5;")
        btn_del.clicked.connect(self._delete)
        btn_row.addWidget(btn_del)

        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("💾 Сохранить")
        btn_save.setProperty("primary", True)
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)

        v.addLayout(btn_row)

    def _save(self):
        name = self.name_edit.text().strip()
        if not name:
            return
        self.result_data = {
            "action": "save",
            "name": name,
            "ip": self.ip_edit.text().strip(),
            "role": self.role_edit.text().strip(),
            "operator": self.operator_edit.text().strip(),
            "online": self.online_chk.isChecked(),
        }
        self.accept()

    def _delete(self):
        self.result_data = {"action": "delete"}
        self.accept()