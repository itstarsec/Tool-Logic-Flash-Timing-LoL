# -*- coding: utf-8 -*-
"""
Check-InProgress.py (Spectator / Coaching + lane hotkeys Alt+F5..F9)

- Lấy gameTime trực tiếp từ Live Client Data API (spectator/in-game).
- Alt+F5: log Flash của TOP
- Alt+F6: log Flash của JG
- Alt+F7: log Flash của MID
- Alt+F8: log Flash của ADC
- Alt+F9: log Flash của SUP
- F5: log Flash không gắn lane (UNKNOWN) nếu muốn dùng nhanh.

Mỗi lần log Flash:
    - Lưu lane + thời điểm dùng + thời điểm hồi (gameTime + FLASH_OFFSET_SEC).
    - Beep khi đến thời điểm hồi.
    - Gõ vào chat thời gian hồi (MM:SS).

F6:
    - Gõ vào chat summary dạng:
      [Time-Flash]> GT 20:22 | MIDF 24:39 (-04:17), JGF 25:10 (-04:48)
      (chỉ hiển thị những lane còn đang cooldown).

Tự động reset:
    - Khi LCU phase != "InProgress" (ví dụ EndOfGame, Lobby...).
    - Khi gameTime từ Live Client API bị reset xuống thấp sau một game dài (Auto-NewGame).

Ctrl+Q:
    - Thoát script.

"""

import os
import time
import threading
import requests
import urllib3
import psutil
import keyboard
import winsound
from base64 import b64encode
import pyautogui as pag

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==================== cấu hình ====================
FLASH_OFFSET_SEC = 300          # +300s = 5 phút
CHAT_PREFIX = "[Time-Flash]> "  # prefix khi fill vào chat

LIVECLIENT_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"

SPECTATOR_DELAY = 180.0
USE_SPECTATOR_DELAY = False     # True: hiển thị thêm thời gian "thực" (trừ 180s)

LCU_CHECK = True
LCU_POLL_INTERVAL = 1.0

stop_all = threading.Event()
hotkey_registered = False

# Thông tin gameTime đồng bộ từ Live Client API
game_clock_lock = threading.Lock()
game_started = threading.Event()
last_game_time = 0.0           # giây
last_game_time_perf = 0.0      # perf_counter lúc nhận gameTime
last_api_ok = 0.0
_prev_gt_for_newgame = 0.0

# Danh sách Flash đã log
# mỗi event: {"id", "lane", "used_game_time", "ready_game_time", "created_perf"}
flash_events = []
flash_events_lock = threading.Lock()
flash_event_id_seq = 0

# session_id dùng để huỷ beep khi reset
session_id_lock = threading.Lock()
session_id = 0

LANE_CODES = {
    "TOP": "TOP",
    "JG":  "JG",
    "MID": "MID",
    "ADC": "ADC",
    "SUP": "SUP",
    "UNKNOWN": ""
}

# ---------- helpers định dạng thời gian ----------
def fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    m = s // 60
    s = s % 60
    return f"{m:02d}:{s:02d}"

# ---------- LCU helpers ----------
def find_game_directory():
    for p in psutil.process_iter(['name', 'exe']):
        try:
            if p.info['name'] in ('LeagueClient.exe', 'LeagueClientUxRender.exe') and p.info['exe']:
                return os.path.dirname(p.info['exe'])
        except Exception:
            pass
    return None

def read_lockfile(gamedir):
    if not gamedir:
        return None
    lockpath = os.path.join(gamedir, 'lockfile')
    if not os.path.isfile(lockpath):
        return None
    try:
        with open(lockpath, 'r') as f:
            data = f.read().strip().split(':')
        if len(data) >= 5:
            return {'host': '127.0.0.1', 'port': data[2], 'password': data[3]}
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

# ---------- Live Client / Spectator: gameTime ----------
def liveclient_get_allgamedata():
    r = requests.get(LIVECLIENT_URL, verify=False, timeout=0.5)
    r.raise_for_status()
    return r.json()

