# -*- coding: utf-8 -*-
"""
flash_timer.py - phiên bản tối giản + mini-map style

F5      = tạo buffer +300s
F6      = undo lần gán gần nhất
F7      = ẩn/hiện overlay (dùng keyboard hook, thường ăn được trong game hơn)
Ctrl+Q  = thoát

Không còn detect sound / LCU → tránh lag.
"""

import sys
import time
import keyboard  # cần: pip install keyboard

from PyQt5.QtCore import Qt, QTimer, QPoint, QCoreApplication
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QComboBox, QGridLayout
)
from PyQt5.QtGui import QFont

# ==========================================================
#  FLASH LOGIC (simple & stable)
# ==========================================================
FLASH_OFFSET = 300
LANES = ["TOP", "JGL", "MID", "ADC", "SUP"]

lane_target = {lane: None for lane in LANES}
buffer_target = None
last_assigned_lane = None


def _now():
    return time.time()


def set_buffer():
    """F5: tạo buffer mới +300s (ghi đè buffer cũ)."""
    global buffer_target
    buffer_target = _now() + FLASH_OFFSET
    print("[F5] Buffer created +300s")


def undo_assign():
    """F6: undo lane vừa gán gần nhất → trả timer về buffer."""
    global buffer_target, last_assigned_lane
    if last_assigned_lane is None:
        print("[F6] Nothing to undo")
        return

    lane = last_assigned_lane
    t = lane_target.get(lane)
    if t:
        buffer_target = t
    lane_target[lane] = None
    last_assigned_lane = None

    print(f"[F6] Undo lane {lane}")


def assign_lane(lane: str):
    """Gán buffer hiện tại vào lane (nếu có buffer)."""
    global buffer_target, last_assigned_lane
    if buffer_target is None:
        return
    lane_target[lane] = buffer_target
    last_assigned_lane = lane
    buffer_target = None
    print(f"[Assign] {lane} updated")


def fmt_timer(ts):
    if not ts:
        return "Ready"
    rem = int(ts - _now())
    if rem <= 0:
        return "Ready"
    return f"{rem // 60:02d}:{rem % 60:02d}"


