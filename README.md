# pyRPiMonitor

**Phiên bản 0.1.1** · [GitHub](https://github.com/PhamDuyAnh/pyRPiMonitor)

`py` là viết tắt của Python. pyRPiMonitor thu thập trạng thái Raspberry Pi,
OpenWebRX và lỗi journal, rồi gửi một JSON tổng hợp qua MQTT mỗi 60 giây.
Dịch vụ chạy nền bằng user `pi`, không cần giữ phiên SSH hoặc mở VS Code.

Đã được người vận hành xác nhận trên Raspberry Pi: thu thập và nhận JSON bằng
MQTT Explorer, tự khởi động sau reboot mà không cần đăng nhập.

- MQTT topic `RPiMonitor/status`, QoS 1, retain=true; không Last Will.
- Chu kỳ cấu hình được; lỗi một collector không làm mất toàn bộ JSON.
- Không cảnh báo, không tự restart OpenWebRX, Mosquitto hoặc Raspberry Pi.
- Không ghi snapshot định kỳ xuống SD card; log do systemd/journald quản lý.

## Yêu cầu

Môi trường đã triển khai: Raspbian 12 Bookworm, kernel aarch64, user-space armhf
32-bit, Python 3.11.2, OpenWebRX 1.2.113 và Mosquitto 2.0.11.

Cần Git, Python >=3.11 và `paho-mqtt==1.6.1` có sẵn cho `/usr/bin/python3`.
Không dùng psutil. Bộ cài không cài package, không dùng sudo và chỉ chạy bằng
user `pi`. User pi cần quyền đọc journal OpenWebRX/kernel và chạy `vcgencmd`.
Broker mặc định tại `127.0.0.1:1883` và yêu cầu username/password.

## Tên và đường dẫn

| Thành phần | Giá trị |
| --- | --- |
| Dự án | pyRPiMonitor |
| Checkout trên Pi | `/home/pi/pyRPiMonitor` |
| Chương trình | `py_rpi_monitor.py` |
| User service | `pyrpimonitor.service` |
| Private environment file | `/home/pi/.config/pyRPiMonitor/pyRPiMonitor.env` |
| MQTT topic | `RPiMonitor/status` (giữ tương thích bên nhận) |
| Environment prefix | `RPIMONITOR_` (giữ tương thích cấu hình cũ) |

Topic MQTT và tên biến môi trường được giữ tương thích với bản 0.1.0.
Tên `rpi-monitor` chỉ còn dùng khi chuyển từ bản cũ.

## Cài mới

Chạy trong terminal Pi bằng user `pi`. Dùng mục chuyển từ 0.1.0 nếu đã có
service `rpi-monitor.service`. Dừng nếu một bước thất bại.

```sh
git clone https://github.com/PhamDuyAnh/pyRPiMonitor.git /home/pi/pyRPiMonitor
cd /home/pi/pyRPiMonitor
python3 -B -m unittest discover -s tests -v
python3 -B py_rpi_monitor.py --once --stdout
sh install.sh
```

Sau khi bộ cài thành công, tạo file cấu hình riêng:

```sh
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

## Vận hành không cần đăng nhập

pyRPiMonitor dùng **systemd user service của pi và lingering**. Lingering cho phép
user manager khởi động khi boot và tồn tại sau logout, theo
[tài liệu loginctl của systemd](https://github.com/systemd/systemd/blob/v252/man/loginctl.xml).
Không cần giữ SSH hoặc VS Code mở. Service kế thừa user/group của pi, không đặt
`User=`/`Group=` để tránh lỗi `216/GROUP` của unit cũ.

Bộ cài kiểm tra `Linger=yes`; nếu chưa có, thử `loginctl --no-ask-password enable-linger pi`.
Nếu policy không cho phép, bộ cài dừng với hướng dẫn, không tự dùng sudo hoặc xin
mật khẩu. Khi đó cần quản trị viên cho phép/bật lingering cho pi một lần rồi chạy
lại bộ cài. Không coi việc enable unit đơn thuần là đủ để chạy sau logout.

Kiểm tra cấu hình vận hành:

```sh
loginctl show-user pi --property=Linger
systemctl --user is-enabled pyrpimonitor.service
systemctl --user status pyrpimonitor.service
journalctl --user -u pyrpimonitor.service -n 30 --no-pager
```

Kết quả mong đợi: `Linger=yes`, `enabled`, `active (running)`. Trong MQTT Explorer,
kiểm tra `timestamp` tiếp tục tăng theo chu kỳ; bản tin retain cũ vẫn có thể hiển thị
khi publisher đã dừng. Khi kiểm tra boot trên máy mới, quan sát MQTT trước khi SSH
đăng nhập để xác nhận dịch vụ không phụ thuộc phiên đăng nhập.

## Cập nhật

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

Nếu bản cập nhật chỉ thay đổi README, chỉ cần `git pull --ff-only`; không cần cài
lại unit hoặc restart service.

## Chuyển từ bản 0.1.0

Chạy trong terminal Pi dưới user `pi`. Các lệnh này clone vào thư mục mới để giữ
checkout cũ làm bản dự phòng. Nếu `/home/pi/pyRPiMonitor` đã tồn tại, không clone
đè; kiểm tra đó là checkout đúng repository, chạy `git pull --ff-only` trong
thư mục đó rồi tiếp tục kiểm thử và `sh install.sh --migrate`.

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

Sau migration, kiểm tra hoạt động theo mục vận hành ở trên.

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
qua JSON nhận bằng MQTT trên môi trường triển khai nêu trên.

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

## Chẩn đoán

```sh
journalctl --user -u pyrpimonitor.service -n 30 --no-pager
loginctl show-user pi --property=Linger
```

Nếu gặp `216/GROUP`, kiểm tra còn đang chạy unit cũ hoặc có override User/Group
không. Nếu báo thiếu credential khi chạy Python trực tiếp, dùng service để nạp
EnvironmentFile. Không gửi nội dung credential khi báo lỗi.

## Gỡ cài đặt

```sh
cd /home/pi/pyRPiMonitor
sh uninstall.sh
```

Chỉ stop/disable và xóa unit pyRPiMonitor. Giữ credential, checkout, backup và
lingering vì các user service khác có thể cần nó.

## Kiểm thử

```sh
python -m py_compile py_rpi_monitor.py tests/test_parsers.py tests/test_installer.py
python -m unittest discover -s tests -v
bash -n install.sh uninstall.sh
```

Bộ test dùng fixture Linux và mock API/MQTT/journal/CLI. Test bộ cài thực thi
trong thư mục giả lập với các lệnh hệ thống được thay thế, không sửa dịch vụ thật;
nhóm test này cần Bash và sẽ được bỏ qua nếu không có Bash phù hợp.

Kết quả kiểm tra bản 0.1.1 trên Windows/Python 3.12: 33 test đạt, `py_compile`,
grammar Python 3.11 và cú pháp shell đạt. Người vận hành đã xác nhận ứng dụng
chạy trên Pi và tự khởi động sau reboot. Đây là kết quả trên môi trường đã nêu,
không thay thế việc kiểm tra cấu hình/quyền trên máy khác.
