import sys
import socket
import json
import os
import csv
from datetime import datetime
from collections import deque

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QProgressBar, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget, QScrollArea, QSplitter,
    QComboBox, QGroupBox
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QColor, QFont

import pyqtgraph as pg


# Настройки сервера
HOST = '0.0.0.0'
PORT = 9090

CPU_THRESHOLD  = 80
RAM_THRESHOLD  = 80
DISK_THRESHOLD = 90

HISTORY_LEN = 60

# Индексы колонок главной таблицы
COL_STATUS = 0
COL_NODE   = 1
COL_IP     = 2
COL_CPU    = 3
COL_RAM    = 4
COL_DISK   = 5
COL_TIME   = 6

# Индексы колонок таблицы процессов
PCOL_PID  = 0
PCOL_NAME = 1
PCOL_CPU  = 2
PCOL_RAM  = 3


# Сетевые потоки
class ClientHandlerThread(QThread):
    data_received = pyqtSignal(dict)

    def __init__(self, conn, addr):
        super().__init__()
        self.conn = conn
        self.addr = addr

    def run(self):
        print(f"[+] Агент подключён: {self.addr}")
        buffer = ""
        with self.conn:
            while True:
                data = self.conn.recv(4096)   # увеличен буфер — пакеты с процессами крупнее
                if not data:
                    print(f"[-] Агент {self.addr} отключился.")
                    break
                buffer += data.decode('utf-8')
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        metrics = json.loads(line)
                        metrics['ip']   = str(self.addr[0])
                        metrics['time'] = datetime.now().strftime('%H:%M:%S')
                        self.data_received.emit(metrics)
                    except json.JSONDecodeError:
                        print(f"[!] Повреждённый пакет от {self.addr}")