# ==========================================================
#  OVERLAY UI – MINI-MAP STYLE (BUF cùng hàng với TOP)
# ==========================================================
class Overlay(QWidget):
    def __init__(self):
        super().__init__()

        # Cửa sổ trong suốt, luôn on top, có thể kéo
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.Tool |
            Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        root = QVBoxLayout()
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Mini-map grid: BUF cùng hàng với TOP
        # Layout:
        #   row0: [BUF] [TOP]
        #   row1: [JGL] [MID]
        #   row2: [ADC] [SUP]
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)

        self.tile_labels = {}  # key -> QLabel

        def make_tile(key: str, title: str) -> QLabel:
            tile = QLabel()
            tile.setAlignment(Qt.AlignCenter)
            tile.setFont(QFont("Consolas", 10, QFont.Bold))
            tile.setStyleSheet("""
                QLabel {
                    color: #888;
                    background-color: rgba(8,8,8,190);
                    border: 1px solid #444;
                    border-radius: 6px;
                    padding: 2px;
                }
            """)
            tile.setFixedSize(80, 40)
            tile.setText(f"{title}\nReady")
            self.tile_labels[key] = tile
            return tile

        # Hàng 0: BUF - TOP
        grid.addWidget(make_tile("BUF", "BUF"), 0, 0)
        grid.addWidget(make_tile("TOP", "TOP"), 0, 1)

        # Hàng 1: JGL - MID
        grid.addWidget(make_tile("JGL", "JGL"), 1, 0)
        grid.addWidget(make_tile("MID", "MID"), 1, 1)

        # Hàng 2: ADC - SUP
        grid.addWidget(make_tile("ADC", "ADC"), 2, 0)
        grid.addWidget(make_tile("SUP", "SUP"), 2, 1)

        root.addLayout(grid)

        # Combobox lane (chọn lane để gán buffer)
        cb_row = QHBoxLayout()
        lbl_lane = QLabel("Lane:")
        lbl_lane.setFont(QFont("Segoe UI", 8))
        lbl_lane.setStyleSheet("color:#cccccc;")
        lbl_lane.setFixedWidth(30)

        self.combo = QComboBox()
        self.combo.setFont(QFont("Segoe UI", 8))
        self.combo.setStyleSheet("""
            QComboBox {
                background-color: rgba(20,20,20,220);
                color: white;
                border-radius: 4px;
                padding: 1px 4px;
                border: 1px solid #555;
            }
            QComboBox QAbstractItemView {
                background-color: rgba(20,20,20,240);
                color: white;
                selection-background-color: #444;
                selection-color: white;
            }
        """)

        # mapping combobox -> lane key
        self.combo_map = {
            "Top": "TOP",
            "Jungle": "JGL",
            "Mid": "MID",
            "ADC": "ADC",
            "Support": "SUP",
        }
        for text in self.combo_map.keys():
            self.combo.addItem(text)

        # ❗ FIX: dùng cả currentIndexChanged và activated
        self.combo.currentIndexChanged.connect(self.on_lane_selected)
        self.combo.activated.connect(self.on_lane_activated)

        cb_row.addWidget(lbl_lane)
        cb_row.addWidget(self.combo, 1)
        root.addLayout(cb_row)

        self.setLayout(root)
        self.resize(200, 220)
        self.move_to_right()

        # drag support
        self._dragging = False
        self._drag_pos = QPoint()

        # timer update
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(300)

    def move_to_right(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 20, screen.top() + 20)

    def refresh(self):
        # BUF tile
        buf_lbl = self.tile_labels.get("BUF")
        if buf_lbl:
            if buffer_target:
                rem = int(buffer_target - _now())
                if rem > 0:
                    buf_lbl.setText(f"BUF\n{rem//60:02d}:{rem%60:02d}")
                    buf_lbl.setStyleSheet("""
                        QLabel {
                            color: white;
                            background-color: rgba(12,12,12,220);
                            border: 1px solid #888;
                            border-radius: 6px;
                            padding: 2px;
                        }
                    """)
                else:
                    # buffer hết hạn
                    buf_lbl.setText("BUF\nReady")
                    buf_lbl.setStyleSheet("""
                        QLabel {
                            color: #888;
                            background-color: rgba(8,8,8,190);
                            border: 1px solid #444;
                            border-radius: 6px;
                            padding: 2px;
                        }
                    """)
            else:
                buf_lbl.setText("BUF\n(empty)")
                buf_lbl.setStyleSheet("""
                    QLabel {
                        color: #888;
                        background-color: rgba(8,8,8,190);
                        border: 1px solid #444;
                        border-radius: 6px;
                        padding: 2px;
                    }
                """)

        # lanes
        for lane in LANES:
            t = lane_target[lane]
            text = fmt_timer(t)
            lbl = self.tile_labels.get(lane)
            if not lbl:
                continue

            if text == "Ready":
                lbl.setText(f"{lane}\nReady")
                lbl.setStyleSheet("""
                    QLabel {
                        color: #888;
                        background-color: rgba(8,8,8,190);
                        border: 1px solid #444;
                        border-radius: 6px;
                        padding: 2px;
                    }
                """)
                # auto clear hết hạn
                lane_target[lane] = None
            else:
                lbl.setText(f"{lane}\n{text}")
                lbl.setStyleSheet("""
                    QLabel {
                        color: white;
                        background-color: rgba(10,10,10,220);
                        border: 1px solid #888;
                        border-radius: 6px;
                        padding: 2px;
                    }
                """)

    def _current_lane_key(self) -> str:
        text = self.combo.currentText()
        return self.combo_map.get(text, "TOP")

    def on_lane_selected(self, idx: int):
        """Gọi khi đổi index (chuyển lane)."""
        lane_key = self._current_lane_key()
        assign_lane(lane_key)

    def on_lane_activated(self, idx: int):
        """
        ❗ FIX thứ 2:
        Activated sẽ fire cả khi chọn lại đúng lane hiện tại,
        nên chọn lại Top vẫn gán buffer được.
        """
        lane_key = self._current_lane_key()
        assign_lane(lane_key)

    # drag window
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_pos = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._dragging and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPos() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = False
            e.accept()


# ==========================================================
#  HOTKEYS BẰNG THƯ VIỆN keyboard
# ==========================================================
def register_hotkeys(overlay: Overlay):
    # chống spam
    last = {"f5": 0.0, "f6": 0.0, "f7": 0.0}

    def debounced(name, func, delay=0.15):
        def wrapper():
            now = time.time()
            if now - last[name] < delay:
                return
            last[name] = now
            func()
        return wrapper

    keyboard.add_hotkey("f5", debounced("f5", set_buffer))
    keyboard.add_hotkey("f6", debounced("f6", undo_assign))

    def toggle_overlay():
        overlay.setVisible(not overlay.isVisible())

    keyboard.add_hotkey("f7", debounced("f7", toggle_overlay))

    def quit_app():
        print("[Ctrl+Q] Quit requested")
        QCoreApplication.quit()

    keyboard.add_hotkey("ctrl+q", quit_app)

    print("Hotkeys: F5 (buffer), F6 (undo), F7 (toggle overlay), Ctrl+Q (quit)")


# ==========================================================
#  MAIN
# ==========================================================
def main():
    app = QApplication(sys.argv)

    ov = Overlay()
    ov.show()

    register_hotkeys(ov)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
