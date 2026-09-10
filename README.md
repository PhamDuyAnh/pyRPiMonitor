# pyRPiMonitor 0.1.1

`py` là viết tắt của Python. pyRPiMonitor thu thập trạng thái Raspberry Pi,
OpenWebRX và lỗi journal, rồi publish một JSON qua MQTT mỗi 60 giây.

Repository: https://github.com/PhamDuyAnh/pyRPiMonitor

Bản 0.1.0 đã được người dùng xác nhận nhận JSON bằng MQTT Explorer trên Pi.
Bản 0.1.1 đổi tên và bổ sung cài đặt chạy không cần đăng nhập; đã kiểm tra local,
chưa xác minh boot/logout thực tế trên Pi.

## Chạy khi không có người đăng nhập

pyRPiMonitor dùng **systemd user service của pi và lingering**. Lingering cho phép
user manager khởi động khi boot và tồn tại sau logout, theo
[tài liệu loginctl của systemd](https://github.com/systemd/systemd/blob/v252/man/loginctl.xml).
Không cần giữ SSH hoặc VS Code mở. Service kế thừa user/group của pi, không đặt
`User=`/`Group=` để tránh lỗi `216/GROUP` của unit cũ.

Bộ cài kiểm tra `Linger=yes`; nếu chưa có, thử `loginctl --no-ask-password enable-linger pi`.
Nếu policy không cho phép, bộ cài dừng với hướng dẫn, không tự dùng sudo hoặc xin
mật khẩu. Khi đó cần quản trị viên cho phép/bật lingering cho pi một lần rồi chạy
lại bộ cài. Không coi việc enable unit đơn thuần là đủ để chạy sau logout.

## Tên và đường dẫn

| Thành phần | Giá trị |
| --- | --- |
| Dự án | pyRPiMonitor |
| Workspace Windows | `D:\pyRPiMonitor` |
| Checkout trên Pi | `/home/pi/pyRPiMonitor` |
| Chương trình | `py_rpi_monitor.py` |
| User service | `pyrpimonitor.service` |
| Private environment file | `/home/pi/.config/pyRPiMonitor/pyRPiMonitor.env` |
| MQTT topic | `RPiMonitor/status` (giữ tương thích bên nhận) |
| Environment prefix | `RPIMONITOR_` (giữ tương thích cấu hình cũ) |

Tên `rpi-monitor` chỉ còn dùng trong hướng dẫn và mã migration từ 0.1.0.
File khảo sát riêng và Python cache giữ local, không đưa lên GitHub.

## Cập nhật từ bản 0.1.0 đã cài

Chạy trong terminal Pi dưới user `pi`. Các lệnh này clone vào thư mục mới để giữ
checkout cũ làm bản dự phòng. Nếu `/home/pi/pyRPiMonitor` đã tồn tại, không clone
đè; kiểm tra đó là checkout đúng repository và dùng quy trình cập nhật ở mục sau.

```sh
git clone https://github.com/PhamDuyAnh/pyRPiMonitor.git /home/pi/pyRPiMonitor
cd /home/pi/pyRPiMonitor
python3 -B -m unittest discover -s tests -v
python3 -B py_rpi_monitor.py --once --stdout
sh install.sh --migrate
```

Chỉ tiếp tục nếu install thành công. `--migrate`:

- Kiểm tra Python, paho, user manager và bật/xác minh lingering.
- Sao lưu unit đích nếu có, và unit `rpi-monitor.service` cũ bằng tên duy nhất.
- Copy credential cũ từ `/home/pi/.config/rpi-monitor/rpi-monitor.env` tới đường
  dẫn mới với quyền 600 **chỉ khi file mới chưa tồn tại**; giữ file cũ, không in nội dung.
- Cài/enable unit mới; stop/disable và gỡ đúng unit cũ để tránh hai publisher.
- Không start unit mới hoặc sửa nội dung credential đã có. Không restart OpenWebRX,
  Mosquitto hoặc Raspberry Pi. Giữ checkout cũ, dữ liệu và backup.

Sau khi install thành công:

```sh
systemctl --user reset-failed pyrpimonitor.service
systemctl --user start pyrpimonitor.service
loginctl show-user pi --property=Linger
systemctl --user is-enabled pyrpimonitor.service
systemctl --user status pyrpimonitor.service
```

Kỳ vọng `Linger=yes`, unit `enabled` và `active (running)`. Nếu credential cũ không
có ở vị trí mặc định, tạo file mới theo mục cài mới trước khi start.

Đăng xuất toàn bộ phiên SSH và theo dõi MQTT Explorer ít nhất 2–3 chu kỳ:
`timestamp` phải tiếp tục thay đổi. Bản tin retain cũ vẫn hiển thị khi publisher
đã dừng, nên chỉ nhìn thấy JSON chưa đủ để xác nhận tiến trình còn hoạt động.
Ở lần reboot do người vận hành chủ động thực hiện, kiểm tra MQTT **trước khi SSH
login lại** để xác nhận khởi động không phụ thuộc phiên đăng nhập. Bộ cài không reboot.

## Cài mới

Môi trường mục tiêu: Raspbian 12 Bookworm, kernel aarch64/user-space armhf,
Python 3.11.2, OpenWebRX 1.2.113, Mosquitto 2.0.11. Cần Git và paho-mqtt 1.6.1
có sẵn cho `/usr/bin/python3`. Không dùng psutil, không tự cài package, không root.
User pi cần quyền đọc journal và chạy vcgencmd như máy đã khảo sát.

```sh
git clone https://github.com/PhamDuyAnh/pyRPiMonitor.git /home/pi/pyRPiMonitor
cd /home/pi/pyRPiMonitor
python3 -B -m unittest discover -s tests -v
python3 -B py_rpi_monitor.py --once --stdout
sh install.sh
umask 077
nano /home/pi/.config/pyRPiMonitor/pyRPiMonitor.env
chmod 600 /home/pi/.config/pyRPiMonitor/pyRPiMonitor.env
```

Trong editor, khai báo hai biến `RPIMONITOR_MQTT_USERNAME` và
`RPIMONITOR_MQTT_PASSWORD` với giá trị thực của broker theo cú pháp systemd
EnvironmentFile (không `export`). Không đặt credential vào source, URL clone,
lịch sử shell, tài liệu mẫu hoặc Git. File mẫu `pyRPiMonitor.env.example` chỉ có
cấu hình không bí mật và tên biến bắt buộc. Thư mục config có quyền 700.

```sh
systemctl --user start pyrpimonitor.service
systemctl --user status pyrpimonitor.service
```

## Cập nhật các bản sau

```sh
cd /home/pi/pyRPiMonitor
git pull --ff-only
python3 -B -m unittest discover -s tests -v
sh install.sh
systemctl --user restart pyrpimonitor.service
```

Chỉ restart nếu các bước trước thành công. Bộ cài sao lưu unit, giữ credential,
không tự start/restart. Nếu checkout có thay đổi local hoặc lịch sử phân nhánh,
review trước; không dùng reset/clean/force. Script có thể cài từ thư mục khác bằng
cách copy source tới đường dẫn chuẩn; dùng clone đúng đường dẫn để tiếp tục cập nhật bằng Git.

## CLI và cấu hình

```sh
python3 -B py_rpi_monitor.py --once --stdout
python3 -B py_rpi_monitor.py --once --publish
python3 -B py_rpi_monitor.py --interval 60
```

`--stdout` không cần credential, không import paho và không kết nối MQTT.
Các chế độ khác mặc định publish. Chạy trực tiếp Python chỉ đọc environment
của terminal, **không tự đọc file .env**; systemd mới nạp EnvironmentFile.
Thiếu credential: dừng trước thu thập/kết nối với exit 2 và thông báo tên biến.

| CLI | Environment | Mặc định |
| --- | --- | --- |
| `--interval` | `RPIMONITOR_INTERVAL` | 60 giây, số dương hữu hạn |
| `--mqtt-host` | `RPIMONITOR_MQTT_HOST` | 127.0.0.1 |
| `--mqtt-port` | `RPIMONITOR_MQTT_PORT` | 1883 |
| `--interface` | `RPIMONITOR_INTERFACE` | Default route đang up; sau đó ưu tiên wlan0 |

CLI ưu tiên hơn environment. Topic `RPiMonitor/status`, QoS 1, retain=true,
không Last Will. Không cảnh báo hoặc tự restart OpenWebRX/Mosquitto/Pi.

## Độ tin cậy và dữ liệu

MQTT kiểm tra CONNACK, mã trả về publish và PUBACK; tối đa 3 lần thử mỗi snapshot,
nghỉ 1 rồi 2 giây. `--once --publish` thất bại trả exit 1. Chế độ liên tục bỏ
snapshot thất bại và chờ chu kỳ kế tiếp để thu thập snapshot mới; không lặp gửi
vô hạn một bản tin. Điều này cho phép phục hồi khi broker chưa sẵn sàng lúc boot.
Không bật reconnect tự động của paho; QoS 1 có thể tạo bản tin trùng khi retry.

Timeout HTTP, subprocess và chờ CONNACK/PUBACK là 5 giây; paho 1.6.1 có timeout
kết nối TCP 5 giây. Một collector lỗi không làm mất toàn bộ JSON: trường chưa đọc
được là null, lỗi ngắn nằm trong `collector_errors`. Không log exception text,
response hoặc stderr gốc. Service dùng `Restart=on-failure`, `RestartSec=10`,
giới hạn restart 3 lần trong 300 giây cho lỗi tiến trình; exit 2 không tự restart.

JSON gồm:

- `version`, timestamp UTC ISO 8601, hostname, uptime (giây).
- CPU tổng/từng core, load 1/5/15 phút, nhiệt độ °C, MHz, throttled và undervoltage.
  CPU dùng delta `/proc/stat`, idle+iowait, không cộng guest lần hai; lần đầu lấy
  hai mẫu cách 0,2 giây. Throttled bit 2/18, undervoltage bit 0/16; occurred là
  lịch sử firmware, không chỉ chu kỳ hiện tại.
- Memory/swap từ `/proc/meminfo`, `_mb` là MiB; used = total - available.
- Filesystem `/`, `_gb` là GiB, free là dung lượng user thường dùng được;
  usage = used/(used+available), có xét reserved blocks.
- Interface, IP local ưu tiên IPv4, byte/error/drop tích lũy từ `/proc/net/dev`.
- OpenWebRX unit state/PID, API reachable, version, users, số SDR,
  max_clients và feature_count. Không copy receiver metadata hoặc toàn bộ features.
- Số lỗi OpenWebRX/kernel và tối đa 5 entry mới nhất cho mỗi nhóm.

Dữ liệu OpenWebRX lấy từ `/status.json`, `/metrics.json`, `/api/features` trên
`http://127.0.0.1:8073`, không theo redirect/proxy. Mapping dựa trên
[OpenWebRX API](https://github.com/jketterl/openwebrx/wiki/API) và đã được xác nhận
qua JSON MQTT của máy hiện tại ở bản 0.1.0.

Journal chỉ đọc `(mốc thu thập trước, hiện tại]`; lần đầu 5 phút. Mốc giữ RAM,
restart có thể đọc lại entry; lỗi đọc không được backfill. Count đếm entry khớp
trong cửa sổ, latest_errors giữ tối đa 5 entry, message tối đa 400 ký tự.
Match priority 0–3, ERROR, CRITICAL, failure/failed, USB disconnect/reset,
device not found, No SDR Devices, PLL not locked, samples lost, I/O error, OOM,
undervoltage và filesystem error. Không suy luận phần cứng hỏng.

Message là mô tả cố định theo nhóm lỗi; không phát nguyên văn journal hoặc
traceback có thể chứa credential/token. Loại ANSI/control trước phân loại.
Không ghi snapshot định kỳ xuống SD; service tắt bytecode, log stdout/stderr do
journald quản lý. Việc journald lưu xuống đĩa phụ thuộc cấu hình OS.

## Chẩn đoán và gỡ cài đặt

```sh
journalctl --user -u pyrpimonitor.service -n 30 --no-pager
loginctl show-user pi --property=Linger
```

Nếu gặp `216/GROUP`, kiểm tra còn đang chạy unit cũ hoặc có override User/Group
không. Nếu báo thiếu credential khi chạy Python trực tiếp, dùng service để nạp
EnvironmentFile. Không gửi nội dung credential khi báo lỗi.

```sh
cd /home/pi/pyRPiMonitor
sh uninstall.sh
```

Chỉ stop/disable và xóa unit pyRPiMonitor. Giữ credential, checkout, backup và
lingering vì các user service khác có thể cần nó.

## Kiểm tra local

```sh
python -m py_compile py_rpi_monitor.py tests/test_parsers.py tests/test_installer.py
python -m unittest discover -s tests -v
bash -n install.sh uninstall.sh
```

Test dùng fixture Linux và mock API/MQTT/journal/CLI, không cần broker hoặc Pi.
Windows dùng Python 3.12; grammar cũng được kiểm tra tương thích Python 3.11.
Bản 0.1.1 chưa được xác nhận trên Pi sau logout hoặc boot. Không SSH/SCP, tự cài
hoặc reboot Pi trong quá trình sửa local này.

Kết quả local 0.1.1: 33 test đạt, gồm migration giả lập và phục hồi MQTT;
py_compile, grammar Python 3.11 và cú pháp shell đều đạt.
