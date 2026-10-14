"""
ChestZn — Интерфейс управления сетевыми узлами и привязки «Сканер <-> Принтер» (master_devices_window.py)
Позволяет старшему смены:
1. Видеть только реально подключенные принтеры и сканеры (без пустых портов и виртуальных принтеров).
2. Назначать каждому сканеру конкретный принтер по правилу «1 сканер = 1 принтер»:
   - Прямо в строке сканера через выпадающий список (QComboBox).
   - Либо через отдельное окно «🔗 Привязать принтер к сканеру» (или двойным кликом по сканеру).
3. Сквозная синхронизация выбранного принтера:
   - В файл devices_config.json (секции bindings и pairs).
   - В конфигурацию модуля печати printer.py (load_config / save_config / set_printer_for_point).
   - В базу данных Store (таблицы printers и scanners), чтобы окно оператора всегда печатало строго на выбранный принтер.
"""

import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QDialog, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFormLayout, QTreeWidget, QTreeWidgetItem, QLabel,
    QPushButton, QCheckBox, QLineEdit, QComboBox, QHeaderView,
    QGroupBox, QMessageBox, QFrame
)

from device_discovery import (
    detect_local_printers, detect_local_scanners,
    check_computer_online, AddComputerByIPDialog
)

logger = logging.getLogger("ChestZn.MasterDevices")

CONFIG_PATH = Path("devices_config.json")


