# League of Legends Timer Tool

## Mục đích
Công cụ này được thiết kế để hỗ trợ người chơi **League of Legends** trong việc theo dõi thời gian hồi chiêu của các kỹ năng, đặc biệt là Flash. Công cụ sẽ tự động bắt đầu tính thời gian khi phát hiện âm thanh từ game và cho phép người dùng theo dõi thời gian hồi chiêu bằng cách nhấn phím `F5`.

---

## 1. Thư viện sử dụng
Các thư viện được sử dụng trong code bao gồm:
- **`keyboard`**: Để lắng nghe sự kiện bàn phím (ví dụ: nhấn phím `F5`).
- **`pyautogui`**: Để mô phỏng các thao tác nhập liệu và nhấn phím trong game.
- **`time`**: Để tạo các khoảng thời gian chờ (delay).
- **`threading`**: Để chạy các tác vụ song song (ví dụ: cập nhật thời gian và kiểm tra âm thanh).
- **`winsound`**: Để phát âm thanh báo hiệu khi thời gian hồi chiêu kết thúc.
- **`sys`**: Để thoát chương trình khi nhận được tín hiệu `Ctrl+C`.
- **`psutil`**: Để kiểm tra xem một process có đang chạy hay không.
- **`pycaw`**: Để kiểm tra âm lượng của một process (xác định xem có âm thanh đang phát ra không).

---

## 2. Biến toàn cục
Các biến toàn cục được sử dụng để quản lý trạng thái của chương trình:
- **`current_time`**: Lưu trữ thời gian hiện tại của game (tính bằng giây).
- **`timer_started`**: Biến cờ để kiểm tra xem thời gian game đã bắt đầu hay chưa.
- **`stop_threads`**: Biến cờ để dừng tất cả các luồng khi chương trình kết thúc.

---

## 3. Hàm `start_timer`
- **Mục đích**: Bắt đầu tính thời gian game.
- **Chi tiết**:
  - Kiểm tra xem thời gian game đã bắt đầu chưa thông qua biến `timer_started`.
  - Nếu chưa bắt đầu, in ra thời gian ban đầu và khởi động một luồng mới để cập nhật thời gian mỗi giây (`update_timer`).
  - Đánh dấu `timer_started = True` để ngăn việc bắt đầu lại thời gian.

---

## 4. Hàm `update_timer`
- **Mục đích**: Cập nhật thời gian game mỗi giây.
- **Chi tiết**:
  - Sử dụng vòng lặp `while not stop_threads` để liên tục tăng `current_time` lên 1 giây.
  - In ra thời gian hiện tại dưới định dạng `phút:giây`.
  - Sử dụng `time.sleep(1)` để tạo độ trễ 1 giây giữa các lần cập nhật.

---

## 5. Hàm `track_flash_cooldown`
- **Mục đích**: Theo dõi thời gian hồi chiêu của Flash.
- **Chi tiết**:
  - Kiểm tra xem thời gian game đã bắt đầu chưa (`timer_started`).
  - Tính toán thời gian hồi chiêu (`cooldown_time`) bằng cách cộng thêm 300 giây vào `current_time`.
  - Gửi tin nhắn vào game với thời gian hồi chiêu (ví dụ: `5:00 flash`).
  - Khởi động một luồng mới để kiểm tra khi nào thời gian hồi chiêu kết thúc (`alarm_check`).

---

## 6. Hàm `alarm_check`
- **Mục đích**: Phát âm thanh báo hiệu khi thời gian hồi chiêu kết thúc.
- **Chi tiết**:
  - Sử dụng vòng lặp `while not stop_threads` để kiểm tra xem `current_time` có bằng `alarm_time` không.
  - Nếu khớp, phát âm thanh bíp bằng `winsound.Beep(1000, 1000)`.
  - Sử dụng `time.sleep(1)` để đảm bảo âm thanh không bị lặp lại quá nhanh.

---

## 7. Hàm `is_sound_playing`
- **Mục đích**: Kiểm tra xem có âm thanh đang phát ra từ một process hay không.
- **Chi tiết**:
  - Sử dụng `pycaw` để lấy danh sách các session âm thanh đang hoạt động.
  - Kiểm tra xem process có tên trùng với `process_name` không.
  - Kiểm tra âm lượng của process (`volume.GetMasterVolume() > 0`).
  - Trả về `True` nếu có âm thanh đang phát, ngược lại trả về `False`.

---

## 8. Hàm `monitor_process`
- **Mục đích**: Theo dõi process và kiểm tra âm thanh phát ra.
- **Chi tiết**:
  - Sử dụng vòng lặp `while not stop_threads` để liên tục kiểm tra xem process có đang chạy không.
  - Nếu process đang chạy, kiểm tra xem có âm thanh đang phát không bằng `is_sound_playing`.
  - Nếu có âm thanh, bắt đầu tính thời gian game bằng `start_timer` và đặt phím tắt `F5` để theo dõi thời gian hồi chiêu.
  - Thoát khỏi vòng lặp giám sát sau khi bắt đầu tính thời gian.
  - Sử dụng `try-except` để bắt tín hiệu `Ctrl+C` và dừng chương trình.

---

## 9. Hàm `main`
- **Mục đích**: Khởi động chương trình.
- **Chi tiết**:
  - Gọi hàm `monitor_process` với `process_name` là `"League of Legends.exe"`.

---

## 10. Luồng thực thi
1. Chương trình bắt đầu bằng cách gọi `monitor_process`.
2. Nếu phát hiện process `League of Legends.exe` đang chạy và có âm thanh, bắt đầu tính thời gian game.
3. Người dùng có thể nhấn `F5` để theo dõi thời gian hồi chiêu của Flash.
4. Khi thời gian hồi chiêu kết thúc, phát âm thanh báo hiệu.
5. Nhấn `Ctrl+C` để dừng chương trình.

---

## 11. Kết luận
- **Ưu điểm**:
  - Tự động bắt đầu tính thời gian khi phát hiện âm thanh từ game.
  - Hỗ trợ theo dõi thời gian hồi chiêu với phím tắt `F5`.
  - Phát âm thanh báo hiệu khi thời gian hồi chiêu kết thúc.
  - Dễ dàng tích hợp với các game khác bằng cách thay đổi `process_name`.
- **Hạn chế**:
  - Phụ thuộc vào thư viện `pycaw` để kiểm tra âm thanh, chỉ hoạt động trên Windows.
  - Cần cài đặt nhiều thư viện bên ngoài.

---

## 12. Hướng phát triển
- Thêm tính năng tùy chỉnh thời gian hồi chiêu cho các kỹ năng khác.
- Hỗ trợ đa nền tảng (Linux, macOS).
- Cải thiện hiệu suất bằng cách tối ưu hóa các vòng lặp và luồng.

---

Nếu bạn có bất kỳ câu hỏi hoặc góp ý nào, vui lòng liên hệ hoặc tạo issue trên repository của tôi. Chúc bạn chơi game vui vẻ và hiệu quả!
