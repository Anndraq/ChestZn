"""
Окно импорта кодов ЧЗ из xlsx (файл ЦРПТ).

Логика:
1. Админ выбирает файл .xlsx.
2. Приложение парсит его и показывает предпросмотр:
   - заказ, количество GTIN, количество кодов;
   - список GTIN с количеством.
3. Админ выбирает точку (T-01 / T-02 / T-03).
4. Нажимает «Импорт» — коды записываются в pool_codes
   с assigned_point = выбранной точки.
5. Показывается отчёт: добавлено / пропущено / ошибки.
"""
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMessageBox, QFrame,
    QTextEdit, QSizePolicy
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush, QShortcut, QKeySequence

from logger import get_logger

log = get_logger("import_codes")


def _cell(text, color=None, mono=False) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text) if text is not None else "—")
    if color:
        it.setForeground(QBrush(QColor(color)))
    if mono:
        f = it.font()
        f.setFamily("Consolas")
        it.setFont(f)
    return it


class ImportCodesWindow(QDialog):
    """
    Модальное окно импорта кодов ЧЗ из xlsx.

    accept() — если импорт завершён (можно обновить пул в родителе).
    reject() — если отменили.
    """

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._parsed_rows = []
        self._order_name = None
        self._file_path = None

        self.setWindowTitle("ChestZn · Импорт кодов ЧЗ")
        self.setModal(True)
        self.setMinimumSize(820, 640)
        self.setObjectName("importCodesWindow")

        self._build_ui()
        self._apply_style()

        QShortcut(QKeySequence("Escape"), self, self.reject)

    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        # ---------- Заголовок ----------
        title = QLabel("ИМПОРТ КОДОВ ЧЗ (ФАЙЛ ЦРПТ)")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "color:#e6edf3; font-size:14px; font-weight:600; letter-spacing:2px;"
        )
        root.addWidget(title)

        subtitle = QLabel(
            "Загрузите файл «КМ для поставщика» (.xlsx), "
            "выберите точку и подтвердите импорт."
        )
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color:#8b97a6; font-size:12px;")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        root.addSpacing(6)

        # ---------- Выбор файла ----------
        file_card = QFrame()
        file_card.setObjectName("card")
        fv = QVBoxLayout(file_card)
        fv.setContentsMargins(16, 12, 16, 12)
        fv.setSpacing(8)

        fv.addWidget(self._label("Файл .xlsx:"))

        file_row = QHBoxLayout()
        file_row.setSpacing(8)

        self.file_input = QLabel("(файл не выбран)")
        self.file_input.setStyleSheet(
            "color:#8b97a6; font-family:Consolas,monospace; font-size:11px; "
            "background:#1c2530; padding:8px 12px; border-radius:6px; "
            "border:1px solid #2a3542;"
        )
        self.file_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        file_row.addWidget(self.file_input, stretch=1)

        self.btn_choose = QPushButton("📁 Выбрать файл")
        self.btn_choose.setProperty("primary", True)
        self.btn_choose.clicked.connect(self._choose_file)
        file_row.addWidget(self.btn_choose)

        fv.addLayout(file_row)
        root.addWidget(file_card)

        # ---------- Выбор точки ----------
        point_card = QFrame()
        point_card.setObjectName("card")
        pv = QHBoxLayout(point_card)
        pv.setContentsMargins(16, 12, 16, 12)
        pv.setSpacing(12)

        pv.addWidget(self._label("Точка (линия):"))

        self.point_combo = QComboBox()
        self.point_combo.setMinimumWidth(240)
        for p in self.store.points:
            self.point_combo.addItem(
                f"{p['id']} — {p['name']}", p["id"]
            )
        # По умолчанию — текущая точка
        idx = self.point_combo.findData(self.store.current_point)
        if idx >= 0:
            self.point_combo.setCurrentIndex(idx)
        pv.addWidget(self.point_combo)

        pv.addStretch(1)
        root.addWidget(point_card)

        # ---------- Сводка ----------
        self.summary_label = QLabel("Выберите файл для предпросмотра")
        self.summary_label.setStyleSheet(
            "color:#8b97a6; font-size:12px; padding:8px; "
            "background:rgba(59,130,246,.08); border-radius:6px;"
        )
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        # ---------- Таблица GTIN ----------
        self.gtin_table = QTableWidget(0, 3)
        self.gtin_table.setHorizontalHeaderLabels(
            ["GTIN", "Артикул", "Кодов"]
        )
        hh = self.gtin_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.gtin_table.verticalHeader().setVisible(False)
        self.gtin_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.gtin_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.gtin_table.setShowGrid(False)
        root.addWidget(self.gtin_table, stretch=1)

        # ---------- Лог импорта ----------
        self.result_log = QTextEdit()
        self.result_log.setReadOnly(True)
        self.result_log.setFixedHeight(80)
        self.result_log.setStyleSheet(
            "background:#1c2530; color:#e6edf3; "
            "font-family:Consolas,monospace; font-size:11px; "
            "border:1px solid #2a3542; border-radius:6px; padding:6px;"
        )
        self.result_log.hide()
        root.addWidget(self.result_log)

        # ---------- Кнопки ----------
        buttons = QHBoxLayout()
        buttons.setSpacing(10)

        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.setMinimumHeight(40)
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_import = QPushButton("✅ Импортировать")
        self.btn_import.setProperty("primary", True)
        self.btn_import.setMinimumHeight(40)
        self.btn_import.setEnabled(False)
        self.btn_import.setDefault(True)
        self.btn_import.clicked.connect(self._do_import)

        buttons.addWidget(self.btn_cancel, stretch=1)
        buttons.addWidget(self.btn_import, stretch=2)
        root.addLayout(buttons)

    def _label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#e6edf3; font-size:12px;")
        return lbl

    # ------------------------------------------------------------------
    def _apply_style(self):
        self.setStyleSheet("""
            QDialog#importCodesWindow {
                background-color: #0f1419;
            }
            QDialog#importCodesWindow QFrame#card {
                background-color: #161c24;
                border: 1px solid #2a3542;
                border-radius: 10px;
            }
        """)

    # ------------------------------------------------------------------
    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл ЦРПТ (.xlsx)",
            "",
            "Excel (*.xlsx *.xlsm);;Все файлы (*.*)"
        )
        if not path:
            return

        self._file_path = path
        self.file_input.setText(Path(path).name)
        self._load_preview(path)

    # ------------------------------------------------------------------
    def _load_preview(self, path: str):
        """Загрузить и распарсить файл, показать предпросмотр."""
        try:
            from cz_import import parse_xlsx
        except ImportError as e:
            QMessageBox.critical(
                self, "Ошибка",
                f"Не найден модуль cz_import:\n{e}\n\n"
                f"Установите openpyxl: pip install openpyxl"
            )
            return

        try:
            rows = parse_xlsx(path)
        except Exception as e:
            log.error("Ошибка парсинга xlsx: %s", e)
            QMessageBox.critical(self, "Ошибка парсинга",
                                 f"Не удалось прочитать файл:\n{e}")
            return

        self._parsed_rows = rows

        if not rows:
            self.summary_label.setText("⚠ Файл пуст или не распознан")
            self.btn_import.setEnabled(False)
            return

        # Заказ
        self._order_name = rows[0].get("order") or Path(path).stem

        # Группировка по GTIN
        gtins = {}
        for r in rows:
            g = r["gtin"]
            if g not in gtins:
                gtins[g] = {"count": 0, "article": r.get("article") or "—"}
            gtins[g]["count"] += 1

        # Заполняем таблицу
        t = self.gtin_table
        t.setRowCount(0)
        for g, info in sorted(gtins.items()):
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, _cell(g, mono=True))
            t.setItem(r, 1, _cell(info["article"]))
            t.setItem(r, 2, _cell(info["count"], mono=True))

        # Сводка
        point = self.point_combo.currentData()
        self.summary_label.setText(
            f"Заказ: {self._order_name}\n"
            f"GTIN: {len(gtins)}   ·   Всего кодов: {len(rows)}\n"
            f"Файл: {Path(path).name}\n"
            f"Точка импорта: {point}"
        )
        self.summary_label.setStyleSheet(
            "color:#93c5fd; font-size:12px; padding:8px; "
            "background:rgba(59,130,246,.08); border-radius:6px;"
        )

        self.btn_import.setEnabled(True)
        log.info("Предпросмотр загружен: %s GTIN, %s кодов",
                 len(gtins), len(rows))

    # ------------------------------------------------------------------
    def _do_import(self):
        if not self._parsed_rows:
            return

        point = self.point_combo.currentData()
        if not point:
            QMessageBox.warning(self, "Ошибка", "Не выбрана точка")
            return

        # Подтверждение
        reply = QMessageBox.question(
            self, "Подтвердите импорт",
            f"Импортировать {len(self._parsed_rows)} кодов\n"
            f"на точку {point}?\n\n"
            f"Заказ: {self._order_name}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        # Импорт
        result = self.store.import_codes_from_file(
            self._file_path,
            point=point,
            user=self.store.current_operator,
        )

        if not result["ok"]:
            QMessageBox.critical(self, "Ошибка импорта", result["info"])
            return

        # Показываем лог
        self.result_log.show()
        log_lines = [result["info"]]
        if result.get("errors"):
            log_lines.append("")
            log_lines.append("Ошибки:")
            for e in result["errors"][:20]:
                log_lines.append(f"  {e}")
            if len(result["errors"]) > 20:
                log_lines.append(f"  ...и ещё {len(result['errors']) - 20}")
        self.result_log.setPlainText("\n".join(log_lines))

        # Toast-подобное сообщение
        QMessageBox.information(
            self, "Импорт завершён",
            f"Добавлено: {result['added']}\n"
            f"Пропущено (дубли): {result['skipped']}\n"
            f"Ошибок: {len(result['errors'])}\n\n"
            f"Точка: {point}\n"
            f"Заказ: {result['order']}"
        )

        log.info("Импорт выполнен: %s", result["info"])
        self.accept()