def sync_bindings_to_system(
    bindings: Dict[str, str],
    connected_printers: Optional[List[Dict[str, Any]]] = None,
    store: Any = None
):
    """
    Сквозная синхронизация привязок «Сканер -> Принтер» со всеми подсистемами ChestZn:
    1. Обновляет конфиг printer.py (load_config / save_config / set_printer_for_point).
    2. Обновляет таблицы printers и scanners в базе данных Store (SQLite),
       чтобы при сканировании печать гарантированно уходила на выбранный старшим смены принтер.
    """
    if not bindings:
        return

    # Определяем основной назначенный принтер (приоритет: USB-HID, затем первый активный)
    primary_printer = bindings.get("USB-HID") or ""
    if not primary_printer:
        for s_k, p_n in bindings.items():
            if p_n:
                primary_printer = p_n
                break

    if not primary_printer:
        return

    # 1. Синхронизация с конфигурацией printer.py
    try:
        from printer import load_config, save_config, set_printer_for_point, detect_language
        cfg = load_config()
        lang = detect_language(primary_printer)

        p_entry = {
            "printer_name": primary_printer,
            "printer_ip": "",
            "printer_port": 9100,
            "printer_lang": lang,
            "label_width_mm": float(cfg.get("label_width_mm", 58)),
            "label_height_mm": float(cfg.get("label_height_mm", 40)),
            "dpi": int(cfg.get("dpi", 203)),
            "copies": int(cfg.get("copies", 1)),
        }

        cfg["printer_name"] = primary_printer
        cfg["printer_ip"] = ""
        cfg["printer_lang"] = lang
        cfg["scanner_bindings"] = dict(bindings)

        points_to_update = set((cfg.get("printers") or {}).keys())
        points_to_update.update(["T-01", "T-02", "T-03"])
        if store is not None:
            if hasattr(store, "points"):
                for pt in store.points:
                    if isinstance(pt, dict) and pt.get("id"):
                        points_to_update.add(pt["id"])
            if getattr(store, "current_point", None):
                points_to_update.add(store.current_point)

        for pt_id in points_to_update:
            cfg = set_printer_for_point(cfg, pt_id, dict(p_entry))

        save_config(cfg)
        logger.info("Конфигурация printer.py синхронизирована: активный принтер -> %s", primary_printer)
    except Exception as e:
        logger.error("Ошибка синхронизации с printer.py: %s", e)

    # 2. Синхронизация с таблицами printers и scanners в Store (SQLite)
    try:
        if store is None:
            return

        try:
            from printer import detect_language
        except ImportError:
            detect_language = lambda name: "auto"

        computers = store.list_computers() if hasattr(store, "list_computers") else []
        real_printer_names = {p["name"] for p in (connected_printers or [])}

        for comp in computers:
            comp_id = comp["id"]
            existing_printers = store.list_printers_for_computer(comp_id)
            pr_by_name = {p["name"]: p for p in existing_printers}

            bound_printer_ids: Dict[str, int] = {}

            for s_key, p_name in bindings.items():
                if not p_name:
                    continue
                lang = detect_language(p_name)

                if p_name in pr_by_name:
                    pr_obj = pr_by_name[p_name]
                    bound_printer_ids[p_name] = pr_obj["id"]
                    store.update_printer(
                        pr_obj["id"],
                        name=p_name,
                        ip="",
                        port=pr_obj.get("port", 9100),
                        lang=lang,
                        width_mm=pr_obj.get("width_mm", 58),
                        height_mm=pr_obj.get("height_mm", 40),
                        dpi=pr_obj.get("dpi", 203),
                        copies=pr_obj.get("copies", 1),
                        online=True
                    )
                else:
                    store.create_printer(
                        computer_id=comp_id,
                        name=p_name,
                        ip="",
                        port=9100,
                        lang=lang,
                        width_mm=58,
                        height_mm=40,
                        dpi=203,
                        copies=1,
                        online=True
                    )
                    existing_printers = store.list_printers_for_computer(comp_id)
                    pr_by_name = {p["name"]: p for p in existing_printers}
                    if p_name in pr_by_name:
                        bound_printer_ids[p_name] = pr_by_name[p_name]["id"]

            # Обновляем привязки у существующих и новых сканеров компьютера
            existing_scanners = store.list_scanners_for_computer(comp_id)
            for s_key, p_name in bindings.items():
                target_pid = bound_printer_ids.get(p_name) if p_name else None
                matched_sc = None
                for sc in existing_scanners:
                    sc_com = (sc.get("com_port") or "").strip()
                    if s_key.upper().startswith("COM") and sc_com.upper() == s_key.upper():
                        matched_sc = sc
                        break
                    elif s_key == "USB-HID" and sc.get("connect_type") in ("usb", "usb-hid", "hid"):
                        matched_sc = sc
                        break
                    elif sc.get("name") == s_key:
                        matched_sc = sc
                        break

                if matched_sc:
                    store.update_scanner(
                        matched_sc["id"],
                        name=matched_sc["name"],
                        operator=matched_sc.get("operator", ""),
                        printer_id=target_pid,
                        connect_type=matched_sc["connect_type"],
                        com_port=matched_sc.get("com_port", ""),
                        bt_address=matched_sc.get("bt_address", ""),
                        ip=matched_sc.get("ip", ""),
                        port=matched_sc.get("port", 9100),
                        enabled=True
                    )
                elif target_pid is not None:
                    is_com = s_key.upper().startswith("COM")
                    store.create_scanner(
                        computer_id=comp_id,
                        name=f"Сканер ({s_key})",
                        operator="Оператор",
                        printer_id=target_pid,
                        connect_type="usb-com" if is_com else "usb",
                        com_port=s_key if is_com else "",
                        bt_address="",
                        ip="",
                        port=9100,
                        enabled=True
                    )

            # Перепривязываем старые демо-сканеры этого ПК, которые указывали на несуществующие принтеры
            if primary_printer in bound_printer_ids:
                primary_pid = bound_printer_ids[primary_printer]
                existing_scanners = store.list_scanners_for_computer(comp_id)
                for sc in existing_scanners:
                    sc_com = (sc.get("com_port") or "").strip()
                    if sc_com and sc_com in bindings:
                        continue
                    cur_pid = sc.get("printer_id")
                    cur_pr = next((p for p in existing_printers if p["id"] == cur_pid), None)
                    if not cur_pr or (real_printer_names and cur_pr["name"] not in real_printer_names) or len(bindings) == 1:
                        store.update_scanner(
                            sc["id"],
                            name=sc["name"],
                            operator=sc.get("operator", ""),
                            printer_id=primary_pid,
                            connect_type=sc["connect_type"],
                            com_port=sc.get("com_port", ""),
                            bt_address=sc.get("bt_address", ""),
                            ip=sc.get("ip", ""),
                            port=sc.get("port", 9100),
                            enabled=sc.get("enabled", True)
                        )
    except Exception as e:
        logger.error("Ошибка синхронизации привязок со Store: %s", e)


