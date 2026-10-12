"""
Чтение сканеров штрихкодов.

Поддерживает:
  - COM-порт (USB-COM / Bluetooth SPP) — через pyserial
  - TCP/IP — через socket
  - HID (USB-клавиатура / Bluetooth-HID) — работает как раньше,
    через поле ввода; здесь не обрабатывается

Каждый COM-порт читается в своём потоке. Коды передаются в UI через
Qt-сигналы. Защита от двойного скана: часть Bluetooth-сканеров шлёт
код дважды — повтор в пределах SCAN_DEDUP_WINDOW игнорируется.
"""
import socket
import threading
import time
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from logger import get_logger

log = get_logger("scanner")


# Окно защиты от двойного скана (в секундах)
SCAN_DEDUP_WINDOW = 1.5

# Таймаут чтения COM-порта
COM_READ_TIMEOUT = 0.5

# Максимальная длина буфера одного кода
MAX_CODE_LENGTH = 512


# ==========================================================
# Список доступных COM-портов
# ==========================================================
def list_com_ports() -> list:
    """
    Вернуть список доступных COM-портов.
    Каждый элемент: {"device": "COM3", "description": "...", "hwid": "..."}
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        log.warning("pyserial не установлен — список COM-портов пуст")
        return []

    result = []
    for p in list_ports.comports():
        result.append({
            "device": p.device,
            "description": p.description or "",
            "hwid": p.hwid or "",
            "manufacturer": getattr(p, "manufacturer", "") or "",
        })
    return result


def looks_like_scanner(port: dict) -> bool:
    """
    Грубая эвристика: похож ли COM-порт на сканер.
    Используется для пометки ⭐ в диалоге выбора порта.
    """
    text = (
        (port.get("description") or "") + " " +
        (port.get("manufacturer") or "") + " " +
        (port.get("hwid") or "")
    ).lower()

    markers = [
        "scanner", "barcode", "zebra", "honeywell", "datalogic",
        "symbol", "motorola", "newland", "mindeo", "bluetooth",
    ]
    return any(m in text for m in markers)


# ==========================================================
# Базовый класс читателя
# ==========================================================
class BaseReader(QObject):
    """Базовый класс читателя — только интерфейс."""

    code_received = Signal(str)     # пришёл готовый код
    error = Signal(str)             # ошибка чтения

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def is_running(self) -> bool:
        raise NotImplementedError


# ==========================================================
# Читатель COM-порта (USB-COM / Bluetooth SPP)
# ==========================================================
class ComReader(BaseReader):
    """
    Читает один COM-порт в отдельном потоке.
    Разбирает поток на коды по CR/LF, склеивает разорванные посылки.
    """

    def __init__(self, port: str, baudrate: int = 9600,
                 scanner_name: str = "", parent=None):
        super().__init__(parent)
        self.port = port
        self.baudrate = baudrate
        self.scanner_name = scanner_name or port

        self._thread = None
        self._stop_flag = threading.Event()
        self._serial = None

        # Буфер незавершённого кода
        self._buffer = ""

        # Последний отданный код и время — для защиты от дублей
        self._last_code = None
        self._last_code_at = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"ComReader-{self.port}", daemon=True
        )
        self._thread.start()
        log.info("COM-читатель запущен: %s (%s), %s бод",
                 self.scanner_name, self.port, self.baudrate)

    def stop(self):
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        log.info("COM-читатель остановлен: %s", self.scanner_name)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        try:
            import serial
        except ImportError:
            self.error.emit("pyserial не установлен")
            return

        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=COM_READ_TIMEOUT,
            )
        except Exception as e:
            log.error("Не удалось открыть %s: %s", self.port, e)
            self.error.emit(f"Не удалось открыть {self.port}: {e}")
            return

        try:
            while not self._stop_flag.is_set():
                try:
                    chunk = self._serial.read(64)
                except Exception as e:
                    if not self._stop_flag.is_set():
                        log.error("Ошибка чтения %s: %s", self.port, e)
                        self.error.emit(f"Ошибка чтения {self.port}: {e}")
                    break

                if not chunk:
                    continue

                try:
                    text = chunk.decode("utf-8", errors="replace")
                except Exception:
                    text = chunk.decode("latin-1", errors="replace")

                self._process_text(text)
        finally:
            try:
                if self._serial and self._serial.is_open:
                    self._serial.close()
            except Exception:
                pass

    def _process_text(self, text: str):
        """
        Разбор текста на коды.
        Код = последовательность до \r или \n.
        Разорванные посылки склеиваются через self._buffer.
        """
        for ch in text:
            if ch in ("\r", "\n"):
                code = self._buffer.strip()
                self._buffer = ""
                if code:
                    self._emit_code(code)
            else:
                self._buffer += ch
                if len(self._buffer) > MAX_CODE_LENGTH:
                    # Слишком длинный — сбрасываем, что-то не так
                    log.warning("Слишком длинный код — сброс буфера")
                    self._buffer = ""

    def _emit_code(self, code: str):
        """Защита от двойного скана + отправка сигнала."""
        now = time.time()

        if (self._last_code == code and
                now - self._last_code_at < SCAN_DEDUP_WINDOW):
            log.debug("Дубль отброшен: %s", code[:40])
            return

        self._last_code = code
        self._last_code_at = now

        log.debug("Код от %s: %s", self.scanner_name, code[:60])
        self.code_received.emit(code)


# ==========================================================
# Читатель сетевого сканера (TCP)
# ==========================================================
class TcpReader(BaseReader):
    """
    Подключается к сканеру по TCP/IP и читает данные.
    Формат потока — тот же: коды через CR/LF.
    """

    def __init__(self, ip: str, port: int = 9100,
                 scanner_name: str = "", parent=None):
        super().__init__(parent)
        self.ip = ip
        self.port = port
        self.scanner_name = scanner_name or f"{ip}:{port}"

        self._thread = None
        self._stop_flag = threading.Event()
        self._socket = None
        self._buffer = ""
        self._last_code = None
        self._last_code_at = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"TcpReader-{self.ip}", daemon=True
        )
        self._thread.start()
        log.info("TCP-читатель запущен: %s", self.scanner_name)

    def stop(self):
        self._stop_flag.set()
        try:
            if self._socket:
                self._socket.close()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)
        log.info("TCP-читатель остановлен: %s", self.scanner_name)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        try:
            self._socket = socket.create_connection(
                (self.ip, self.port), timeout=5.0
            )
            self._socket.settimeout(COM_READ_TIMEOUT)
        except Exception as e:
            log.error("Не удалось подключиться к %s: %s",
                      self.scanner_name, e)
            self.error.emit(f"Не удалось подключиться к {self.scanner_name}: {e}")
            return

        try:
            while not self._stop_flag.is_set():
                try:
                    chunk = self._socket.recv(64)
                except socket.timeout:
                    continue
                except Exception as e:
                    if not self._stop_flag.is_set():
                        log.error("Ошибка приёма %s: %s", self.scanner_name, e)
                        self.error.emit(f"Ошибка {self.scanner_name}: {e}")
                    break

                if not chunk:
                    # Соединение закрыто
                    break

                try:
                    text = chunk.decode("utf-8", errors="replace")
                except Exception:
                    text = chunk.decode("latin-1", errors="replace")

                self._process_text(text)
        finally:
            try:
                if self._socket:
                    self._socket.close()
            except Exception:
                pass

    def _process_text(self, text: str):
        for ch in text:
            if ch in ("\r", "\n"):
                code = self._buffer.strip()
                self._buffer = ""
                if code:
                    self._emit_code(code)
            else:
                self._buffer += ch
                if len(self._buffer) > MAX_CODE_LENGTH:
                    self._buffer = ""

    def _emit_code(self, code: str):
        now = time.time()
        if (self._last_code == code and
                now - self._last_code_at < SCAN_DEDUP_WINDOW):
            return
        self._last_code = code
        self._last_code_at = now
        log.debug("Код от %s: %s", self.scanner_name, code[:60])
        self.code_received.emit(code)


# ==========================================================
# Менеджер читателей
# ==========================================================
class ScannerManager(QObject):
    """
    Управляет всеми активными читателями сканеров.
    Один читатель — один физический сканер.
    """

    code_received = Signal(str, str)    # (scanner_name, code)
    error = Signal(str, str)            # (scanner_name, error_text)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._readers = {}   # scanner_name -> BaseReader

    def start_com(self, port: str, baudrate: int = 9600,
                  scanner_name: str = "") -> bool:
        """Запустить чтение COM-порта."""
        name = scanner_name or port
        if name in self._readers and self._readers[name].is_running():
            log.warning("Читатель %s уже запущен", name)
            return False

        reader = ComReader(port=port, baudrate=baudrate,
                           scanner_name=name)
        reader.code_received.connect(
            lambda code, n=name: self.code_received.emit(n, code)
        )
        reader.error.connect(
            lambda err, n=name: self.error.emit(n, err)
        )
        reader.start()
        self._readers[name] = reader
        return True

    def start_tcp(self, ip: str, port: int = 9100,
                  scanner_name: str = "") -> bool:
        """Запустить чтение сетевого сканера."""
        name = scanner_name or f"{ip}:{port}"
        if name in self._readers and self._readers[name].is_running():
            log.warning("Читатель %s уже запущен", name)
            return False

        reader = TcpReader(ip=ip, port=port, scanner_name=name)
        reader.code_received.connect(
            lambda code, n=name: self.code_received.emit(n, code)
        )
        reader.error.connect(
            lambda err, n=name: self.error.emit(n, err)
        )
        reader.start()
        self._readers[name] = reader
        return True

    def stop(self, scanner_name: str):
        """Остановить читатель по имени."""
        reader = self._readers.get(scanner_name)
        if reader:
            reader.stop()
            del self._readers[scanner_name]

    def stop_all(self):
        """Остановить все читатели (при закрытии приложения)."""
        for name in list(self._readers.keys()):
            self.stop(name)
        log.info("Все читатели остановлены")

    def running_names(self) -> list:
        """Имена активных читателей."""
        return [n for n, r in self._readers.items() if r.is_running()]

    def count_running(self) -> int:
        return len(self.running_names())