# RPiMonitor 0.1.0

Thu thập trạng thái Raspberry Pi và OpenWebRX thành một JSON; publish tới
`RPiMonitor/status` với QoS 1, retain=true. Không Last Will, cảnh báo, tự restart
OpenWebRX/Mosquitto/Pi hoặc ghi snapshot định kỳ xuống SD card.

Mục tiêu: Raspbian 12 Bookworm, kernel aarch64/user-space armhf, Python 3.11.2,
OpenWebRX 1.2.113 (`openwebrx.service`), Mosquitto 2.0.11.
Chỉ cần `paho-mqtt==1.6.1` đã có trên Pi; không dùng psutil. Chương trình dùng
`/usr/bin/python3` và thư viện chuẩn, không tự cài package.

**Trạng thái: mã nguồn và kiểm tra local; chưa deploy hoặc kiểm thử thực tế trên Pi.**
Các lệnh cài/chạy bên dưới dành cho giai đoạn triển khai sau.

## CLI

```sh
python3 rpi_monitor.py --once --stdout
python3 rpi_monitor.py --once --publish
python3 rpi_monitor.py --interval 60
```

`--stdout` không import paho, không yêu cầu credential, không kết nối MQTT.
Không chỉ định `--stdout` thì mặc định publish; `--once` thu thập đúng một lần.
Không có `--once` thì chạy liên tục. Ctrl+C/SIGTERM dừng sau thao tác đang thực hiện.
Chu kỳ tính từ đầu lần thu thập; nếu quá chu kỳ thì lần kế tiếp bắt đầu ngay,
không tạo nhiều collector chồng lên nhau.

| CLI | Environment | Mặc định |
| --- | --- | --- |
| `--interval` | `RPIMONITOR_INTERVAL` | 60 giây, số dương hữu hạn |
| `--mqtt-host` | `RPIMONITOR_MQTT_HOST` | 127.0.0.1 |
| `--mqtt-port` | `RPIMONITOR_MQTT_PORT` | 1883 |
| `--interface` | `RPIMONITOR_INTERFACE` | Interface up của default route; sau đó ưu tiên wlan0 |

CLI ưu tiên hơn environment. URL OpenWebRX cố định `http://127.0.0.1:8073`;
HTTP và subprocess timeout 5 giây. Không theo HTTP redirect hoặc proxy environment.

Publish bắt buộc có cả `RPIMONITOR_MQTT_USERNAME` và
`RPIMONITOR_MQTT_PASSWORD` trong environment. Thiếu hoặc rỗng: dừng trước khi
thu thập/kết nối, exit 2 và chỉ in tên biến bị yêu cầu. Không có tham số CLI
nhận credential, không đọc credential từ source hoặc file trong chương trình.
Không đưa credential thật vào repository, ví dụ, dòng lệnh hoặc báo cáo.

## JSON và ý nghĩa

- `version`: phiên bản RPiMonitor; `timestamp`: UTC ISO 8601 tại đầu lần thu thập.
- `hostname`, `uptime`: hostname và uptime theo giây.
- `cpu`: `usage_percent`, `per_core_percent` theo thứ tự cpu0, cpu1..., ba load
  average, nhiệt độ °C, tần số MHz. CPU tính bằng chênh lệch `/proc/stat`, coi
  idle+iowait là nhàn rỗi, không cộng guest lần hai. Lần đầu lấy hai mẫu cách
  0,2 giây; các lần sau dùng mẫu trước. Core mới hoặc counter reset cho `null`.
- `throttled_now`/`throttled_occurred`: bit 2/18;
  `undervoltage_now`/`undervoltage_occurred`: bit 0/16 từ `get_throttled`.
  Các bit occurred biểu thị lịch sử do firmware cung cấp, không phải chỉ chu kỳ này.
- `memory`: total/used/available và swap, `_mb` tính theo MiB (1024² byte).
  Used = MemTotal - MemAvailable; swap không được cấu hình có usage 0%.
- `storage`: filesystem `/`, `_gb` tính theo GiB (1024³ byte). `used_gb` không
  tính block tự do; `free_gb` là block có thể dùng bởi user thường. Usage tính
  used/(used+available), giống cách xử lý reserved blocks của `df`.
- `network`: interface, `local_ip` (ưu tiên IPv4, sau đó IPv6 global), byte/error/drop
  tích lũy từ `/proc/net/dev`, không phải tốc độ/giây. Counter có thể reset khi reboot.
- `openwebrx`: `service_state`, `sub_state`, `main_pid`, `api_reachable` (ít nhất
  một endpoint trả JSON object hợp lệ), `api_endpoints` cho từng endpoint,
  `version` từ status, `users` từ metrics, `sdr_count` từ status.sdrs.
  `api_summary` chỉ gồm `max_clients` và `feature_count`; không sao chép receiver
  metadata, địa chỉ quản trị, tọa độ hoặc toàn bộ `/api/features`.