class ServerThread(QThread):
    new_client = pyqtSignal(object)

    def run(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((HOST, PORT))
            s.listen(10)
            print(f"[*] Сервер запущен. Слушаю {HOST}:{PORT}...")
            while True:
                conn, addr = s.accept()
                client = ClientHandlerThread(conn, addr)
                self.new_client.emit(client)
                client.start()


# Вспомогательные виджеты
def make_progress_bar(value: int, threshold: int) -> QProgressBar:
    bar = QProgressBar()
    bar.setValue(value)
    bar.setTextVisible(True)
    bar.setFormat(f"{value}%")
    bar.setFixedHeight(20)

    if value >= threshold:
        chunk = "#c0392b"
    elif value >= threshold * 0.75:
        chunk = "#d4a017"
    else:
        chunk = "#27ae60"

    bar.setStyleSheet(f"""
        QProgressBar {{
            border: 1px solid #999;
            border-radius: 4px;
            background-color: #e8e8e8;
            color: #222222;
            text-align: center;
            font-size: 11px;
            font-weight: bold;
        }}
        QProgressBar::chunk {{
            background-color: {chunk};
            border-radius: 3px;
        }}
    """)
    return bar


def make_status_dot(is_alert: bool) -> QLabel:
    dot = QLabel("●")
    dot.setAlignment(Qt.AlignCenter)
    dot.setFont(QFont("Arial", 18))
    if is_alert:
        dot.setStyleSheet("color: #c0392b;")
        dot.setToolTip("Превышен порог нагрузки!")
    else:
        dot.setStyleSheet("color: #27ae60;")
        dot.setToolTip("Нагрузка в норме")
    return dot


# ─────────────────────────────────────────────
# Виджет с графиками одного узла
# ─────────────────────────────────────────────

class NodeChartWidget(QWidget):
    def __init__(self, node_name: str):
        super().__init__()
        self.node_name = node_name
        self.cpu_data  = deque([0] * HISTORY_LEN, maxlen=HISTORY_LEN)
        self.ram_data  = deque([0] * HISTORY_LEN, maxlen=HISTORY_LEN)
        self.disk_data = deque([0] * HISTORY_LEN, maxlen=HISTORY_LEN)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        header = QLabel(f"Узел: {self.node_name}")
        header.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(header)

        pg.setConfigOption('background', '#1e1e2e')
        pg.setConfigOption('foreground', '#cdd6f4')

        self.cpu_plot = pg.PlotWidget(title="CPU (%)")
        self._setup_plot(self.cpu_plot)
        self.cpu_line = self.cpu_plot.plot(list(self.cpu_data),
                                           pen=pg.mkPen(color='#f38ba8', width=2))
        layout.addWidget(self.cpu_plot)

        self.ram_plot = pg.PlotWidget(title="RAM (%)")
        self._setup_plot(self.ram_plot)
        self.ram_line = self.ram_plot.plot(list(self.ram_data),
                                           pen=pg.mkPen(color='#89b4fa', width=2))
        layout.addWidget(self.ram_plot)

        self.disk_plot = pg.PlotWidget(title="Disk (%)")
        self._setup_plot(self.disk_plot)
        self.disk_line = self.disk_plot.plot(list(self.disk_data),
                                             pen=pg.mkPen(color='#a6e3a1', width=2))
        layout.addWidget(self.disk_plot)

    def _setup_plot(self, plot_widget: pg.PlotWidget):
        plot_widget.setYRange(0, 100)
        plot_widget.setXRange(0, HISTORY_LEN)
        plot_widget.setFixedHeight(130)
        plot_widget.showGrid(x=False, y=True)
        plot_widget.setMouseEnabled(x=False, y=False)
        plot_widget.hideButtons()
        threshold_line = pg.InfiniteLine(
            pos=CPU_THRESHOLD, angle=0,
            pen=pg.mkPen(color='#f9e2af', width=1, style=Qt.DashLine)
        )
        plot_widget.addItem(threshold_line)

    def update(self, cpu: float, ram: float, disk: float):
        self.cpu_data.append(cpu)
        self.ram_data.append(ram)
        self.disk_data.append(disk)
        self.cpu_line.setData(list(self.cpu_data))
        self.ram_line.setData(list(self.ram_data))
        self.disk_line.setData(list(self.disk_data))


# ─────────────────────────────────────────────
# Виджет вкладки «Процессы»
# ─────────────────────────────────────────────

class ProcessTab(QWidget):
    """
    Вкладка отображает топ-процессы по CPU и RAM для выбранного узла.
    Сверху — выпадающий список узлов (QComboBox).
    Ниже — две таблицы рядом: топ по CPU и топ по RAM.
    """

    def __init__(self):
        super().__init__()
        # Хранилище: node_name -> {'top_cpu': [...], 'top_ram': [...]}
        self.node_data: dict = {}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── Строка выбора узла ──
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Узел:"))
        self.node_selector = QComboBox()
        self.node_selector.setFixedWidth(260)
        self.node_selector.currentTextChanged.connect(self._refresh_tables)
        top_row.addWidget(self.node_selector)
        top_row.addStretch()

        self.last_update_label = QLabel("Ожидание данных...")
        self.last_update_label.setStyleSheet("color: gray; font-size: 11px;")
        top_row.addWidget(self.last_update_label)
        layout.addLayout(top_row)

        # ── Две таблицы рядом ──
        tables_layout = QHBoxLayout()
        tables_layout.setSpacing(12)

        # Топ по CPU
        cpu_group = QGroupBox("Топ процессов по CPU")
        cpu_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 12px; }")
        cpu_layout = QVBoxLayout(cpu_group)
        self.cpu_table = self._make_proc_table(["PID", "Процесс", "CPU %", "RAM (МБ)"])
        cpu_layout.addWidget(self.cpu_table)
        tables_layout.addWidget(cpu_group)

        # Топ по RAM
        ram_group = QGroupBox("Топ процессов по RAM")
        ram_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 12px; }")
        ram_layout = QVBoxLayout(ram_group)
        self.ram_table = self._make_proc_table(["PID", "Процесс", "CPU %", "RAM (МБ)"])
        ram_layout.addWidget(self.ram_table)
        tables_layout.addWidget(ram_group)

        layout.addLayout(tables_layout)

    def _make_proc_table(self, headers: list) -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setAlternatingRowColors(True)
        t.horizontalHeader().setSectionResizeMode(PCOL_NAME, QHeaderView.Stretch)
        t.horizontalHeader().setSectionResizeMode(PCOL_PID,  QHeaderView.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(PCOL_CPU,  QHeaderView.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(PCOL_RAM,  QHeaderView.ResizeToContents)
        t.verticalHeader().setDefaultSectionSize(26)
        return t

    def update_node(self, node: str, top_cpu: list, top_ram: list, timestamp: str):
        """Вызывается из главного окна при получении новых данных."""
        self.node_data[node] = {
            'top_cpu':   top_cpu,
            'top_ram':   top_ram,
            'timestamp': timestamp,
        }

        # Добавляем узел в выпадающий список, если его там ещё нет
        if self.node_selector.findText(node) == -1:
            self.node_selector.addItem(node)

        # Если сейчас выбран именно этот узел — сразу перерисовываем
        if self.node_selector.currentText() == node:
            self._refresh_tables(node)

    def _refresh_tables(self, node: str):
        if node not in self.node_data:
            return

        entry = self.node_data[node]
        self.last_update_label.setText(f"Обновлено: {entry['timestamp']}")
        self._fill_table(self.cpu_table, entry['top_cpu'])
        self._fill_table(self.ram_table, entry['top_ram'])

    def _fill_table(self, table: QTableWidget, procs: list):
        """Заполняет таблицу списком процессов."""
        table.setRowCount(0)   # очищаем
        for proc in procs:
            row = table.rowCount()
            table.insertRow(row)

            pid_item  = QTableWidgetItem(str(proc.get('pid', '—')))
            name_item = QTableWidgetItem(str(proc.get('name', '—')))
            cpu_item  = QTableWidgetItem(f"{proc.get('cpu', 0):.1f}%")
            ram_item  = QTableWidgetItem(f"{proc.get('ram_mb', 0):.0f}")

            # Выравнивание
            pid_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cpu_item.setTextAlignment(Qt.AlignCenter)
            ram_item.setTextAlignment(Qt.AlignCenter)

            # Подсвечиваем процессы с высокой нагрузкой красным
            cpu_val = proc.get('cpu', 0)
            if cpu_val >= CPU_THRESHOLD:
                for item in (pid_item, name_item, cpu_item, ram_item):
                    item.setForeground(QColor("#c0392b"))

            table.setItem(row, PCOL_PID,  pid_item)
            table.setItem(row, PCOL_NAME, name_item)
            table.setItem(row, PCOL_CPU,  cpu_item)
            table.setItem(row, PCOL_RAM,  ram_item)


# ─────────────────────────────────────────────
# Главное окно
# ─────────────────────────────────────────────

class MonitorApp(QWidget):
    def __init__(self):
        super().__init__()
        self.node_rows   = {}
        self.node_charts = {}

        self.log_file = "metrics_log.csv"
        self._init_csv_log()

        self.initUI()
        self.startServer()

    def _init_csv_log(self):
        file_exists = os.path.isfile(self.log_file)
        with open(self.log_file, mode='a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, delimiter=';')
            if not file_exists:
                writer.writerow(["Время", "Имя узла", "IP адрес",
                                 "CPU (%)", "RAM (%)", "Disk (%)"])

    def initUI(self):
        self.setWindowTitle("Сетевой мониторинг узлов")
        self.resize(1050, 650)

        main_layout = QVBoxLayout()

        title = QLabel("Система мониторинга ресурсов")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 15px; font-weight: bold; padding: 6px;")
        main_layout.addWidget(title)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # ── Вкладка 1: Таблица узлов ──
        table_tab = QWidget()
        table_layout = QVBoxLayout(table_tab)
        self.table = self._build_node_table()
        table_layout.addWidget(self.table)
        self.tabs.addTab(table_tab, "📋  Таблица узлов")

        # ── Вкладка 2: Графики ──
        self.charts_tab = QWidget()
        self.charts_layout = QVBoxLayout(self.charts_tab)
        self.charts_layout.setAlignment(Qt.AlignTop)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.charts_tab)
        self.tabs.addTab(scroll, "📈  Графики")

        # ── Вкладка 3: Процессы (НОВОЕ) ──
        self.process_tab = ProcessTab()
        self.tabs.addTab(self.process_tab, "⚙️  Процессы")

        # ── Строка статуса ──
        self.status_label = QLabel("Ожидание подключения агентов...")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: gray; font-size: 12px; padding: 4px;")
        main_layout.addWidget(self.status_label)

        self.setLayout(main_layout)

    def _build_node_table(self) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels(
            ["●", "Узел", "IP", "CPU", "RAM", "Disk", "Обновлено"]
        )
        header = table.horizontalHeader()
        header.setSectionResizeMode(COL_STATUS, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_NODE,   QHeaderView.Stretch)
        header.setSectionResizeMode(COL_IP,     QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_CPU,    QHeaderView.Stretch)
        header.setSectionResizeMode(COL_RAM,    QHeaderView.Stretch)
        header.setSectionResizeMode(COL_DISK,   QHeaderView.Stretch)
        header.setSectionResizeMode(COL_TIME,   QHeaderView.ResizeToContents)
        table.setColumnWidth(COL_STATUS, 36)
        table.verticalHeader().setDefaultSectionSize(32)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        return table

    def startServer(self):
        self.server_thread = ServerThread()
        self.server_thread.new_client.connect(self.on_new_client)
        self.server_thread.start()

    def on_new_client(self, client_thread):
        client_thread.data_received.connect(self.update_metrics)

    def update_metrics(self, data: dict):
        node = data.get('node', 'Unknown')
        ip   = data.get('ip',   '—')
        cpu  = float(data.get('cpu',  0))
        ram  = float(data.get('ram',  0))
        disk = float(data.get('disk', 0))
        ts   = data.get('time', '—')

        # Данные о процессах (могут отсутствовать у старых агентов)
        top_cpu = data.get('top_cpu', [])
        top_ram = data.get('top_ram', [])

        # ── Лог в CSV ──
        with open(self.log_file, mode='a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow([ts, node, ip, cpu, ram, disk])

        # ── Обновляем таблицу узлов ──
        if node not in self.node_rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.node_rows[node] = row

        row      = self.node_rows[node]
        is_alert = (cpu >= CPU_THRESHOLD or ram >= RAM_THRESHOLD
                    or disk >= DISK_THRESHOLD)

        self.table.setCellWidget(row, COL_STATUS, make_status_dot(is_alert))
        self.table.setItem(row, COL_NODE, QTableWidgetItem(node))
        self.table.setItem(row, COL_IP,   QTableWidgetItem(ip))
        self.table.setCellWidget(row, COL_CPU,  make_progress_bar(int(cpu),  CPU_THRESHOLD))
        self.table.setCellWidget(row, COL_RAM,  make_progress_bar(int(ram),  RAM_THRESHOLD))
        self.table.setCellWidget(row, COL_DISK, make_progress_bar(int(disk), DISK_THRESHOLD))

        time_item = QTableWidgetItem(ts)
        time_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, COL_TIME, time_item)

        # ── Обновляем графики ──
        if node not in self.node_charts:
            chart = NodeChartWidget(node)
            self.node_charts[node] = chart
            self.charts_layout.addWidget(chart)

        self.node_charts[node].update(cpu, ram, disk)

        # ── Обновляем вкладку процессов (НОВОЕ) ──
        if top_cpu or top_ram:
            self.process_tab.update_node(node, top_cpu, top_ram, ts)

        # ── Строка статуса ──
        count = len(self.node_rows)
        self.status_label.setText(
            f"Подключено узлов: {count}  |  Лог: {self.log_file}"
        )


# ─────────────────────────────────────────────
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MonitorApp()
    window.show()
    sys.exit(app.exec_())