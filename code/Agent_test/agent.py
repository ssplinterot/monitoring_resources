import socket
import json
import time
import psutil
import platform
import configparser
import os

# ─────────────────────────────────────────────
# Чтение конфига
# ─────────────────────────────────────────────
# Файл config.ini должен лежать рядом с agent.py
# Если файла нет — создаём его с настройками по умолчанию

CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.ini')

def load_config():
    config = configparser.ConfigParser()

    if not os.path.isfile(CONFIG_FILE):
        # Создаём config.ini автоматически при первом запуске
        config['server'] = {
            'ip':   '127.0.0.1',
            'port': '9090',
        }
        config['agent'] = {
            'name':          platform.node(),
            'send_interval': '2',
            'top_n':         '5',
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)
        print(f"[*] Создан файл конфигурации: {CONFIG_FILE}")
        print(f"[*] Отредактируй его при необходимости и перезапусти агент.")
    else:
        config.read(CONFIG_FILE, encoding='utf-8')

    return config

config = load_config()

SERVER_IP     = config.get('server', 'ip',              fallback='127.0.0.1')
SERVER_PORT   = config.getint('server', 'port',         fallback=9090)
NODE_NAME     = config.get('agent',  'name',            fallback=platform.node())
SEND_INTERVAL = config.getint('agent', 'send_interval', fallback=2)
TOP_N         = config.getint('agent', 'top_n',         fallback=5)

DISK_PATH = '/' if platform.system() != 'Windows' else 'C:\\'


# ─────────────────────────────────────────────
# Сбор метрик процессов
# ─────────────────────────────────────────────

def get_top_processes(n=TOP_N):
    """
    Возвращает два списка:
      top_cpu — топ-N процессов по загрузке CPU
      top_ram — топ-N процессов по использованию RAM (в МБ)

    Почему try/except внутри цикла:
      - процесс может завершиться прямо во время итерации -> NoSuchProcess
      - системные процессы не дают читать свои данные   -> AccessDenied
      - memory_info может вернуть None на macOS          -> проверяем явно
    """
    procs = []

    for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
        try:
            mem_info = p.info['memory_info']

            # Явная проверка на None — на macOS некоторые системные
            # процессы возвращают memory_info=None вместо объекта
            if mem_info is None:
                continue

            mem_mb = round(mem_info.rss / 1024 / 1024, 1)

            procs.append({
                'pid':    p.info['pid'],
                'name':   p.info['name'] or 'unknown',
                'cpu':    round(p.info['cpu_percent'] or 0.0, 1),
                'ram_mb': mem_mb,
            })

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    top_cpu = sorted(procs, key=lambda x: x['cpu'],    reverse=True)[:n]
    top_ram = sorted(procs, key=lambda x: x['ram_mb'], reverse=True)[:n]

    return top_cpu, top_ram


# ─────────────────────────────────────────────
# Сбор системных метрик
# ─────────────────────────────────────────────

def collect_metrics():
    top_cpu, top_ram = get_top_processes()

    data = {
        'node': NODE_NAME,
        'cpu':  psutil.cpu_percent(interval=1),
        'ram':  psutil.virtual_memory().percent,
        'disk': psutil.disk_usage(DISK_PATH).percent,
        'top_cpu': top_cpu,
        'top_ram': top_ram,
    }
    return data


# ─────────────────────────────────────────────
# Основной цикл с автопереподключением
# ─────────────────────────────────────────────

def start_agent():
    print(f"[*] Агент запущен.")
    print(f"[*] Узел:   {NODE_NAME}")
    print(f"[*] Сервер: {SERVER_IP}:{SERVER_PORT}")
    print(f"[*] Интервал отправки: {SEND_INTERVAL} сек.")
    print(f"[*] Конфиг: {CONFIG_FILE}")

    while True:
        # ── Фаза 1: подключение (повторяем до успеха) ──
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connected = False

        while not connected:
            try:
                s.connect((SERVER_IP, SERVER_PORT))
                print(f"[+] Подключение установлено к {SERVER_IP}:{SERVER_PORT}")
                connected = True
            except ConnectionRefusedError:
                print(f"[!] Сервер {SERVER_IP}:{SERVER_PORT} недоступен. "
                      f"Повтор через 3 сек...")
                time.sleep(3)
            except OSError:
                s.close()
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                time.sleep(3)

        # ── Фаза 2: цикл отправки метрик ──
        try:
            while True:
                data      = collect_metrics()
                json_data = json.dumps(data, ensure_ascii=False) + '\n'

                s.sendall(json_data.encode('utf-8', errors='replace'))
                top_name_raw = data['top_cpu'][0]['name'] if data['top_cpu'] else 'n/a'
                top_name = top_name_raw.encode('utf-8', errors='replace').decode('utf-8')
                
                print(f"[>] CPU={data['cpu']}%  RAM={data['ram']}%  "
                      f"Disk={data['disk']}%  | топ CPU: {top_name}")

                time.sleep(SEND_INTERVAL)

        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            print(f"[-] Связь потеряна: {e}. Переподключение...")
        finally:
            s.close()

        time.sleep(2)


if __name__ == '__main__':
    start_agent()