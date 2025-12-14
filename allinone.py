# -*- coding: utf-8 -*-
"""
COMBINED LOL TOOL (ULTRA-LIGHT, NO LCU/PSUTIL, NO CHAMP MAP)

(A) TAB Ping Click Learner (RAM only)
    - Shift+S: Learn ON/OFF (left click to record points)
    - Shift+C: Clear points
    - Tab: Replay ONCE (anti-spam holding key) + initial delay before first click

(B) Coach Flash Tracker (Live Client Data API only)
    - ONLY uses gameData.gameTime (no allPlayers parsing)
    - Alt+F5..F9: log Flash TOP/JG/MID/AD/SP
    - F6: chat summary ONLY:
          "MID 24:39 (-04:17) | JG 25:10 (-04:48)" or "ALL FLASH UP"
    - Ctrl+Q: Exit
"""

import time
import threading

import requests
import keyboard
import winsound
import pyautogui as pag
from pynput import mouse
import urllib3

# LCU optional (nếu bạn muốn auto-reset theo phase)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# =========================
# CONFIG (A) TAB "ping style"
# =========================
INITIAL_DELAY_BEFORE_FIRST_CLICK = 0.50
CLICK_INTERVAL_SEC = 0.08
CLICK_HOLD_SEC = 0.07

# =========================
# CONFIG (B) Flash tracker
# =========================
FLASH_OFFSET_SEC = 300
CHAT_PREFIX = "[Time-Flash]> "
LIVECLIENT_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
POLL_INTERVAL_SEC = 0.50  # poll gameTime nhẹ nhàng

# =========================
# CONFIG: typing F6 (anti duplicate)
# =========================
F6_PRE_TYPE_DELAY_SEC = 0
F6_CHAR_INTERVAL_SEC = 0
F6_DEBOUNCE_SEC = 0.45

# =========================
# PyAutoGUI safety
# =========================
pag.FAILSAFE = True
pag.PAUSE = 0

stop_all = threading.Event()

# ======================================================
# (A) TAB Click Learner (RAM-only)
# ======================================================
tab_lock = threading.Lock()
tab_learning = False
tab_points = []
tab_down = False
tab_replay_running = False

def tab_toggle_learning():
    global tab_learning, tab_points
    with tab_lock:
        tab_learning = not tab_learning
        if tab_learning:
            tab_points = []
            print("\n[LEARN] ON  -> Click chuột trái để ghi điểm. Bấm Shift+S lần nữa để kết thúc học.\n")
        else:
            print(f"\n[LEARN] OFF -> Đã lưu {len(tab_points)} điểm trong RAM.\n")

def tab_clear_points():
    global tab_points
    with tab_lock:
        tab_points.clear()
    print("\n[CLEAR] Đã xoá toàn bộ điểm trong RAM.\n")

def _tab_safe_click(x: int, y: int):
    pag.moveTo(x, y, duration=0)
    pag.mouseDown(button="left")
    time.sleep(CLICK_HOLD_SEC)
    pag.mouseUp(button="left")

def tab_replay_points_once():
    global tab_replay_running
    with tab_lock:
        if tab_replay_running:
            return
        if tab_learning:
            print("[REPLAY] Đang learn mode -> hãy tắt learn (Shift+S) trước.")
            return
        if not tab_points:
            print("[REPLAY] Chưa có điểm. Bấm Shift+S để học.")
            return
        points = list(tab_points)
        tab_replay_running = True

    try:
        time.sleep(INITIAL_DELAY_BEFORE_FIRST_CLICK)
        for (x, y) in points:
            _tab_safe_click(int(x), int(y))
            if CLICK_INTERVAL_SEC > 0:
                time.sleep(CLICK_INTERVAL_SEC)
    except pag.FailSafeException:
        print("[REPLAY] FAILSAFE triggered (chuột góc trên-trái). Dừng ngay.")
    finally:
        with tab_lock:
            tab_replay_running = False

def on_mouse_click_record(x, y, button, pressed):
    if button != mouse.Button.left or pressed:
        return
    with tab_lock:
        if not tab_learning:
            return
        tab_points.append((int(x), int(y)))
        total = len(tab_points)
    print(f"[LEARN] + ({int(x)},{int(y)}) total={total}")

def tab_on_press(_e):
    global tab_down
    with tab_lock:
        if tab_down:
            return
        tab_down = True
    threading.Thread(target=tab_replay_points_once, daemon=True).start()

def tab_on_release(_e):
    global tab_down
    with tab_lock:
        tab_down = False

# ======================================================
# (B) Flash Tracker (Live Client API: gameTime only)
# ======================================================
game_lock = threading.Lock()
game_started = False
last_game_time = 0.0
last_game_perf = 0.0
prev_gt = 0.0

flash_lock = threading.Lock()
flash_by_lane = {}  # lane -> {"used": float, "ready": float}

LANE_LABEL = {
    "TOP": "TOP",
    "JG":  "JG",
    "MID": "MID",
    "ADC": "AD",
    "SUP": "SP",
}

def fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s//60:02d}:{s%60:02d}"

def liveclient_get_allgamedata():
    r = requests.get(LIVECLIENT_URL, verify=False, timeout=0.6)
    r.raise_for_status()
    return r.json()

def game_clock_now() -> float:
    with game_lock:
        if not game_started:
            return 0.0
        return last_game_time + (time.perf_counter() - last_game_perf)

def reset_game_state(reason: str):
    global game_started, last_game_time, last_game_perf, prev_gt
    print(f"\n[{reason}] Reset game state + flash list\n")

    with game_lock:
        game_started = False
        last_game_time = 0.0
        last_game_perf = 0.0
        prev_gt = 0.0

    with flash_lock:
        flash_by_lane.clear()