def game_clock_now():
    """
    Thời gian trong game (theo spectator) tính tại thời điểm hiện tại.
    Dùng last_game_time + (perf_counter - last_game_time_perf).
    """
    with game_clock_lock:
        if not game_started.is_set():
            return 0.0
        dt = time.perf_counter() - last_game_time_perf
        return last_game_time + dt

def game_clock_reset():
    global _prev_gt_for_newgame
    with game_clock_lock:
        global last_game_time, last_game_time_perf
        last_game_time = 0.0
        last_game_time_perf = 0.0
        _prev_gt_for_newgame = 0.0
    game_started.clear()

def liveclient_game_time_poller():
    """
    Thread định kỳ gọi Live Client Data API để:
    - Cập nhật gameTime (spectator)
    - Đánh dấu game_started khi gameTime > 0
    - Auto reset khi gameTime giảm mạnh (game mới)
    """
    global last_game_time, last_game_time_perf, last_api_ok, _prev_gt_for_newgame
    print("[LiveClient] Bắt đầu poll gameTime từ 127.0.0.1:2999 ...")
    while not stop_all.is_set():
        try:
            data = liveclient_get_allgamedata()
            gt = float(data.get("gameData", {}).get("gameTime", 0.0))
            now = time.perf_counter()
            with game_clock_lock:
                # phát hiện game mới: gt nhỏ hơn nhiều so với gt trước
                if game_started.is_set() and _prev_gt_for_newgame > 300 and gt < 60:
                    # gameTime reset về thấp sau một game dài
                    # → coi là game mới
                    reset_all("Auto-NewGame(gt reset)")
                last_game_time = gt
                last_game_time_perf = now
                _prev_gt_for_newgame = gt

            last_api_ok = now
            if gt > 0.0 and not game_started.is_set():
                game_started.set()
                print(f"[LiveClient] Game started (gameTime={gt:.1f}s)")
        except Exception:
            pass
        time.sleep(0.5)

# ---------- quản lý Flash events ----------
def schedule_flash_event(lane="UNKNOWN"):
    """
    Log 1 lần Flash cho lane:
    - used_game_time = game_clock_now()
    - ready_game_time = used_game_time + FLASH_OFFSET_SEC
    - Lưu vào flash_events
    - Lên lịch beep khi tới thời gian đó (wall-clock)
    - Đồng thời fill thời gian hồi vào khung chat hiện tại
    """
    if not game_started.is_set():
        print("[Flash] Game chưa start (chưa có gameTime từ spectator).")
        return

    used = game_clock_now()
    ready = used + FLASH_OFFSET_SEC

    global flash_event_id_seq
    with flash_events_lock:
        flash_event_id_seq += 1
        eid = flash_event_id_seq
        flash_events.append({
            "id": eid,
            "lane": lane,
            "used_game_time": used,
            "ready_game_time": ready,
            "created_perf": time.perf_counter(),
        })

    used_str = fmt_time(used)
    ready_str = fmt_time(ready)
    lane_code = LANE_CODES.get(lane, "F")

    if USE_SPECTATOR_DELAY:
        real_used = max(0.0, used - SPECTATOR_DELAY)
        real_ready = max(0.0, ready - SPECTATOR_DELAY)
        print(
            f"{CHAT_PREFIX}{lane_code} dùng ~{used_str} (real~{fmt_time(real_used)}) "
            f"→ HỒI ~{ready_str} (real~{fmt_time(real_ready)})"
        )
    else:
        print(f"{CHAT_PREFIX}{lane_code} dùng ~{used_str} → HỒI ~{ready_str}")

    # === FILL CHAT: thời gian HỒI đúng theo spectator gameTime ===
    try:
        pag.typewrite(f"{CHAT_PREFIX}{lane_code} {ready_str} ")
        # pag.press('enter')  # nếu muốn auto gửi luôn
    except Exception as e:
        print(f"[Flash] Không gửi chat được: {e}")

    # Lên lịch beep theo wall-clock
    offset = FLASH_OFFSET_SEC
    with session_id_lock:
        my_session = session_id
    tgt_wall = time.perf_counter() + offset

    def _alarm():
        while not stop_all.is_set():
            with session_id_lock:
                if my_session != session_id:
                    return  # bị reset
            rem = tgt_wall - time.perf_counter()
            if rem <= 0.03:
                with session_id_lock:
                    if my_session != session_id:
                        return
                try:
                    winsound.Beep(600, 800)
                    time.sleep(0.2)
                    winsound.Beep(800, 300)
                except Exception:
                    pass
                break
            time.sleep(0.2 if rem > 1 else max(0.03, rem - 0.01))

    threading.Thread(target=_alarm, daemon=True).start()

