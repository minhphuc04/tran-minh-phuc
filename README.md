# Bài test R&D Engineer (Infrastructure & Automation) – Trần Minh Phúc

- **Nhận đề:** 16:27, 27/10/2026
- **Nộp bài:** 12:00, 30/10/2026
- **Thời gian làm thật (ước tính):** khoảng 7 giờ 5 phút (chi tiết trong `phan4-nhat-ky.md`)
- Tại báo cáo của mỗi phần em đã trình bày rõ cách chạy tool và các giả định liên quan để thuận tiện cho việc đối chiếu trực tiếp với nội dung tương ứng trong bài làm. 

## 1. Cấu trúc bài nộp

```
tran-minh-phuc/
  README.md              # file này: tổng quan, cách chạy tool, giả định
  phan1-postmortem.md    # Báo cáo điều tra sự cố web-hcm-07
  phan2-tool/            # Tool cân bằng tải Proxmox (Python), unit test, README riêng
    proxmox_balance.py
    test_proxmox_balance.py
    phan2-tool.md
  phan3-nghien-cuu.md    # Đề B: chặn / rate-limit bot crawl, L4 (nftables) vs L7 (Nginx)
  phan4-nhat-ky.md       # Nhật ký làm bài, ghi rõ chỗ dùng AI và cách kiểm chứng
```

## 2. Tóm tắt từng phần

### Phần 1 – Điều tra sự cố `web-hcm-07`

- **Hiện tượng:** từ khoảng 01:33 ngày 20/10/2026, request động (PHP) của các website trên máy trả 502/504; khoảng 48 phút cho đến lúc on-call vào máy (02:21).
- **Nguyên nhân gốc:** phân vùng `/` đầy 100%. Logrotate của nginx hỏng từ 14/10 do lỗi gõ sai `dailly` trong `/etc/logrotate.d/nginx`, nên `access.log` phình đến 37 GB; job backup 01:00 ghi thêm ~16 GB vào cùng phân vùng và làm đĩa đầy lúc 01:32:57.
- **Chuỗi hệ quả:** đĩa đầy → MySQL treo ở bước ghi binlog → worker PHP-FPM chờ MySQL và chiếm hết `pm.max_children` → hàng đợi socket đầy → 502 (từ chối ngay) và 504 (timeout ~60s).
- **Loại trừ:** bot crawl (chỉ xuất hiện lúc 01:41, sau lỗi 5xx đầu tiên lúc 01:33), CPU/RAM/OOM, `pm.max_children` thấp, lỗi nội tại MySQL, mạng/firewall.
- **Khắc phục tức thời và phòng ngừa:** xem `phan1-postmortem.md`, mục 5 và 6. Các lệnh khắc phục không đụng tới dữ liệu, database hay source của khách.

### Phần 2 – Tool phân tích tải cluster Proxmox

Tool Python 3.10+ (chỉ dùng thư viện chuẩn) đọc snapshot JSON của cluster, thống kê tải từng node (CPU, RAM, overcommit vCPU), đánh dấu node quá tải, đề xuất kế hoạch live-migrate bằng thuật toán greedy worst-fit, và đảm bảo node nhận VM vẫn nằm trong ngưỡng an toàn. Hỗ trợ output bảng (terminal) và JSON; có 26 unit test; không crash khi gặp file lỗi hoặc thiếu trường, thay vào đó log cảnh báo.

**Cách chạy** (từ thư mục `phan2-tool/`, đường dẫn file snapshot tùy chỉnh theo vị trí bạn đặt file):

```bash
python3 --version                                   # cần >= 3.10, không cần cài thêm gì

# Bảng (mặc định)
python3 proxmox_balance.py ../phan2-proxmox/cluster-pve-hcm-c3.json

# JSON (để pipe sang tool khác)
python3 proxmox_balance.py ../phan2-proxmox/cluster-pve-hcm-c3.json --format json

# Đổi ngưỡng
python3 proxmox_balance.py ../phan2-proxmox/cluster-pve-hcm-c3.json \
  --cpu-threshold 85 --mem-threshold 90 --overcommit-threshold 5

# Unit test
python3 -m unittest test_proxmox_balance -v
```

Ngưỡng mặc định: CPU 80%, RAM 85%, overcommit vCPU 4x. Ví dụ output thực tế, thuật toán và các giả định xử lý dữ liệu nằm trong `phan2-tool/README.md`.

### Phần 3 – Nghiên cứu (Đề B): L4 nftables vs L7 Nginx `limit_req`

