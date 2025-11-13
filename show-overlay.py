# -*- coding: utf-8 -*-
"""
Check-InProgress.py
- F5: đặt Flash +300s
- F6: reset clock + huỷ mọi beep + lắng nghe lại audio
- Alt+Shift+X: bật/tắt overlay countdown
- Có thể kéo thả overlay đến bất kỳ vị trí nào, giữ nguyên giữa các lần update
- Tự động reset khi hết trận (process biến mất / im lặng dài / LCU phase!=InProgress)
"""

import os, time, threading, requests, urllib3, psutil, keyboard, pyautogui as pag, winsound, pythoncom
from base64 import b64encode
from pycaw.pycaw import AudioUtilities
try:
    from pycaw.pycaw import IAudioMeterInformation
    HAVE_METER = True
except Exception:
    HAVE_METER = False

# -------- PyQt5 cho overlay --------
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtWidgets import QApplication, QLabel, QWidget
from PyQt5.QtGui import QFont

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== cấu hình ====================
AUDIO_PROCESS_NAME = "League of Legends.exe"
AUDIO_POLL_INTERVAL = 0.10             # 100ms
AUDIO_PEAK_THRESHOLD = 0.01            # ngưỡng coi là "đang phát" khi detect start
THRESH_LOW = 0.003                     # ngưỡng "im lặng" cho watcher
AUDIO_EDGE_BIAS_SEC = 0.0

FLASH_OFFSET_SEC = 300
CHAT_PREFIX = "[Time-Flash]> "

# Auto-reset tiêu chí
AUTO_SILENCE_END_SEC = 12.0            # im lặng liên tục (s) sau khi vào trận -> coi là hết trận
PROCESS_GONE_GRACE_SEC = 3.0           # process biến mất liên tục (s) -> coi là hết trận
LCU_CHECK = True                       # nếu True, thử dùng LCU phase (nếu lockfile tồn tại)
LCU_POLL_INTERVAL = 0.5

stop_all = threading.Event()
hotkey_registered = False
restart_event = threading.Event()      # dừng tracker cũ khi reset
match_active = threading.Event()       # đã start đồng hồ
session_id_lock = threading.Lock()
session_id = 0                         # tăng mỗi lần reset để huỷ beep cũ

# ---------- COM helper ----------
_COM_TL = threading.local()
def ensure_com_initialized():
    if getattr(_COM_TL, "com_inited", False):
        return
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
    except pythoncom.com_error:
        pass
    _COM_TL.com_inited = True

# ---------- đồng hồ ----------
class GameClock:
    def __init__(self):
        self._lock = threading.Lock()
        self._started = False
        self._t0_perf = 0.0
        self._t0_game = 0.0
    def start(self, initial_game_time_sec=0.0, bias_sec=0.0):
        with self._lock:
            if not self._started:
                self._t0_perf = time.perf_counter()
                self._t0_game = float(initial_game_time_sec) + float(bias_sec)
                self._started = True
                print(f"[Clock] Started at {self._t0_game:.2f}s (bias {bias_sec:+.2f})")
                match_active.set()
    def reset(self):
        with self._lock:
            self._started = False
        match_active.clear()
    def started(self):
        with self._lock:
            return self._started
    def now(self):
        with self._lock:
            if not self._started:
                return 0.0
            return self._t0_game + (time.perf_counter() - self._t0_perf)

clock = GameClock()