- `openwebrx.recent_error_count` và `system.recent_kernel_error_count`: số journal
  entry khớp trong cửa sổ, không phải số dòng traceback hay chẩn đoán phần cứng.
  `latest_errors`: tối đa 5 entry mới nhất, mỗi entry có timestamp, categories
  và message tối đa 400 ký tự.
- `collector_errors`: tên collector → loại lỗi ngắn; không chứa exception text,
  stderr hoặc response gốc. Trường không đọc được dùng `null`; một collector lỗi
  không làm mất các collector còn lại. HTTP không truy cập được có reachable=false
  và lỗi tương ứng; danh sách lỗi rỗng nghĩa là đọc thành công và không thấy lỗi.

Mapping API tham khảo [OpenWebRX API](https://github.com/jketterl/openwebrx/wiki/API).
Báo cáo khảo sát chỉ lưu kiểu và số lượng trường, nên schema thực tế của bản cài
cần được xác nhận khi thử `--stdout` trên Pi; trường lạ giữ `null` thay vì đoán.

## Journal và bảo mật dữ liệu

Lần đầu chỉ đọc 5 phút trước thời điểm thu thập. Các lần sau đọc cửa sổ
`(thời điểm thu thập trước, thời điểm hiện tại]`, ghi rõ trong `journal_window`.
Mốc chỉ giữ trong RAM; restart bắt đầu lại 5 phút, có thể gặp lại entry cũ.
Nếu journal lỗi, mốc vẫn tiến theo chu kỳ và cửa sổ lỗi không được đọc bù;
count/list là `null`, có `collector_errors`. Đồng hồ lùi tạo cửa sổ rỗng và báo lỗi.

Journal được đọc dạng stream, giữ tối đa 5 entry phù hợp trong RAM; count đếm
toàn cửa sổ. Match priority 0–3, ERROR, CRITICAL, failure/failed, USB disconnect/reset,
device not found, No SDR Devices, PLL not locked, samples lost, I/O error, OOM,
undervoltage và filesystem error. Chỉ đọc unit `openwebrx.service` và kernel boot hiện tại.

Để không phát tán credential/token không có nhãn trong log, **message là mô tả
cố định theo nhóm lỗi, không phải nguyên văn journal**. ANSI/control được bỏ trước
khi phân loại. Không gửi traceback, dòng lệnh, username, password hoặc token từ
journal. Một entry có nhiều dấu hiệu vẫn chỉ được đếm một lần. Không kết luận
thiết bị đã hỏng từ các dấu hiệu này. Hostname và local IP được gửi theo yêu cầu.

MQTT kiểm tra CONNACK, return code publish và `is_published()` sau PUBACK với
QoS 1; tối đa 3 lần thử, nghỉ 1 rồi 2 giây. Mỗi lần chờ CONNACK/PUBACK tối đa
5 giây; paho 1.6.1 có timeout kết nối TCP 5 giây. Không bật reconnect tự động.
Hết retry: exit 1; không retry vô hạn bên trong chương trình. QoS 1 có thể tạo
bản tin trùng khi retry; consumer có thể dùng hostname+timestamp để nhận diện.
PUBACK không xác nhận ứng dụng subscriber đã xử lý JSON.
Tham khảo [Paho 1.6.1](https://github.com/eclipse-paho/paho.mqtt.python/tree/v1.6.1).

## Cài đặt sau này, không dùng root

Repository: [PhamDuyAnh/pyRPiMonitor](https://github.com/PhamDuyAnh/pyRPiMonitor).
Workspace phát triển Windows: `D:\pyRPiMonitor`.

Trên Pi, trong terminal của user `pi`, clone vào đúng đường dẫn service sử dụng
(cần Git có sẵn; không chạy các lệnh này nếu thư mục đích đã có dự án):

```sh
git clone https://github.com/PhamDuyAnh/pyRPiMonitor.git /home/pi/rpi-monitor
cd /home/pi/rpi-monitor
python3 -B -m unittest discover -s tests -v
python3 -B rpi_monitor.py --once --stdout
sh install.sh
```

Nếu repository private, dùng cơ chế xác thực GitHub của Git trong terminal;
không đặt token vào URL clone hoặc source. Bộ cài dùng dependency paho đã có
trên Pi và không chạy pip. `.gitattributes` giữ script shell ở định dạng LF.

Sau khi install tạo thư mục config, tạo file riêng bằng editor:

```sh
umask 077
nano /home/pi/.config/rpi-monitor/rpi-monitor.env
chmod 600 /home/pi/.config/rpi-monitor/rpi-monitor.env
```

Trong editor, khai báo `RPIMONITOR_MQTT_USERNAME` và `RPIMONITOR_MQTT_PASSWORD`
với giá trị của broker, theo cú pháp EnvironmentFile của systemd. Có thể thêm
các biến không bí mật từ `rpi-monitor.env.example`. Sau đó chạy lệnh start ở dưới.
Không đưa file này vào Git; file khảo sát riêng và cache Python cũng không được track.

Để cập nhật một checkout sạch sau này:

```sh
cd /home/pi/rpi-monitor
git pull --ff-only
python3 -B -m unittest discover -s tests -v
sh install.sh
systemctl --user restart rpi-monitor.service
```

Lệnh cuối chỉ restart RPiMonitor để nạp mã mới và do người vận hành chạy chủ động.
Nếu Git báo thay đổi local hoặc lịch sử phân nhánh, dừng để review; không dùng
reset/clean/force. Credential ngoài checkout được giữ nguyên khi cập nhật.

Bộ cài dùng **systemd user service của pi**, không ghi `/etc/systemd/system`.
Unit vẫn có `User=pi`, `WorkingDirectory=/home/pi/rpi-monitor`,
`EnvironmentFile=/home/pi/.config/rpi-monitor/rpi-monitor.env`,
`Restart=on-failure`, `RestartSec=10`. Chỉ RPiMonitor được restart khi lỗi;
exit 2 do cấu hình/dependency không được tự restart. StartLimit giới hạn 3 lần
trong 300 giây, tránh lỗi MQTT dẫn đến restart liên tục không giới hạn.
`User=pi` hợp lệ với user manager đang chạy dưới chính user pi, theo
[systemd 252 User/Group Identity](https://github.com/systemd/systemd/blob/v252/man/systemd.exec.xml).

Sau khi có phê duyệt triển khai, đặt source ở `/home/pi/rpi-monitor` và chạy
`sh install.sh` bằng user pi. Script kiểm tra dependency đã có, sao lưu unit cũ
bằng tên duy nhất trong `~/.config/systemd/user`, copy source nếu cần, reload
user manager và enable RPiMonitor. Không start/restart service, không cài package,
không tạo hoặc đọc credential file. Bản source cũ có thể bị thay khi cài từ thư mục khác;
unit cũ luôn được backup trước khi thay thế.

Tạo file environment riêng tại đường dẫn đã chỉ định, quyền 600, thư mục cha 700;
điền hai biến credential bằng editor trực tiếp trên máy đích. Không dùng giá trị
credential mẫu, không copy credential vào source. File dùng cú pháp
`EnvironmentFile` của systemd; không có `export`, không tự source bằng shell.
`rpi-monitor.env.example` chỉ chứa cấu hình không bí mật và tên hai biến cần có.

Các lệnh vận hành sau khi đã cấu hình:

```sh
systemctl --user start rpi-monitor.service
systemctl --user status rpi-monitor.service
journalctl --user -u rpi-monitor.service -n 30 --no-pager
```

User manager phải có sẵn trong phiên đăng nhập pi. Chạy trước khi đăng nhập hoặc
duy trì sau khi logout phụ thuộc cấu hình lingering của máy. Bộ cài không thay đổi
lingering hoặc quyền hệ thống, nên chưa đảm bảo chạy lúc boot ngoài login session.
User pi dùng các quyền journal/vcgencmd đã xác nhận trong khảo sát; không thêm group.

`sh uninstall.sh` chỉ stop/disable RPiMonitor user service và xóa đúng unit đã cài.
Giữ toàn bộ credential, source, unit backup và dữ liệu người dùng.

## Kiểm tra local

```sh
python -m py_compile rpi_monitor.py tests/test_parsers.py
python -m unittest discover -s tests -v
```

Test dùng fixture `/proc/stat`, `/proc/meminfo`, `/proc/net/dev`, `get_throttled`,
cùng mock journal, API, MQTT và CLI. Không cần dữ liệu Linux, paho hoặc broker local.
Không chạy `install.sh`/`uninstall.sh` trên Windows. `--stdout` trên Windows có
thể dùng để kiểm tra JSON suy giảm, nhưng không xác minh được collector Linux.
Không ghi file runtime định kỳ; bytecode bị tắt trong service. Logging qua
stdout/stderr; việc journald lưu xuống đĩa phụ thuộc cấu hình hệ điều hành.

Kết quả local ngày 2026-09-10 trên Windows/Python 3.12.4:

- `py_compile`: đạt cho chương trình và test.
- `unittest`: 28 test đạt, gồm bốn parser được yêu cầu và các trường hợp journal/MQTT/CLI.
- `ast.parse(..., feature_version=(3, 11))`: đạt; đây là kiểm tra grammar, không thay thế chạy Python 3.11 trên Pi.
- Git Bash `bash -n install.sh uninstall.sh`: đạt; chưa thực thi cài/gỡ.
- `--once --stdout`: JSON hợp lệ trên Windows, collector Linux không có dữ liệu trả null và lỗi ngắn.
- `--once --publish` khi bỏ hai biến credential: exit 2, không phát JSON.

MQTT được kiểm tra bằng mock (bao gồm thiếu PUBACK); chưa thử broker thật, service
thật, journal thật hoặc schema API thực tế trên Pi. Không SSH/SCP hoặc deploy trong bước này.
