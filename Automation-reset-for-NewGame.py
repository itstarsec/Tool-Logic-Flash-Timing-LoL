import requests
import os
import time
from base64 import b64encode
import urllib3
import keyboard
import pyautogui as pag
import threading
import winsound
import sys
import psutil
from pycaw.pycaw import AudioUtilities

# Bỏ qua các cảnh báo không an toàn
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def find_game_directory():
    possible_executables = [
        'LeagueClient.exe',
        'LeagueClientUxRender.exe'
    ]

    for process in psutil.process_iter(['pid', 'name', 'exe']):
        try:
            process_name = process.info['name']
            if process_name in possible_executables:
                exe_path = process.info['exe']
                if exe_path:
                    print(f"Found {process_name} at: {exe_path}")
                    return os.path.dirname(exe_path)
        except (psutil.AccessDenied, psutil.ZombieProcess, psutil.NoSuchProcess) as e:
            print(f"Error accessing process {process.info['name']}: {e}")
            continue
        except Exception as e:
            print(f"Unexpected error: {e}")
            continue

    print("Không thể tìm thấy đường dẫn trò chơi. Vui lòng đảm bảo League of Legends đang chạy.")
    return None

# Tìm đường dẫn trò chơi ngay khi mở tệp
gamedir = find_game_directory()
if gamedir is None:
    print("Không thể tìm thấy đường dẫn trò chơi. Vui lòng kiểm tra xem League of Legends có đang chạy không.")
    sys.exit(1)
else:
    print(f"Đường dẫn trò chơi được tìm thấy: {gamedir}")

# Khởi tạo thời gian game
current_time = 0  # Thời gian hiện tại được khởi tạo
timer_started = False  # Biến cờ để kiểm tra thời gian đã bắt đầu hay chưa
stop_threads = False  # Biến cờ để dừng các luồng

def start_timer():
    global current_time, timer_started
    if not timer_started:  # Kiểm tra nếu chưa bắt đầu
        print(f"Bắt đầu thời gian game: {current_time // 60}:{current_time % 60:02d}")
        timer_started = True  # Đánh dấu thời gian đã bắt đầu
        
        # Bắt đầu tăng thời gian mỗi giây
        threading.Thread(target=update_timer, daemon=True).start()
    else:
        print("Thời gian đã bắt đầu, không thể thiết lập lại.")

def update_timer():
    global current_time, stop_threads
    while not stop_threads:
        time.sleep(1)  # Chờ 1 giây
        current_time += 1  # Tăng thời gian hiện tại lên 1 giây
        minutes = current_time // 60
        seconds = current_time % 60
        print(f"Thời gian hiện tại: {minutes}:{seconds:02d}")

def track_flash_cooldown():
    global current_time, timer_started, stop_threads

    if timer_started:  # Đảm bảo rằng chúng ta chỉ theo dõi khi thời gian đã bắt đầu
        cooldown_time = current_time + 300  # Thêm thời gian tùy chỉnh cho thử nghiệm
        cooldown_minutes = cooldown_time // 60
        cooldown_seconds = cooldown_time % 60
        formatted_time = f"{cooldown_minutes}:{cooldown_seconds:02d}"

        # Gửi tin nhắn vào game
        pag.typewrite(f"{formatted_time} flash")
        pag.press('enter')
        
        # Kiểm tra thời điểm khớp và phát âm thanh bíp
        threading.Thread(target=alarm_check, args=(cooldown_time,), daemon=True).start()

def alarm_check(alarm_time):
    global current_time, stop_threads
    while not stop_threads:
        time.sleep(1)  # Kiểm tra mỗi giây
        if current_time == alarm_time:  # Kiểm tra nếu thời gian hiện tại khớp với thời gian báo
            winsound.Beep(1000, 1000)  # Phát âm thanh bíp (tần số 1000Hz, âm thanh 1 giây)
            # Sau khi phát âm thanh bíp, có thể cần chờ một lúc rồi kiểm tra tiếp
            time.sleep(1)  # Đợi một giây trước khi tiếp tục kiểm tra
            # Cho phép phát lại âm thanh nếu vẫn khớp với thời gian
            while current_time == alarm_time and not stop_threads:
                winsound.Beep(1000, 1000)
                time.sleep(1)  # Đợi 1 giây trước khi kiểm tra lại

def is_sound_playing(process_name):
    """Kiểm tra có âm thanh nào đang phát ra từ process xác định hay không."""
    sessions = AudioUtilities.GetAllSessions()
    for session in sessions:
        if session.Process:
            # Kiểm tra tên process
            if session.Process.name() == process_name:
                # Lấy giá trị âm lượng
                volume = session.SimpleAudioVolume
                if volume and volume.GetMasterVolume() > 0:
                    return True
    return False
    