# ---------- Countdown Manager cho overlay ----------
class CountdownManager:
    """
    Lưu các countdown tương ứng với từng lần F5.
    Key: label (mm:ss game-time)
    Value: target_perf_time
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._timers = {}  # label -> tgt_perf

    def add_timer(self, label, tgt_perf):
        """
        Nếu label đã tồn tại thì overwrite để đảm bảo lần gần nhất thắng (deduplicate).
        """
        with self._lock:
            self._timers[label] = tgt_perf

    def clear_all(self):
        with self._lock:
            self._timers.clear()

    def get_active(self):
        """
        Trả về list (label, remaining_sec > 0), sort theo remaining tăng dần.
        Những countdown <= 0 sẽ tự xoá.
        """
        now = time.perf_counter()
        res = []
        with self._lock:
            to_delete = []
            for label, tgt in self._timers.items():
                rem = tgt - now
                if rem <= 0:
                    to_delete.append(label)
                else:
                    res.append((label, rem))
            for label in to_delete:
                del self._timers[label]
        res.sort(key=lambda x: x[1])
        return res

countdown_manager = CountdownManager()

# ---------- LCU helpers (tùy chọn) ----------
def find_game_directory():
    for p in psutil.process_iter(['name','exe']):
        try:
            if p.info['name'] in ('LeagueClient.exe','LeagueClientUxRender.exe') and p.info['exe']:
                return os.path.dirname(p.info['exe'])
        except Exception:
            pass
    return None

def read_lockfile(gamedir):
    if not gamedir: return None
    lockpath = os.path.join(gamedir, 'lockfile')
    if not os.path.isfile(lockpath): return None
    try:
        with open(lockpath, 'r') as f:
            data = f.read().strip().split(':')
        if len(data) >= 5:
            return {'host':'127.0.0.1','port':data[2],'password':data[3]}
    except Exception:
        return None
    return None

def lcu_headers(password):
    userpass = b64encode(f"riot:{password}".encode()).decode()
    return {'Authorization': f'Basic {userpass}'}

def lcu_get(host, port, path, headers, timeout=2.0):
    try:
        return requests.get(f'https://{host}:{port}{path}', headers=headers, verify=False, timeout=timeout)
    except Exception:
        return None

# ---------- PyCAW ----------
def session_is_playing(session, thresh=AUDIO_PEAK_THRESHOLD):
    ensure_com_initialized()
    try:
        if HAVE_METER:
            meter = session._ctl.QueryInterface(IAudioMeterInformation)
            return meter.GetPeakValue() > thresh
        # fallback (ít tin cậy): chỉ dựa vào volume>0
        vol = session.SimpleAudioVolume
        return bool(vol and vol.GetMasterVolume() > 0.0)
    except Exception:
        return False

def get_peak_exact(proc_name):
    """Max peak của đúng process; -1 nếu không thấy session / lỗi."""
    ensure_com_initialized()
    try:
        peak = -1.0
        for s in AudioUtilities.GetAllSessions():
            if s.Process and s.Process.name() == proc_name:
                if HAVE_METER:
                    m = s._ctl.QueryInterface(IAudioMeterInformation)
                    pv = float(m.GetPeakValue() or 0.0)
                else:
                    # fallback: coi như 0 nếu không có meter
                    pv = 0.0
                if pv > peak: peak = pv
        return peak
    except Exception:
        return -1.0

def is_sound_playing(proc_name, thresh=AUDIO_PEAK_THRESHOLD):
    ensure_com_initialized()
    for _ in range(2):
        try:
            for s in AudioUtilities.GetAllSessions():
                if s.Process and s.Process.name() == proc_name:
                    if session_is_playing(s, thresh=thresh):
                        return True
            return False
        except Exception:
            time.sleep(0.05)
    return False

# ---------- theo dõi edge âm thanh ----------
def monitor_process_audio_edge(proc_name, init_game_time=0.0):
    ensure_com_initialized()
    try:
        print(f"[Audio] Monitor {proc_name} ...")
        # chờ process xuất hiện
        while not stop_all.is_set() and not restart_event.is_set():
            if any(p.info['name'] == proc_name for p in psutil.process_iter(['name'])):
                break
            time.sleep(0.2)

        last = False
        while not stop_all.is_set() and not restart_event.is_set():
            # dùng đúng ngưỡng peak
            play = is_sound_playing(proc_name, thresh=AUDIO_PEAK_THRESHOLD)
            if play and not last:
                edge = time.perf_counter()
                clock.start(init_game_time, bias_sec=AUDIO_EDGE_BIAS_SEC)
                print(f"[Audio] Edge detected at {edge:.3f}")
                return
            last = play
            time.sleep(AUDIO_POLL_INTERVAL)
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


# ---------- alarm (bị hủy khi reset) ----------
def schedule_flash_alarm(offset=FLASH_OFFSET_SEC):
    if not clock.started():
        print("[Flash] Đồng hồ chưa chạy")
        return

    now_game = clock.now()
    tgt_game = now_game + offset
    m, s = divmod(int(tgt_game), 60)
    label = f"{m}:{s:02d}"  # dùng label game-time để deduplicate

    try:
        pag.typewrite(f"{CHAT_PREFIX}{label} ")
        pag.press('enter')
    except Exception as e:
        print(f"[Flash] Không gửi chat: {e}")

    with session_id_lock:
        my_session = session_id

    tgt_perf = time.perf_counter() + offset

    # --> thêm countdown vào overlay
    countdown_manager.add_timer(label, tgt_perf)

    def _alarm():
        while not stop_all.is_set():
            with session_id_lock:
                if my_session != session_id:
                    return  # bị reset/hủy
            rem = tgt_perf - time.perf_counter()
            if rem <= 0.03:
                with session_id_lock:
                    if my_session != session_id:
                        return
                try:
                    winsound.Beep(500, 1000); time.sleep(0.2); winsound.Beep(500, 300)
                except Exception:
                    pass
                break
            time.sleep(0.2 if rem > 1 else max(0.03, rem - 0.01))

    threading.Thread(target=_alarm, daemon=True).start()
    print(f"[Flash] +{offset}s → {label} (session {my_session})")

# ---------- reset ----------
def reset_and_restart_monitor(reason="Manual"):
    """Xoá tracking, huỷ beep, khởi động lại lắng nghe audio."""
    print(f"\n[{reason}] Reset clock, cancel alarms, restart monitor...")
    clock.reset()
    countdown_manager.clear_all()  # xoá hết countdown trên overlay
    with session_id_lock:
        globals()['session_id'] += 1
        cur = session_id
    print(f"[{reason}] All pending alarms cancelled (new session {cur}).")
    restart_event.set()
    time.sleep(0.3)
    restart_event.clear()
    threading.Thread(target=monitor_process_audio_edge,
                     args=(AUDIO_PROCESS_NAME, 0.0), daemon=True).start()
    print("[Audio] Listening restarted.")

# ---------- watcher: tự nhận biết hết trận ----------
def auto_end_watcher(lcu_info):
    """Khi đồng hồ đã start, theo dõi: im lặng dài / process biến mất / LCU phase != InProgress -> auto reset."""
    ensure_com_initialized()
    gone_acc = 0.0
    silence_acc = 0.0
    last_t = time.perf_counter()
    headers = lcu_headers(lcu_info['password']) if lcu_info else None

    while not stop_all.is_set():
        now = time.perf_counter()
        dt = now - last_t
        last_t = now

        if match_active.is_set():
            # 1) process còn chạy?
            present = any(p.info['name'] == AUDIO_PROCESS_NAME for p in psutil.process_iter(['name']))
            if not present:
                gone_acc += dt
                if gone_acc >= PROCESS_GONE_GRACE_SEC:
                    reset_and_restart_monitor(reason="Auto-ProcessGone")
                    gone_acc = 0.0
                    silence_acc = 0.0
                    continue
            else:
                gone_acc = 0.0

            # 2) im lặng dài?
            peak = get_peak_exact(AUDIO_PROCESS_NAME)  # -1 nếu không thấy session
            if peak >= 0.0:
                if peak <= THRESH_LOW:
                    silence_acc += dt
                    if silence_acc >= AUTO_SILENCE_END_SEC:
                        reset_and_restart_monitor(reason="Auto-Silence")
                        silence_acc = 0.0
                        gone_acc = 0.0
                        continue
                else:
                    silence_acc = 0.0
            else:
                # không thấy session -> đối xử như im lặng
                silence_acc += dt
                if silence_acc >= AUTO_SILENCE_END_SEC:
                    reset_and_restart_monitor(reason="Auto-NoSession")
                    silence_acc = 0.0
                    gone_acc = 0.0
                    continue

            # 3) LCU phase?
            if LCU_CHECK and lcu_info and headers:
                r = lcu_get(lcu_info['host'], lcu_info['port'], "/lol-gameflow/v1/gameflow-phase", headers, timeout=1.0)
                if r is not None and r.status_code == 200:
                    try:
                        phase = r.json()
                        if phase != "InProgress":
                            reset_and_restart_monitor(reason=f"Auto-LCU({phase})")
                            silence_acc = 0.0
                            gone_acc = 0.0
                            continue
                    except Exception:
                        pass

        time.sleep(min(AUDIO_POLL_INTERVAL, LCU_POLL_INTERVAL if lcu_info else AUDIO_POLL_INTERVAL))

    try: pythoncom.CoUninitialize()
    except Exception: pass

# ---------- Overlay bằng PyQt ----------
class OverlayWindow(QWidget):
    def __init__(self):
        super().__init__()

        # Cửa sổ không viền, luôn on top, nền trong suốt
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.label = QLabel(self)
        self.label.setFont(QFont("Consolas", 14))
        self.label.setStyleSheet(
            """
            QLabel {
                color: white;
                background-color: rgba(0, 0, 0, 180);
                padding: 8px 14px;
                border-radius: 8px;
            }
            """
        )
        self.label.setText("No active countdown")
        self.label.adjustSize()

        self.resize(self.label.size())

        # cờ đánh dấu user đã kéo chưa
        self._user_moved = False
        self._drag_pos = None

        # khởi tạo ở góc phải trên, lệch xuống 30px
        self.move_initial()

        # Timer update UI
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_content)
        self.timer.start(200)  # 5 lần/giây cho mượt

    def move_initial(self, margin=30):
        """Đặt vị trí mặc định (chỉ dùng lúc khởi tạo hoặc khi chưa từng kéo)."""
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = geo.right() - self.width() - margin
        y = geo.top() + margin
        self.move(x, y)

    def update_content(self):
        timers = countdown_manager.get_active()
        if not timers:
            text = "No active countdown"
        else:
            lines = []
            for label, rem in timers:
                # rem = giây còn lại tính từ lúc bấm F5
                mm, ss = divmod(int(rem + 0.999), 60)
                lines.append(f"{label} → {mm:02d}:{ss:02d}")
            text = "\n".join(lines)
        self.label.setText(text)
        self.label.adjustSize()
        self.resize(self.label.size())
        # KHÔNG tự move lại vị trí nữa để không phá kéo tay

    # Cho phép kéo overlay bằng chuột
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self._drag_pos is not None:
            self._user_moved = True
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

class OverlayController(QObject):
    toggle_signal = pyqtSignal()

    def __init__(self, window: OverlayWindow):
        super().__init__()
        self.window = window
        self.toggle_signal.connect(self.toggle_overlay)

    def toggle_overlay(self):
        if self.window.isVisible():
            self.window.hide()
        else:
            # nếu user chưa từng kéo thì lần đầu hiện lại sẽ đưa về vị trí mặc định
            if not self.window._user_moved:
                self.window.move_initial()
            self.window.show()

# ---------- hotkeys ----------
def register_hotkeys_once(overlay_controller=None):
    global hotkey_registered
    if hotkey_registered: return

    def f5_handler():
        now = time.perf_counter()
        if not hasattr(f5_handler, "_last"): f5_handler._last = 0.0
        if now - f5_handler._last >= 0.15:
            f5_handler._last = now
            schedule_flash_alarm()

    keyboard.add_hotkey('F5', f5_handler)
    keyboard.add_hotkey('F6', lambda: reset_and_restart_monitor(reason="Manual"))

    # ctrl+q: thoát toàn bộ tool
    def quit_all():
        print("Thoát...")
        stop_all.set()
    keyboard.add_hotkey('ctrl+q', quit_all)

    # Alt+Shift+X: bật/tắt overlay
    if overlay_controller is not None:
        keyboard.add_hotkey('alt+shift+x', overlay_controller.toggle_signal.emit)

    hotkey_registered = True
    print("Hotkeys: F5 (Flash+300s), F6 (Reset), Alt+Shift+X (Toggle overlay), Ctrl+Q (Thoát)")

# ---------- backend threads ----------
def start_backend_threads(lcu_info):
    # Bắt đầu lắng nghe để xác định "đầu trận"
    threading.Thread(target=monitor_process_audio_edge,
                     args=(AUDIO_PROCESS_NAME, 0.0), daemon=True).start()
    # Watcher tự động phát hiện hết trận
    threading.Thread(target=auto_end_watcher, args=(lcu_info,), daemon=True).start()

# ---------- main ----------
def main():
    try: pag.FAILSAFE = False
    except Exception: pass

    # LCU (tuỳ chọn)
    lcu_info = read_lockfile(find_game_directory()) if LCU_CHECK else None

    # Khởi tạo Qt Application trong main thread
    app = QApplication([])

    overlay_window = OverlayWindow()
    overlay_window.show()  # hiện luôn từ đầu để dễ kéo

    overlay_controller = OverlayController(overlay_window)

    # Đăng ký hotkey (F5/F6/Ctrl+Q + Alt+Shift+X)
    register_hotkeys_once(overlay_controller)

    # Chạy backend LoL tracking
    start_backend_threads(lcu_info)

    # Timer trong Qt để check stop_all rồi thoát app
    def check_stop():
        if stop_all.is_set():
            app.quit()

    stop_timer = QTimer()
    stop_timer.timeout.connect(check_stop)
    stop_timer.start(500)

    app.exec_()
    print("Đã dừng.")

if __name__ == "__main__":
    main()