class AssignPrinterDialog(QDialog):
    """
    Диалоговое окно привязки принтера к конкретному сканеру (для старшего смены)
    """
    def __init__(
        self,
        scanners: List[Dict[str, Any]],
        printers: List[Dict[str, Any]],
        current_bindings: Dict[str, str],
        selected_scanner_key: Optional[str] = None,
        parent=None
    ):
        super().__init__(parent)
        self.setWindowTitle("Привязка принтера к сканеру (1 Сканер = 1 Принтер)")
        self.setMinimumWidth(520)
        self.scanners = scanners
        self.printers = printers
        self.bindings = dict(current_bindings or {})
        self.selected_scanner_key = selected_scanner_key
        self.result_binding = None  # (scanner_key, printer_name)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        info = QLabel(
            "Выберите сканер и укажите, какой физический принтер будет печатать этикетки "
            "при сканировании этим устройством.\n"
            "Правило: за каждым сканером закрепляется строго 1 принтер."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #93c5fd; font-size: 12px;")
        layout.addWidget(info)

        form_box = QGroupBox("Настройка рабочей пары")
        form = QFormLayout(form_box)
        form.setSpacing(10)

        self.cmb_scanner = QComboBox()
        for sc in self.scanners:
            key = sc.get("device") or sc.get("name")
            label = f"📡 {sc['name']} [{sc['device']}]"
            self.cmb_scanner.addItem(label, key)

        if self.selected_scanner_key:
            idx = self.cmb_scanner.findData(self.selected_scanner_key)
            if idx >= 0:
                self.cmb_scanner.setCurrentIndex(idx)

        self.cmb_scanner.currentIndexChanged.connect(self._update_printer_combo)
        form.addRow("Сканер:", self.cmb_scanner)

        self.cmb_printer = QComboBox()
        form.addRow("Присвоить принтер:", self.cmb_printer)

        layout.addWidget(form_box)

        self.lbl_conflict_warn = QLabel("")
        self.lbl_conflict_warn.setWordWrap(True)
        self.lbl_conflict_warn.setStyleSheet("color: #f59e0b; font-size: 12px;")
        layout.addWidget(self.lbl_conflict_warn)
        self.cmb_printer.currentIndexChanged.connect(self._check_conflict_warning)

        self._update_printer_combo()

        btns = QHBoxLayout()
        btns.addStretch()

        self.btn_save = QPushButton("💾 Присвоить принтер")
        self.btn_save.setStyleSheet(
            "background-color: #2e7d32; color: white; font-weight: bold; padding: 7px 16px;"
        )
        self.btn_save.clicked.connect(self._on_save)
        btns.addWidget(self.btn_save)

        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.clicked.connect(self.reject)
        btns.addWidget(self.btn_cancel)

        layout.addLayout(btns)

    def _update_printer_combo(self):
        scanner_key = self.cmb_scanner.currentData()
        current_printer = self.bindings.get(scanner_key, "")

        self.cmb_printer.blockSignals(True)
        self.cmb_printer.clear()
        self.cmb_printer.addItem("— Не назначен (отвязать принтер) —", "")

        for pr in self.printers:
            p_name = pr["name"]
            occupied_by = None
            for s_key, bound_p in self.bindings.items():
                if bound_p == p_name and s_key != scanner_key:
                    occupied_by = s_key
                    break

            if occupied_by:
                display = f"🖨 {p_name} ({pr['port']}) — [Занят: {occupied_by}]"
            else:
                display = f"🖨 {p_name} ({pr['port']}) — [Свободен]"

            self.cmb_printer.addItem(display, p_name)

        idx = self.cmb_printer.findData(current_printer)
        if idx >= 0:
            self.cmb_printer.setCurrentIndex(idx)
        else:
            self.cmb_printer.setCurrentIndex(0)
        self.cmb_printer.blockSignals(False)
        self._check_conflict_warning()

    def _check_conflict_warning(self):
        scanner_key = self.cmb_scanner.currentData()
        printer_name = self.cmb_printer.currentData()
        if not printer_name:
            self.lbl_conflict_warn.setText("")
            return

        for s_key, bound_p in self.bindings.items():
            if bound_p == printer_name and s_key != scanner_key:
                self.lbl_conflict_warn.setStyleSheet("color: #f59e0b; font-size: 12px;")
                self.lbl_conflict_warn.setText(
                    f"⚠ Внимание: принтер «{printer_name}» сейчас закреплён за сканером «{s_key}». "
                    f"При сохранении он будет автоматически перепривязан к выбранному сканеру."
                )
                return
        self.lbl_conflict_warn.setText("✓ Принтер свободен и готов к привязке.")
        self.lbl_conflict_warn.setStyleSheet("color: #22c55e; font-size: 12px;")

    def _on_save(self):
        scanner_key = self.cmb_scanner.currentData()
        printer_name = self.cmb_printer.currentData()
        if not scanner_key:
            QMessageBox.warning(self, "Ошибка", "Не выбран сканер")
            return
        self.result_binding = (scanner_key, printer_name)
        self.accept()