def monitor_process(process_name):
    """Theo dõi process và kiểm tra âm thanh phát ra."""
    global stop_threads
    print(f"Đang theo dõi: {process_name}")

    while not stop_threads:
        # Kiểm tra xem League of Legends.exe có đang chạy không
        if any(proc.name() == process_name for proc in psutil.process_iter()):
            print(f"{process_name} đang chạy. Kiểm tra âm thanh...")
            if is_sound_playing(process_name):
                print("Âm thanh đang phát. Bắt đầu tính thời gian game...")
                start_timer()  # Bắt đầu tính thời gian game
                keyboard.add_hotkey('F5', track_flash_cooldown)  # Đặt phím tắt F5
                break  # Thoát vòng lặp giám sát để không bắt đầu lại timer
        else:
            print(f"{process_name} không chạy. Đang tiếp tục giám sát...")

        time.sleep(1)  # Kiểm tra mỗi giây

    try:
        # Giữ chương trình chạy liên tục cho đến khi nhận được Ctrl+C
        while not stop_threads:
            time.sleep(1)  # Chạy liên tục lặp lại
    except KeyboardInterrupt:
        print("\nChương trình đã dừng.")
        stop_threads = True  # Đánh dấu để dừng các luồng
        sys.exit(0)  # Thoát chương trình

def read_lockfile(gamedir):
    lockfile = None
    while not lockfile:
        lockpath = os.path.join(gamedir, 'lockfile')

        if os.path.isfile(lockpath):
            print('Found running League of Legends, dir:', gamedir)
            lockfile = open(lockpath, 'r')
        else:
            time.sleep(1)

    # Đọc dữ liệu từ lockfile
    lockdata = lockfile.read()
    lockfile.close()

    # Phân tích dữ liệu lockfile
    lock = lockdata.split(':')
    print('Lockfile data:', lockdata)
    print('Parsed lockfile:', lock)
    return lock  # Trả về danh sách thông tin từ lockfile

def create_headers(password):
    username = 'riot'
    userpass = b64encode(bytes(f'{username}:{password}', 'utf-8')).decode('ascii')
    headers = {'Authorization': f'Basic {userpass}'}
    print('Generated headers:', headers)
    return headers

def request(method, path, headers):
    url = f'https://{host}:{port}{path}'  # Thay đổi để sử dụng HTTPS
    print(f'Making request to URL: {url} with method: {method}')
    response = requests.request(method, url, headers=headers, verify=False)  # verify=False để bỏ qua xác thực SSL
    return response

def check_game_phase(headers):
    """Kiểm tra trạng thái trận đấu mỗi 5 giây."""
    global stop_threads
    while not stop_threads:
        try:
            # Gọi yêu cầu đến API
            r = request('get', '/lol-gameflow/v1/gameflow-phase', headers)

            # Trả về kết quả của yêu cầu
            if r.status_code == 200:
                phase = r.json()
                print(f'Current game phase: {phase}')
                if phase != "InProgress":
                    print("Trận đấu không còn ở trạng thái InProgress. Dừng đếm thời gian và khởi động lại script...")
                    stop_threads = True  # Dừng các luồng
                    return False
            else:
                print(f'Error: {r.status_code}, {r.text}')
        except Exception as e:
            print(f'An error occurred: {str(e)}')
        
        # Đợi 5 giây trước khi gửi yêu cầu tiếp theo
        time.sleep(5)
    return True

def main():
    global stop_threads, host, port
    while True:
        # Đọc dữ liệu từ lockfile
        lock_data = read_lockfile(gamedir)

        # Trích xuất thông tin từ lockfile
        procname = lock_data[0]
        pid = lock_data[1]
        port = lock_data[2]
        password = lock_data[3]
        protocol = lock_data[4]

        host = '127.0.0.1'

        # Tạo tiêu đề xác thực
        headers = create_headers(password)

        # Khởi động luồng kiểm tra trạng thái trận đấu mỗi 5 giây
        phase_check_thread = threading.Thread(target=check_game_phase, args=(headers,), daemon=True)
        phase_check_thread.start()

        # Bắt đầu lắng nghe âm thanh và tính thời gian
        monitor_process("League of Legends.exe")

        # Nếu trận đấu kết thúc, reset các biến và bắt đầu lại
        stop_threads = False
        current_time = 0
        timer_started = False

if __name__ == "__main__":
    main()