def liveclient_poller():
    global game_started, last_game_time, last_game_perf, prev_gt

    print("[LiveClient] Polling gameTime 127.0.0.1:2999 ...")
    while not stop_all.is_set():
        try:
            data = liveclient_get_allgamedata()
            gt = float((data.get("gameData", {}) or {}).get("gameTime", 0.0))
            now = time.perf_counter()

            with game_lock:
                # New game detect: đang game dài mà gt tụt về thấp
                if game_started and prev_gt > 300 and gt < 60:
                    reset_game_state("Auto-NewGame(gt reset)")

                last_game_time = gt
                last_game_perf = now
                prev_gt = gt

                if gt > 0 and not game_started:
                    game_started = True
                    print(f"[LiveClient] Game started (gameTime={gt:.1f}s)")

        except Exception:
            pass

        time.sleep(POLL_INTERVAL_SEC)

def schedule_flash_event(lane: str):
    if not game_started:
        print("[Flash] Game chưa start.")
        return

    used = game_clock_now()
    ready = used + FLASH_OFFSET_SEC

    with flash_lock:
        # nhấn lại lane sẽ ghi đè (không nối dài)
        flash_by_lane[lane] = {"used": used, "ready": ready}

    label = LANE_LABEL.get(lane, lane)
    print(f"{CHAT_PREFIX}{label} dùng ~{fmt_time(used)} → HỒI ~{fmt_time(ready)}")

    try:
        pag.typewrite(f"{CHAT_PREFIX}{label} {fmt_time(ready)} ")
    except Exception as e:
        print(f"[Flash] Không gõ chat được: {e}")

    tgt = time.perf_counter() + FLASH_OFFSET_SEC

    def _alarm():
        while not stop_all.is_set():
            rem = tgt - time.perf_counter()
            if rem <= 0.03:
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
    if not game_started:
        print("[Info] Game chưa start.")
        return

    cur_gt = game_clock_now()
    with flash_lock:
        if not flash_by_lane:
            print("[Info] Chưa có Flash nào.")
            return
        snap = dict(flash_by_lane)

    pieces = []
    for lane, ev in snap.items():
        remaining = ev["ready"] - cur_gt
        if remaining <= 0:
            continue
        label = LANE_LABEL.get(lane, lane)
        pieces.append(f"{label} {fmt_time(ev['ready'])} (-{fmt_time(remaining)})")

    msg = " | ".join(pieces) if pieces else "ALL FLASH UP"
    print(f"[InfoChat] {msg}")

    try:
        if F6_PRE_TYPE_DELAY_SEC > 0:
            time.sleep(F6_PRE_TYPE_DELAY_SEC)
        pag.write(msg + " ", interval=F6_CHAR_INTERVAL_SEC)
    except Exception as e:
        print(f"[Info] Không gõ chat được: {e}")

# ======================================================
# HOTKEYS
# ======================================================
def register_hotkeys():
    # TAB module
    keyboard.add_hotkey('shift+s', tab_toggle_learning)
    keyboard.add_hotkey('shift+c', tab_clear_points)
    keyboard.on_press_key('tab', tab_on_press, suppress=False)
    keyboard.on_release_key('tab', tab_on_release, suppress=False)

    # Flash module
    def mk_lane(lane: str, min_gap: float = 0.10):
        def _inner():
            now = time.perf_counter()
            if not hasattr(_inner, "_last"):
                _inner._last = 0.0
            if now - _inner._last < min_gap:
                return
            _inner._last = now
            schedule_flash_event(lane)
        return _inner

    keyboard.add_hotkey('alt+f5', mk_lane("TOP"))
    keyboard.add_hotkey('alt+f6', mk_lane("JG"))
    keyboard.add_hotkey('alt+f7', mk_lane("MID"))
    keyboard.add_hotkey('alt+f8', mk_lane("ADC"))
    keyboard.add_hotkey('alt+f9', mk_lane("SUP"))

    # F6 summary (debounce mạnh)
    def f6_handler():
        now = time.perf_counter()
        if not hasattr(f6_handler, "_last"):
            f6_handler._last = 0.0
        if now - f6_handler._last < F6_DEBOUNCE_SEC:
            return
        f6_handler._last = now
        send_status_to_chat()

    keyboard.add_hotkey('f6', f6_handler)
    keyboard.add_hotkey('ctrl+q', lambda: (stop_all.set(), print("Thoát...")))

    print("\n=== HOTKEYS ===")
    print("[TAB PING]")
    print("  Shift+S  -> Learn ON/OFF (click trái để ghi điểm)")
    print("  Shift+C  -> Clear điểm")
    print("  Tab      -> Replay 1 lượt (giữ Tab KHÔNG spam) + delay trước click đầu")
    print("")
    print("[FLASH TRACKER]")
    print("  Alt+F5..F9 -> TOP/JG/MID/AD/SP (KHÔNG map tướng)")
    print("  F6         -> chat summary (gõ theo interval để tránh lặp ký tự)")
    print("")
    print("  Ctrl+Q     -> Thoát")
    print("==============\n")

# ======================================================
# MAIN
# ======================================================
def main():
    print("[*] Combined LOL Tool (NO CHAMP MAP) running...")
    print("NOTE: PyAutoGUI FAILSAFE: kéo chuột lên góc trên-trái để dừng click ngay.\n")

    m_listener = mouse.Listener(on_click=on_mouse_click_record)
    m_listener.daemon = True
    m_listener.start()

    register_hotkeys()
    threading.Thread(target=liveclient_poller, daemon=True).start()

    while not stop_all.is_set():
        time.sleep(0.25)

    try:
        keyboard.unhook_all()
    except Exception:
        pass
    print("Đã dừng.")

if __name__ == "__main__":
    main()