class MasterDevicesWidget(QWidget):
    """
    Панель управления сетевыми компьютерами, физическим оборудованием и привязкой пар (1 Сканер = 1 Принтер)
    """
    bindings_updated = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.store = self._resolve_store(parent)
        self.config = self._load_config()
        self._last_printers: List[Dict[str, Any]] = []
        self._last_scanners: List[Dict[str, Any]] = []
        self._init_ui()
        self.refresh_all()

    @staticmethod
    def _resolve_store(parent):
        curr = parent
        while curr is not None:
            if hasattr(curr, "store") and curr.store is not None:
                return curr.store
            curr = curr.parent() if hasattr(curr, "parent") and callable(curr.parent) else None
        return None

    def _load_config(self) -> Dict[str, Any]:
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    data.setdefault("bindings", {})
                    data.setdefault("pairs", [])
                    return data
            except Exception as e:
                logger.error("Ошибка загрузки конфигурации: %s", e)

        return {
            "master_node": "MASTER-PC",
            "active_only_filter": True,
            "ping_timeout_ms": 600,
            "bindings": {},
            "pairs": [],
            "computers": [
                {
                    "id": 1,
                    "name": "МАСТЕР-ПК (Этот компьютер)",
                    "ip": "127.0.0.1",
                    "role": "Сервер маркировки",
                    "sync_enabled": True
                }
            ]
        }

    def _save_config(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Ошибка сохранения конфигурации: %s", e)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        head_layout = QHBoxLayout()
        title_box = QVBoxLayout()
        lbl_title = QLabel("Управление оборудованием и привязка пар (Сканер ➔ Принтер)")
        lbl_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff;")
        lbl_sub = QLabel(
            "Старший смены может назначить каждому сканеру свой принтер прямо в таблице или кнопкой «Привязать принтер»"
        )
        lbl_sub.setStyleSheet("font-size: 12px; color: #888;")
        title_box.addWidget(lbl_title)
        title_box.addWidget(lbl_sub)
        head_layout.addLayout(title_box)
        head_layout.addStretch()

        self.btn_bind_pair = QPushButton("🔗 Привязать принтер к сканеру")
        self.btn_bind_pair.setStyleSheet(
            "background-color: #1976d2; color: white; font-weight: bold; padding: 7px 14px;"
        )
        self.btn_bind_pair.clicked.connect(self._on_open_bind_dialog)
        head_layout.addWidget(self.btn_bind_pair)

        self.btn_add_ip = QPushButton("➕ Добавить компьютер по IP")
        self.btn_add_ip.setStyleSheet(
            "background-color: #2e7d32; color: white; font-weight: bold; padding: 7px 14px;"
        )
        self.btn_add_ip.clicked.connect(self._on_add_computer_ip)
        head_layout.addWidget(self.btn_add_ip)

        self.btn_refresh = QPushButton("🔄 Опросить устройства и сеть")
        self.btn_refresh.setStyleSheet("padding: 7px 14px;")
        self.btn_refresh.clicked.connect(self.refresh_all)
        head_layout.addWidget(self.btn_refresh)

        layout.addLayout(head_layout)

        # Панель быстрого теста сканирования
        test_scan_bar = QFrame()
        test_scan_bar.setStyleSheet(
            "background-color: #1a222d; border: 1px solid #2a3542; border-radius: 6px; padding: 6px;"
        )
        ts_layout = QHBoxLayout(test_scan_bar)
        ts_layout.setContentsMargins(10, 4, 10, 4)

        ts_label = QLabel("⚡ Проверить сканер:")
        ts_label.setStyleSheet("font-weight: bold; color: #93c5fd;")
        ts_layout.addWidget(ts_label)

        self.txt_test_scan = QLineEdit()
        self.txt_test_scan.setPlaceholderText("Кликните сюда и отсканируйте любой штрихкод для проверки...")
        self.txt_test_scan.returnPressed.connect(self._on_test_barcode_scanned)
        ts_layout.addWidget(self.txt_test_scan, stretch=1)

        self.lbl_scan_feedback = QLabel("Ожидание сканирования...")
        self.lbl_scan_feedback.setStyleSheet("color: #888; font-size: 11px;")
        ts_layout.addWidget(self.lbl_scan_feedback)

        layout.addWidget(test_scan_bar)

        filter_bar = QHBoxLayout()
        self.chk_online_only = QCheckBox("Показывать только актуальные (онлайн) компьютеры")
        self.chk_online_only.setChecked(self.config.get("active_only_filter", True))
        self.chk_online_only.toggled.connect(self._on_filter_toggled)
        filter_bar.addWidget(self.chk_online_only)

        filter_bar.addStretch()

        self.lbl_summary = QLabel("Опрос устройств...")
        self.lbl_summary.setStyleSheet("color: #aaa; font-size: 12px;")
        filter_bar.addWidget(self.lbl_summary)

        layout.addLayout(filter_bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([
            "Узел / Устройство",
            "Порт / IP-адрес",
            "Статус",
            "Тип",
            "Привязанный принтер / Сведения"
        ])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 130)
        self.tree.setColumnWidth(3, 140)
        self.tree.setColumnWidth(4, 280)
        self.tree.setAlternatingRowColors(False)
        self.tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        self.tree.setStyleSheet("""
            QTreeWidget {
                background: #161c24;
                border: 1px solid #2a3542;
                border-radius: 8px;
                color: #e6edf3;
            }
            QTreeWidget::item {
                padding: 7px 5px;
                border-bottom: 1px solid #232c37;
            }
            QTreeWidget::item:selected {
                background: rgba(59,130,246,0.3);
            }
            QHeaderView::section {
                background: #1b232e;
                color: #8b97a6;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #2a3542;
                font-weight: bold;
            }
        """)

        layout.addWidget(self.tree, stretch=1)

    def _on_filter_toggled(self, checked: bool):
        self.config["active_only_filter"] = checked
        self._save_config()
        self.refresh_all()

    def _on_test_barcode_scanned(self):
        text = self.txt_test_scan.text().strip()
        if text:
            self.lbl_scan_feedback.setText(f"✓ Успешно принят штрихкод: {text}")
            self.lbl_scan_feedback.setStyleSheet("color: #4caf50; font-weight: bold;")
            self.txt_test_scan.clear()

    def _assign_printer_to_scanner(self, scanner_key: str, printer_name: str):
        """
        Присваивает принтер сканеру с соблюдением правила 1 сканер = 1 принтер.
        Синхронизирует привязку с devices_config.json, printer.py и таблицами Store.
        """
        bindings: Dict[str, str] = self.config.setdefault("bindings", {})

        if not printer_name:
            bindings.pop(scanner_key, None)
        else:
            conflict_scanner = None
            for s_key, p_val in list(bindings.items()):
                if p_val == printer_name and s_key != scanner_key:
                    conflict_scanner = s_key
                    break

            if conflict_scanner:
                reply = QMessageBox.question(
                    self,
                    "Переназначение принтера",
                    f"Принтер «{printer_name}» уже привязан к сканеру «{conflict_scanner}».\n\n"
                    f"По правилу «1 сканер = 1 принтер» он будет отвязан от «{conflict_scanner}» "
                    f"и закреплён за «{scanner_key}».\nПродолжить?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                if reply != QMessageBox.Yes:
                    self.refresh_all()
                    return
                bindings.pop(conflict_scanner, None)

            bindings[scanner_key] = printer_name

        self._rebuild_engine_pairs()
        self._save_config()

        # Мгновенная синхронизация с printer.py и базой данных Store
        sync_bindings_to_system(
            bindings=self.config.get("bindings", {}),
            connected_printers=self._last_printers,
            store=self.store
        )

        self.refresh_all()
        self.bindings_updated.emit(self.config.get("pairs", []))

    def _rebuild_engine_pairs(self):
        """Формирует секцию 'pairs' в конфиге на основе текущих привязок"""
        bindings: Dict[str, str] = self.config.get("bindings", {})
        printers_map = {p["name"]: p for p in self._last_printers}
        scanners_map = {(s.get("device") or s.get("name")): s for s in self._last_scanners}

        pairs_list = []
        pair_id = 1
        for s_key, p_name in bindings.items():
            if not p_name:
                continue
            sc_info = scanners_map.get(s_key, {})
            pr_info = printers_map.get(p_name, {})

            conn_type = (sc_info.get("connect_type") or "usb-com").lower()
            pairs_list.append({
                "pair_id": pair_id,
                "name": f"Пост #{pair_id} ({s_key} ➔ {p_name})",
                "scanner_type": conn_type,
                "scanner_port": s_key,
                "printer_name": p_name,
                "printer_lang": pr_info.get("lang", "auto"),
                "enabled": True
            })
            pair_id += 1

        self.config["pairs"] = pairs_list

    def _on_open_bind_dialog(self, selected_scanner_key: Optional[str] = None):
        if not self._last_scanners:
            QMessageBox.warning(
                self,
                "Нет подключенных сканеров",
                "Не обнаружено ни одного подключенного сканера для привязки.\n"
                "Подключите сканер и нажмите «Опросить устройства и сеть»."
            )
            return

        if not self._last_printers:
            QMessageBox.warning(
                self,
                "Нет подключенных принтеров",
                "Не обнаружено ни одного подключенного принтера.\n"
                "Включите принтер и нажмите «Опросить устройства и сеть»."
            )
            return

        dlg = AssignPrinterDialog(
            scanners=self._last_scanners,
            printers=self._last_printers,
            current_bindings=self.config.get("bindings", {}),
            selected_scanner_key=selected_scanner_key,
            parent=self
        )
        if dlg.exec() == AssignPrinterDialog.Accepted and dlg.result_binding:
            s_key, p_name = dlg.result_binding
            self._assign_printer_to_scanner(s_key, p_name)

    def _on_tree_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.UserRole)
        if data and isinstance(data, tuple) and data[0] == "scanner":
            self._on_open_bind_dialog(selected_scanner_key=data[1])

    def refresh_all(self):
        """Опрос только реально подключенных устройств и проверка сети"""
        self.tree.clear()

        connected_printers = detect_local_printers(connected_only=True)
        connected_scanners = detect_local_scanners(connected_only=True)
        self._last_printers = connected_printers
        self._last_scanners = connected_scanners

        bindings: Dict[str, str] = self.config.setdefault("bindings", {})

        # При обновлении также синхронизируем существующие привязки с системой
        if bindings:
            sync_bindings_to_system(
                bindings=bindings,
                connected_printers=connected_printers,
                store=self.store
            )

        online_count = 0
        total_count = len(self.config.get("computers", []))

        for comp in self.config.get("computers", []):
            ip = comp.get("ip", "")
            is_online, msg, latency = check_computer_online(ip, timeout_ms=self.config.get("ping_timeout_ms", 600))
            comp["online"] = is_online
            comp["last_status"] = msg

            if is_online:
                online_count += 1
            elif self.chk_online_only.isChecked():
                continue

            comp_item = QTreeWidgetItem(self.tree)
            comp_item.setText(0, f"💻  {comp.get('name')}")
            comp_item.setText(1, ip)
            comp_item.setText(2, "🟢  онлайн" if is_online else "🔴  офлайн")
            comp_item.setText(3, comp.get("role", "Складской узел"))
            comp_item.setText(4, msg)

            font = comp_item.font(0)
            font.setBold(True)
            comp_item.setFont(0, font)

            if is_online:
                comp_item.setForeground(2, QBrush(QColor("#22c55e")))
            else:
                comp_item.setForeground(2, QBrush(QColor("#ef4444")))

            if ip in ("127.0.0.1", "localhost"):
                # 1. Принтеры
                if connected_printers:
                    for pr in connected_printers:
                        p_name = pr["name"]
                        bound_scanner = None
                        for s_key, b_pr in bindings.items():
                            if b_pr == p_name:
                                bound_scanner = s_key
                                break

                        p_item = QTreeWidgetItem(comp_item)
                        p_item.setText(0, f"    🖨  {p_name}")
                        p_item.setText(1, pr["port"])
                        p_item.setText(2, "🟢  готов" if pr["online"] else "🟡  " + pr["status_text"])
                        p_item.setText(3, f"Термопринтер ({pr['lang'].upper()})")
                        if bound_scanner:
                            p_item.setText(4, f"🔗 Закреплён за сканером: {bound_scanner}")
                            p_item.setForeground(4, QBrush(QColor("#93c5fd")))
                        else:
                            p_item.setText(4, "Свободен (не привязан)")
                            p_item.setForeground(4, QBrush(QColor("#888")))
                        p_item.setForeground(2, QBrush(QColor("#22c55e")))
                else:
                    empty_p = QTreeWidgetItem(comp_item)
                    empty_p.setText(0, "    🖨  Подключенные принтеры не найдены")
                    empty_p.setForeground(0, QBrush(QColor("#888")))

                # 2. Сканеры + интерактивный выбор принтера прямо в строке
                if connected_scanners:
                    for sc in connected_scanners:
                        s_key = sc.get("device") or sc.get("name")
                        bound_printer = bindings.get(s_key, "")

                        s_item = QTreeWidgetItem(comp_item)
                        s_item.setText(0, f"    📡  {sc['name']}")
                        s_item.setText(1, sc["device"])
                        s_item.setText(2, "🟢  подключен" if sc["online"] else "⚪  отключен")
                        s_item.setText(3, sc["connect_type"])
                        s_item.setData(0, Qt.UserRole, ("scanner", s_key))
                        s_item.setForeground(2, QBrush(QColor("#22c55e")))

                        cmb = QComboBox()
                        cmb.setStyleSheet(
                            "padding: 3px 6px; background: #1e2631; color: #e6edf3; "
                            "border: 1px solid #3b82f6; border-radius: 4px;"
                        )
                        cmb.addItem("— Выберите принтер —", "")
                        for pr in connected_printers:
                            p_name = pr["name"]
                            other_sc = None
                            for k, v in bindings.items():
                                if v == p_name and k != s_key:
                                    other_sc = k
                                    break
                            suffix = f" [занят: {other_sc}]" if other_sc else ""
                            cmb.addItem(f"🖨 {p_name}{suffix}", p_name)

                        idx = cmb.findData(bound_printer)
                        if idx >= 0:
                            cmb.setCurrentIndex(idx)

                        cmb.currentIndexChanged.connect(
                            lambda _, key=s_key, combo=cmb: self._assign_printer_to_scanner(key, combo.currentData())
                        )
                        self.tree.setItemWidget(s_item, 4, cmb)
                else:
                    empty_s = QTreeWidgetItem(comp_item)
                    empty_s.setText(0, "    📡  Подключенные сканеры не найдены")
                    empty_s.setForeground(0, QBrush(QColor("#888")))

            comp_item.setExpanded(is_online)

        active_pairs = len(self.config.get("pairs", []))
        self.lbl_summary.setText(
            f"ПК в сети: {online_count}/{total_count} | "
            f"Принтеров: {len(connected_printers)} | "
            f"Сканеров: {len(connected_scanners)} | "
            f"Активных пар (Сканер➔Принтер): {active_pairs}"
        )

    def _on_add_computer_ip(self):
        dlg = AddComputerByIPDialog(parent=self)
        if dlg.exec() == AddComputerByIPDialog.Accepted and dlg.result_data:
            data = dlg.result_data
            data["id"] = len(self.config.get("computers", [])) + 1
            self.config.setdefault("computers", []).append(data)
            self._save_config()
            self.refresh_all()
            QMessageBox.information(self, "Успешно", f"Компьютер «{data['name']}» ({data['ip']}) добавлен!")


class MasterDevicesMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChestZn — Монитор оборудования и привязка пар (Сканер ➔ Принтер)")
        self.resize(1080, 660)
        self.widget = MasterDevicesWidget(self)
        self.setCentralWidget(self.widget)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MasterDevicesMainWindow()
    win.show()
    sys.exit(app.exec())