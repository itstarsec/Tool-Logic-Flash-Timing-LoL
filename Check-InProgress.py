# -*- coding: utf-8 -*-
"""
Check-InProgress.py
- F5: đặt Flash +300s
- F6: reset clock + huỷ mọi beep + lắng nghe lại audio
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
            play = is_sound_playing(proc_name, thresh=AUDIO_PEAK_THRESHOLD)
            if play and not last:
                edge = time.perf_counter()
                clock.start(init_game_time, bias_sec=AUDIO_EDGE_BIAS_SEC)
                print(f"[Audio] Edge detected at {edge:.3f}")
                return
            last = play
            time.sleep(AUDIO_POLL_INTERVAL)
    finally:
        try: pythoncom.CoUninitialize()
        except Exception: pass

# ---------- alarm (bị hủy khi reset) ----------
def schedule_flash_alarm(offset=FLASH_OFFSET_SEC):
    if not clock.started():
        print("[Flash] Đồng hồ chưa chạy")
        return

    now_game = clock.now()
    tgt_game = now_game + offset
    m, s = divmod(int(tgt_game), 60)
    try:
        pag.typewrite(f"{CHAT_PREFIX}{m}:{s:02d} ")
        pag.press('enter')
    except Exception as e:
        print(f"[Flash] Không gửi chat: {e}")

    with session_id_lock:
        my_session = session_id

    tgt_perf = time.perf_counter() + offset

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
    print(f"[Flash] +{offset}s → {m}:{s:02d} (session {my_session})")

# ---------- reset ----------
def reset_and_restart_monitor(reason="Manual"):
    """Xoá tracking, huỷ beep, khởi động lại lắng nghe audio."""
    print(f"\n[{reason}] Reset clock, cancel alarms, restart monitor...")
    clock.reset()
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
    last_present = False
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

# ---------- hotkeys ----------
def register_hotkeys_once():
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
    keyboard.add_hotkey('ctrl+q', lambda: (stop_all.set(), print("Thoát...")))

    hotkey_registered = True
    print("Hotkeys: F5 (Flash+300s), F6 (Reset), Ctrl+Q (Thoát)")

# ---------- main ----------
def main():
    try: pag.FAILSAFE = False
    except Exception: pass

    register_hotkeys_once()

    # LCU (tuỳ chọn)
    lcu_info = read_lockfile(find_game_directory()) if LCU_CHECK else None

    # Bắt đầu lắng nghe để xác định "đầu trận"
    threading.Thread(target=monitor_process_audio_edge,
                     args=(AUDIO_PROCESS_NAME, 0.0), daemon=True).start()
    # Watcher tự động phát hiện hết trận
    threading.Thread(target=auto_end_watcher, args=(lcu_info,), daemon=True).start()

    while not stop_all.is_set():
        time.sleep(0.5)
    print("Đã dừng.")

if __name__ == "__main__":
    main()