- So sánh theo các tiêu chí: dữ liệu nhìn thấy, độ mịn theo vhost, cơ chế, bộ nhớ, nguy cơ chặn nhầm, vị trí sau load balancer, khả năng thử nghiệm an toàn. Số liệu và nhận định có nguồn từ tài liệu chính thức của nginx và nftables; phần suy luận được gắn nhãn *(phân tích)*.
- **Khuyến nghị:** L7 (`limit_req` theo IP thật, áp ở location đi vào PHP, kèm zone theo `$server_name` làm "cầu chì") là lớp chính; L4 là lưới phụ chỉ có ý nghĩa nếu đặt ở nơi thấy IP khách thật (LB/edge), vì LB là L7 nên nftables trên node chỉ thấy IP của LB. Triển khai theo lộ trình dry-run trước, enforce sau.
- **Kiểm chứng lab:** VPS 1 vCPU, Nginx + PHP-FPM (`pm.max_children=5`), tải bằng `wrk`, 3 kịch bản baseline / dry-run / enforce. Lab mới đo phía L7, chưa đo L4.

### Phần 4 – Nhật ký

Ghi mốc thời gian từng phiên, giả định, các bước dùng AI và việc tự kiểm chứng lại, phần chưa xong và điều sẽ làm khác.

## 3. Các giả định chính

**Phần 1**
- Việc kỹ sư `hieunv` gây ra lỗi `dailly` là **suy luận**: log chỉ cho thấy anh mở file bằng vim lúc 13/10 17:42; logrotate báo lỗi từ 14/10 00:00.
- Log LB chỉ có 5 domain trong khung 00:50–02:25; coi đó là mẫu của khoảng 40 site trên máy.
- `dmesg | tail -8` chỉ có 8 dòng và không có OOM nên kết luận loại trừ OOM có độ tin cậy hạn chế; không có `df -i` nên không kiểm chứng được inode.

**Phần 2**
- `cpu_usage` trong khoảng (1, 100] được hiểu là phần trăm và chia 100 (đề nói 0–1 nhưng dữ liệu có `31.0`, `98.0`, `20.8`).
- Node `hcm-c3-n06` có `mem_total=262144` coi là dữ liệu nghi hỏng (có thể exporter xuất sai đơn vị, nếu là MiB thì đúng 256 GiB), không làm node nguồn hay đích.
- Ngưỡng 80% CPU / 85% RAM / 4x overcommit chọn theo kinh nghiệm chung, chưa hiệu chỉnh bằng dữ liệu thật.
- VM `stopped` không tính vào tải; node `offline` không nhận VM.
- Snapshot có `vmid` 2001 xuất hiện trên cả `hcm-c3-n01` và `hcm-c3-n02` (hai bản ghi giống hệt, chỉ khác node). Ban đầu tool cộng tải VM này vào cả hai node. Sau yêu cầu của anh Tuấn, tool phát hiện và cảnh báo, chỉ tính 1 lần (ưu tiên bản ghi `running`, nếu cùng trạng thái thì giữ bản xuất hiện trước) và không đề xuất migrate VM này vì không biết vị trí thật của nó. Lý do và đánh đổi được ghi trong phan2-tool.md.

**Phần 3**
- LB (`lb-hcm-01`) là L7 vì có access log HTTP ở Phần 1.
- Ngưỡng `rate`, `burst` chỉ là điểm khởi đầu; dải IP LB trong `set_real_ip_from` chỉ là ví dụ.
- Lab là mô hình thu nhỏ (1 vCPU, 1 endpoint `usleep(50000)`, tải từ máy local qua Internet nên P99 gồm RTT), chỉ dùng để so sánh xu hướng giữa các kịch bản.

## 4. Phần chưa hoàn thiện

- **Phần 1:** tốc độ ghi `access.log` (~6 GB/ngày) cao hơn nhiều so với lưu lượng thấy trong log LB; cần đọc `access.log` thật để biết vhost/IP nào ghi nhiều. Request 5xx đầu tiên (`rt=60.5s` lúc 01:33:05) bắt đầu sớm hơn lỗi `tar` khoảng 1 phút, chưa giải thích được. Các lệnh khắc phục chưa chạy trên máy thật.
- **Phần 2:** tool chưa phát hiện `vmid` trùng; chưa có test riêng cho ngưỡng biên. Greedy không tối ưu toàn cục và không tính chi phí migrate.
- **Phần 3:** chưa đo L4 để so sánh trực tiếp hai tầng; chưa mô phỏng 40 vhost hay IP dùng chung (NAT); chưa trích source code Nginx.

## 5. Về việc dùng AI

Dùng Claude và DeepSeek ở nhiều bước (đọc log, review tool, dựng khung so sánh, gợi ý cấu hình lab). Chi tiết từng bước và cách tự kiểm chứng kết quả AI nằm trong `phan4-nhat-ky.md`.