def send_status_to_chat():
    """
    F6: Gửi summary ngắn vào chat (lane-based):
    - GameTime hiện tại
    - Các lane có Flash đang cooldown: TOPF / JGF / MIDF / ADCF / SUPF
      dạng: GT 20:22 | MIDF 24:39 (-04:17), JGF 25:10 (-04:48)
    """
    if not game_started.is_set():
        print("[Info] Game chưa start, không có gì để gửi.")
        return

    cur_gt = game_clock_now()
    with flash_events_lock:
        if not flash_events:
            print("[Info] Chưa có Flash nào được log (Alt+F5..F9 / F5).")
            return
        events_copy = list(flash_events)

    # Lấy event cuối cùng mỗi lane (theo used_game_time) và còn cooldown
    latest_per_lane = {}
    for ev in events_copy:
        lane = ev["lane"]
        if lane not in latest_per_lane:
            latest_per_lane[lane] = ev
        else:
            if ev["used_game_time"] > latest_per_lane[lane]["used_game_time"]:
                latest_per_lane[lane] = ev

    pieces = []
    for lane, ev in latest_per_lane.items():
        remaining = ev["ready_game_time"] - cur_gt
        if remaining <= 0:
            continue  # Flash lane này đã hồi, không cần báo
        lane_code = LANE_CODES.get(lane, "F")
        pieces.append(f"{lane_code} {fmt_time(ev['ready_game_time'])} (-{fmt_time(remaining)})")

    msg = ""
    if pieces:
        msg = f" | ".join(pieces)
    else:
        msg = f"ALL FLASH UP"

    print(f"[InfoChat] {msg}")
    try:
        pag.typewrite(msg + " ")
        # pag.press('enter')  # nếu muốn gửi luôn
    except Exception as e:
        print(f"[Info] Không gửi chat được: {e}")

def reset_all(reason="Manual"):
    """
    reset toàn bộ:
    - reset game_clock (trạng thái "chưa start", poller vẫn chạy)
    - huỷ toàn bộ beep (tăng session_id)
    - xoá danh sách flash_events
    """
    print(f"\n[{reason}] Reset clock & flash events...")
    game_clock_reset()
    with session_id_lock:
        global session_id
        session_id += 1
        cur = session_id
    print(f"[{reason}] All pending alarms cancelled (new session {cur}).")

    with flash_events_lock:
        flash_events.clear()
    print(f"[{reason}] Flash events cleared.\n")

# ---------- watcher: LCU auto reset khi phase != InProgress ----------
def auto_end_watcher_lcu(lcu_info):
    if not (LCU_CHECK and lcu_info):
        return

    headers = lcu_headers(lcu_info['password'])
    host = lcu_info["host"]
    port = lcu_info["port"]

    print("[LCU] Auto-end watcher bật (theo gameflow-phase).")
    while not stop_all.is_set():
        if game_started.is_set():
            r = lcu_get(host, port, "/lol-gameflow/v1/gameflow-phase", headers, timeout=1.0)
            if r is not None and r.status_code == 200:
                try:
                    phase = r.json()
                    if phase != "InProgress":
                        reset_all(reason=f"Auto-LCU({phase})")
                except Exception:
                    pass
        time.sleep(LCU_POLL_INTERVAL)

