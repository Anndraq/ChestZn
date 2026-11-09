"""
Диалоги создания и редактирования рабочих точек и компьютеров (edit_point_window.py).
Поддерживает добавление новой точки с автогенерацией кода T-XX.
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QFormLayout
)
from PySide6.QtCore import Qt


class EditPointDialog(QDialog):
    """Создание и редактирование точки."""

    def __init__(self, point: dict = None, parent=None, is_new: bool = False):
        super().__init__(parent)
        self._is_new = is_new
        self._point = dict(point or {})
        self.result_data = None

        title_text = "Новая рабочая точка" if is_new else f"Точка: {self._point.get('id', '')}"
        self.setWindowTitle(title_text)
        self.setMinimumWidth(480)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(12)

        title = QLabel(title_text)
        title.setStyleSheet("font-size:16px; font-weight:700; color:#f8fafc;")
        v.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)

        self.id_edit = QLineEdit(self._point.get("id", ""))
        self.id_edit.setPlaceholderText("Например: T-04")
        if not is_new:
            self.id_edit.setEnabled(False)
        form.addRow("Код точки (ID):", self.id_edit)

        self.name_edit = QLineEdit(self._point.get("name", ""))
        self.name_edit.setPlaceholderText("Например: Упаковочный стол №2")
        form.addRow("Название:", self.name_edit)

        self.address_edit = QLineEdit(self._point.get("address", ""))
        self.address_edit.setPlaceholderText("Цех 1, Линия Б")
        form.addRow("Расположение:", self.address_edit)

        self.online_chk = QCheckBox("Точка активна")
        self.online_chk.setChecked(bool(self._point.get("online", True)))
        form.addRow("", self.online_chk)

        v.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("💾 Сохранить")
        btn_save.setProperty("primary", True)
        btn_save.setStyleSheet(
            "background:#16a34a; color:#ffffff; font-weight:700; padding:7px 18px; border-radius:6px;"
        )
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)

        v.addLayout(btn_row)

    def _save(self):
        p_id = self.id_edit.text().strip().upper()
        name = self.name_edit.text().strip()
        if not p_id or not name:
            return

        self.result_data = {
            "id": p_id,
            "name": name,
            "address": self.address_edit.text().strip(),
            "ip": "",
            "printer": "",
            "template": self._point.get("template", "electronics"),
            "online": self.online_chk.isChecked(),
        }
        self.accept()


class EditComputerDialog(QDialog):
    """Редактирование компьютера точки."""

    def __init__(self, computer: dict, point_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройка ПК")
        self.setMinimumWidth(440)
        self._c = dict(computer)
        self.result_data = None

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 18)
        v.setSpacing(12)

        title = QLabel(f"Компьютер поста: {point_name}")
        title.setStyleSheet("font-size:15px; font-weight:700; color:#f8fafc;")
        v.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit(self._c.get("name", ""))
        self.name_edit.setPlaceholderText("Например: PC-PACK-01")
        form.addRow("Имя компьютера:", self.name_edit)

        self.ip_edit = QLineEdit(self._c.get("ip", ""))
        self.ip_edit.setPlaceholderText("192.168.1.100")
        form.addRow("IP-адрес:", self.ip_edit)

        self.role_edit = QLineEdit(self._c.get("role", ""))
        self.role_edit.setPlaceholderText("Основной пост упаковки")
        form.addRow("Назначение:", self.role_edit)

        v.addLayout(form)

        btn_row = QHBoxLayout()
        btn_del = QPushButton("🗑 Удалить ПК")
        btn_del.setStyleSheet("color:#ef4444; background:transparent; border:none;")
        btn_del.clicked.connect(self._delete)
        btn_row.addWidget(btn_del)

        btn_row.addStretch(1)

        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("💾 Сохранить")
        btn_save.setProperty("primary", True)
        btn_save.setStyleSheet(
            "background:#2563eb; color:#ffffff; font-weight:700; padding:7px 18px; border-radius:6px;"
        )
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
            "online": True,
        }
        self.accept()

    def _delete(self):
        self.result_data = {"action": "delete"}
        self.accept()