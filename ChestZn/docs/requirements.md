\# Требования и установка



\## Что нужно для работы



| Компонент | Версия | Зачем |

|---|---|---|

| Windows | 10 / 11 | Целевая операционная система |

| Python | 3.9 – 3.11 | Только для запуска из исходников |

| Ghostscript | 10.x | Рендер GS1 DataMatrix |

| Принтер | Любой | Zebra, TSC, Godex, офисный HP/Canon |



\## Установка Ghostscript (обязательно)



Без Ghostscript коды DataMatrix \*\*не будут рендериться\*\*.



1\. Откройте https://www.ghostscript.com/releases/gsdnld.html

2\. Найдите раздел \*\*Ghostscript AGPL Release\*\*.

3\. Скачайте \*\*Windows (64 bit)\*\* — файл `gs10080w64.exe`.

4\. Запустите установщик.

5\. \*\*Важно:\*\* поставьте галочку \*\*Add Ghostscript to PATH\*\*.

6\. После установки \*\*перезапустите терминал и VS\*\*.



Проверка:



&#x20;   gswin64c --version



Должно вывести номер версии, например `10.08.0`.



\## Установка Python (для запуска из исходников)



1\. Скачайте с https://www.python.org/downloads/

2\. При установке поставьте галочку \*\*Add Python to PATH\*\*.



Проверка:



&#x20;   python --version



\## Установка зависимостей проекта



&#x20;   cd ChestZn\\ChestZn

&#x20;   python -m venv env

&#x20;   .\\env\\Scripts\\Activate.ps1

&#x20;   pip install -r requirements.txt



\## Запуск



&#x20;   python main.py



Или двойной клик по `ChestZn.exe` (если собрано).



\## Что дальше



\- Если принтер ещё не настроен — см. \[Настройка принтера](printer.md).

\- Если Ghostscript не ставится — см. \[Установка Ghostscript](install-gs.md).

\- Если что-то не работает — см. \[Типичные проблемы](problems.md).