# ---------- in bảng tổng hợp Flash ra console ----------
def flash_summary_printer():
    last_print = 0.0
    INTERVAL = 10.0
    while not stop_all.is_set():
        now = time.time()
        if now - last_print >= INTERVAL:
            last_print = now
            if not game_started.is_set():
                time.sleep(0.5)
                continue

            cur_gt = game_clock_now()
            with flash_events_lock:
                events_copy = list(flash_events)

            if not events_copy:
                time.sleep(0.5)
                continue

            print("\n--- TỔNG HỢP FLASH ---")
            print(f" GameTime (spectator): {fmt_time(cur_gt)}")
            if USE_SPECTATOR_DELAY:
                print(f" Ước tính thời gian thực: {fmt_time(max(0.0, cur_gt - SPECTATOR_DELAY))}")

            for ev in events_copy:
                used = ev["used_game_time"]
                ready = ev["ready_game_time"]
                remaining = ready - cur_gt
                lane_code = LANE_CODES.get(ev["lane"], "F")
                status = "ĐÃ HỒI" if remaining <= 0 else f"Còn ~{fmt_time(remaining)}"
                print(
                    f"  - Flash #{ev['id']} [{lane_code}]: dùng ~{fmt_time(used)}, "
                    f"hồi ~{fmt_time(ready)} -> {status}"
                )
            print("----------------------\n")
        time.sleep(0.5)

# ---------- hotkeys ----------
def register_hotkeys_once():
    global hotkey_registered
    if hotkey_registered:
        return

    def generic_handler(lane):
        def _inner():
            now = time.perf_counter()
            if not hasattr(_inner, "_last"):
                _inner._last = 0.0
            if now - _inner._last >= 0.15:
                _inner._last = now
                schedule_flash_event(lane=lane)
        return _inner

    def f6_handler():
        now = time.perf_counter()
        if not hasattr(f6_handler, "_last"):
            f6_handler._last = 0.0
        if now - f6_handler._last >= 0.15:
            f6_handler._last = now
            send_status_to_chat()

    # F5: generic (UNKNOWN lane)
    keyboard.add_hotkey('F5', generic_handler("UNKNOWN"))
    # Alt+F5..F9: TOP/JG/MID/ADC/SUP
    keyboard.add_hotkey('alt+F5', generic_handler("TOP"))
    keyboard.add_hotkey('alt+F6', generic_handler("JG"))
    keyboard.add_hotkey('alt+F7', generic_handler("MID"))
    keyboard.add_hotkey('alt+F8', generic_handler("ADC"))
    keyboard.add_hotkey('alt+F9', generic_handler("SUP"))

    keyboard.add_hotkey('F6', f6_handler)                     # chat summary lane-based
    keyboard.add_hotkey('ctrl+q', lambda: (stop_all.set(), print("Thoát...")))

    hotkey_registered = True
    print("Hotkeys:")
    print("  F5         -> log Flash (lane UNKNOWN) + chat thời gian hồi")
    print("  Alt+F5..F9 -> log Flash cho TOP/JG/MID/ADC/SUP + chat thời gian hồi")
    print("  F6         -> chat summary: GT + lane Flash còn cooldown")
    print("  Ctrl+Q     -> Thoát")

# ---------- main ----------
def main():
    try:
        pag.FAILSAFE = False
    except Exception:
        pass

    register_hotkeys_once()

    lcu_info = read_lockfile(find_game_directory()) if LCU_CHECK else None

    threading.Thread(target=liveclient_game_time_poller, daemon=True).start()
    threading.Thread(target=auto_end_watcher_lcu, args=(lcu_info,), daemon=True).start()
    threading.Thread(target=flash_summary_printer, daemon=True).start()

    print("\n[*] Script COACH / SPECTATOR:")
    print(" - Alt+F5..F9: Khi THẤY lane tương ứng Flash → mở chat trong game → bấm Alt+F?.")
    print("   Lane map: Alt+F5=TOP, Alt+F6=JG, Alt+F7=MID, Alt+F8=ADC, Alt+F9=SUP.")
    print(" - F5: log Flash generic (UNKNOWN) nếu bạn không cần lane.")
    print(" - F6: Gõ vào chat summary: gameTime hiện tại + các lane còn cooldown Flash.")
    print(" - Hết trận / vào game mới: script tự reset (LCU phase & gameTime reset).")
    print(" - Ctrl+Q: Thoát.\n")

    while not stop_all.is_set():
        time.sleep(0.5)
    print("Đã dừng.")

if __name__ == "__main__":
    main()
