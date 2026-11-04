# Nhật ký làm bài

- **Họ tên:** Trần Minh Phúc
- **Nhận đề lúc:** 16:27 - 27/10/2026
- **Nộp bài lúc:** 12:00 - 30/10/2026
- **Tổng thời gian làm thật (ước tính):** Khoảng 7 giờ 5 phút
- **Sau khi cập nhật bổ sung:** Thời gian làm bài ước tính 7 giờ 50 phút

## Các phiên làm việc

| Thời điểm | Thời lượng | Làm phần nào | Ghi chú (kẹt ở đâu, đã thử gì) |
|---|---|---|---|
| 27/10 tối | ~2 giờ | Phần 1: điều tra sự cố | Dữ liệu nhiều (log LB ~1,1 MB, nginx ~210 KB). Ban đầu nghi bot crawl, nhưng bot chỉ xuất hiện lúc 01:41, sau lỗi 5xx đầu tiên lúc 01:33, nên chuyển sang hướng đĩa đầy. Sau khi có báo cáo, tự chạy lại các lệnh grep trên syslog và log LB để xác nhận mốc giờ và số lượng lỗi. |
| 28/10 tối | ~1 giờ 30 phút | Phần 2: viết tool Proxmox và test | Viết bản đầu của tool và test. AI hỗ trợ phần này |
| 28/10 tối | ~30p | Phần 2: review lại tool | Sửa: bảng hiển thị tải trước migrate, mã thoát khác 0 khi lỗi, cảnh báo node không migrate được, số test. Chạy lại test và tool trên dữ liệu thật |
| 28/10 tối | ~30 phút | Phần 3 (Đề B): chọn đề B, tham khảo các nội dung liên quan tại các documents liên quan. Research thông tin thêm từ AI và nhờ AI đưa ra 1 gợi ý khung để dễ hình dung và kiểm chứng | Viết khung so sánh L4 và L7 dựa trên các thông tin research về nftables và Nginx; đặt giả định LB là L7 nên L4 trên node chỉ thấy IP của LB |
| 28/10 tối | ~20 phút | Phần 3: đối chiếu tài liệu chính thức | Tra tài liệu nginx `limit_req`, nftables Meters, nginx realip; tự mở lại các trang để xác nhận số liệu (16.000/8.000 state mỗi MB, `limit_req_dry_run` từ 1.17.1, `ct count` không dùng với set có `timeout`) |
| 28/10 tối | ~45 phút | Phần 3: dựng lab benchmark trên VPS | Cài Docker trên VPS, tạo stack Nginx + PHP-FPM (`pm.max_children=5`), chạy `wrk` từ máy local cho 3 kịch bản baseline / dry-run / enforce; ghi lại Req/s, P99, số `REJECTED_DRY_RUN`, số Non-2xx |
| 28/10 tối | ~15 phút | Phần 3: bổ sung kết quả lab vào tài liệu | Chèn mục "Kiểm chứng lab" với bảng số liệu thật; viết nhận xét; ghi rõ hạn chế của lab (1 vCPU, 1 endpoint, tải qua Internet) |
| 28/10 | ~15 phút | Phần 3: sửa log format để đếm `REJECTED_DRY_RUN` | Ban đầu grep ra 0 do log format chưa có `$limit_req_status`; thêm `log_format` và `access_log`, chạy lại dry-run 10 giây, thu được 883/888 request |
| 29/10 tối | ~1 giờ | Phần 4: nhật ký. Xem lại những phần đã note để bổ sung vào nhật ký | Do các phần được làm/ điều chỉnh ở các ngày khác nhau dẫn đến nhiều phần bị quên nên nhật kí sẽ không đầy đủ như thực tế|

## Phần cập nhật theo góp ý của anh Tuấn:

Anh Tuấn góp ý: nhật ký ghi tool chưa xử lý `vmid` 2001 xuất hiện trên cả `hcm-c3-n01` và `hcm-c3-n02`, tool đang cộng tải VM này vào cả hai node. Yêu cầu: phát hiện, cảnh báo, không tính một VM hai lần, tự chọn cách xử lý và ghi lý do trong README, kèm test.

| Thời điểm | Thời lượng | Làm phần nào | Ghi chú |
|---|---|---|---|
| 03/11 Tối | 45 phút | Phần 2: bổ sung xử lý `vmid` trùng | Sửa `proxmox_balance.py` (phát hiện, cảnh báo, chỉ tính 1 lần, không đề xuất migrate VM trùng); thêm 6 test (tổng 32 test, trong đó có test cho VM thiếu `vmid` trước đó chưa có). Chạy lại tool trên dữ liệu thật: `hcm-c3-n02` từ RAM 59,7% còn 55,0% và vCPU 92 còn 80, kế hoạch ở ngưỡng mặc định không đổi. 

## Giả định đã đặt ra

**Phần 1**
- Việc kỹ sư `hieunv` gây ra lỗi `dailly` là **suy luận**. Log chỉ cho thấy anh ấy mở file bằng vim lúc 13/10 17:42; logrotate chạy tốt ngày 13/10 và báo lỗi từ 14/10 00:00.
- Log LB chỉ có 5 domain trong khung 00:50-02:25; mình coi đó là mẫu của khoảng 40 site trên máy.
- `dmesg | tail -8` không có dòng OOM, nhưng chỉ có 8 dòng nên kết luận loại trừ OOM có độ tin cậy hạn chế. Không có `df -i` nên không kiểm chứng được inode.

**Phần 2**
- `cpu_usage` trong khoảng (1, 100] được hiểu là phần trăm và chia 100 (đề nói 0-1 nhưng dữ liệu có `31.0`, `98.0`, `20.8`).
- Node `hcm-c3-n06` có `mem_total=262144` được coi là dữ liệu hỏng, không làm node nguồn hoặc đích; nghi exporter xuất sai đơn vị (nếu là MiB thì đúng 256 GiB).
- Ngưỡng 80% CPU / 85% RAM / 4x overcommit chọn theo kinh nghiệm chung, chưa hiệu chỉnh bằng dữ liệu thật.
- VM `stopped` không tính vào tải; node `offline` không nhận VM.    
- Snapshot có `vmid` 2001 xuất hiện trên cả `hcm-c3-n01` và `hcm-c3-n02` (hai bản ghi giống hệt, chỉ khác node). Ban đầu tool cộng tải VM này vào cả hai node. Sau yêu cầu của anh Tuấn, tool phát hiện và cảnh báo, chỉ tính 1 lần (ưu tiên bản ghi `running`, nếu cùng trạng thái thì giữ bản xuất hiện trước) và không đề xuất migrate VM này vì không biết vị trí thật của nó. Lý do và đánh đổi được ghi trong phan2-tool.md.

**Phần 3**
- LB (`lb-hcm-01`) là L7 vì có access log HTTP ở Phần 1. Do đó nftables trên node chỉ thấy IP của LB, không đọc được `X-Forwarded-For` hay PROXY protocol, nên lớp L4 chặn IP khách được đặt ở LB/edge, còn trên node L4 chỉ dùng để cho phép 80/443 từ IP LB.
- Ngưỡng `rate`, `burst` trong cấu hình mẫu chỉ là điểm khởi đầu, chưa đo trên traffic thực; dải IP của LB trong `set_real_ip_from` là ví dụ.
- Lab dùng `pm.max_children=5` và endpoint `usleep(50000)` để mô phỏng tác vụ tốn worker; đây là mô hình thu nhỏ, không đại diện cho 40 vhost thật. Tải chạy từ máy local qua Internet nên P99 bao gồm RTT, chỉ dùng để so sánh tương đối.

## Câu hỏi đã gửi cho người ra đề

- Không có.
## Dùng AI và công cụ

| Bước | Công cụ | Dùng để làm gì | Bạn đã kiểm tra, sửa lại gì |
|---|---|---|---|
| Phần 1: đọc log, tìm nguyên nhân tổng quát, dựng khung postmortem | Claude | Đọc syslog, snapshot, log nginx/php/mysql/LB; đếm 502/504, so mốc giờ bot với lỗi đầu tiên. Gợi ý các bước khắc phục| Em xem tổng quan hết tất cả file logs để nắm tổng quan, research các nguyên nhân phổ biến gây ra lỗi 502/504. Từ các thông tin AI phân tích tổng quan, em có chạy lại các lệnh để xem log chi tiết, đối chiếu và nhận định.|
| Phần 2: viết tool và review tool | Claude / DeepSeek | Chỉ ra 4 điểm cần sửa (bảng trước/sau migrate, exit code, số test, node không migrate được); chạy test và tool| Em có nhờ AI kiểm tra sơ qua tool thì ghi nhận có các điểm cần sửa, em có chạy lại tool kiểm tra các kết quả ban đâu, tìm nguyên nhân tại sao lại ra kết quả mà bị báo các điểm cần điều chỉnh. Sau đó em có tham khảo các điều chỉnh từ AI để điều chỉnh lại tool, khắc phục các điểm cần sửa |
| Phần 2: kiểm tra dữ liệu | Claude | Phát hiện `vmid` 2001 trùng trên hai node | Mở file `cluster-pve-hcm-c3.json` nhìn tổng quan trước thì ghi nhận `vmid 2001 ` xuất hiện 2 lần và trên 2 node khác nhau |
| Phần 3: viết bản nháp | Claude | Research thêm thông tin về L4 và L7, tham khảo AI để xây dựng khung so sánh và đề xuất cấu trúc bảng tiêu chí. Từ khung gợi ý, em xem xét các tiêu chí phù hợp, đối chiếu thông tin kỹ thuật và kiểm chứng tính chính xác trước khi hoàn thiện bảng so sánh.| Tự viết lại nội dung theo hiểu biết của mình; tự chọn Đề B; tự đặt giả định LB là L7 |
| Phần 3: rà soát logic | Claude | Chỉ ra lỗi logic: nftables trên node không đọc được XFF/PROXY protocol nếu LB là L7 | Tự suy luận lại và đồng ý; sửa lại phần L4 trong bảng và khuyến nghị |
| Phần 3: đối chiếu tài liệu chính thức | Claude | Đối chiếu số liệu với tài liệu nginx `limit_req`, nftables Meters, nginx realip | Tự mở lại 3 trang tài liệu để xác nhận các con số và mốc phiên bản |
| Phần 3: dựng lab benchmark | Claude / DeepSeek | Đưa ra cấu trúc tổng quan để hình dung cách triển khai. Gợi ý Dockerfile, `docker-compose.yml`, cấu hình Nginx, lệnh `wrk`, cách đọc `fpm-status` | Tự chạy toàn bộ lệnh trên VPS và máy local; tự phát hiện `REJECTED_DRY_RUN=0` và sửa log format |
| Phần 3: viết nhận xét lab | Claude | Diễn giải ý nghĩa các con số (P99 giảm khoảng 8 lần, throughput ~11k là tốc độ từ chối) | Tự đọc lại output `wrk`, tự kiểm tra con số 883/888 và 352.681/352.986 khớp nhau |
| Phần 3: sửa lỗi log format | Claude / DeepSeek | Thêm `log_format` có `$limit_req_status`, chạy lại dry-run | Tự chạy `sed`, tự `grep` xác nhận, tự chạy lại `wrk` 10 giây và đếm log |
| Phần 3: trình bày | Claude | Điều chỉnh lại format Markdown, hỗ trợ phần nội dung và khung triển khai | Rà soát, đọc lại nội dung, kiểm chứng và đối chiếu với các tài liệu liên quan |

## Phần chưa xong hoặc chưa hài lòng

- **Phần 1:** tốc độ ghi `access.log` (~6 GB/ngày) cao hơn nhiều so với lưu lượng thấy trong log LB; cần đọc `access.log` thật để biết vhost/IP nào ghi nhiều. Các lệnh khắc phục chưa chạy trên máy thật.
- **Phần 1:** request 5xx đầu tiên trong log LB có `rt=60.5s` ghi lúc 01:33:05, tức bắt đầu sớm hơn lỗi `tar` khoảng 1 phút; chưa giải thích được.
- **Phần 2:** chưa có test riêng cho ngưỡng đúng bằng giá trị biên. Greedy không tối ưu toàn cục và không tính chi phí migrate. Với `vmid` trùng, tool chỉ chọn một bản ghi theo quy tắc cố định chứ không biết vị trí thật của VM, nên cần đối chiếu với Proxmox thật.
- **Phần 3:** lab mới đo phía L7, chưa đo L4 (nftables) để có số liệu so sánh trực tiếp hai tầng. Nếu có thêm thời gian: thêm kịch bản nftables meter/limit rate chặn theo IP, so sánh Req/s, P99, CPU giữa L4-only / L7-only / L7+L4.
- **Phần 3:** lab chỉ có 1 endpoint, chưa mô phỏng 40 vhost nên chưa kiểm chứng được "độ mịn theo khách hàng" (áp `limit_req` riêng từng vhost) trong thực tế.
- **Phần 3:** chưa mô phỏng IP dùng chung/NAT để đo rủi ro chặn nhầm.
- **Phần 3:** chưa trích source code Nginx cho cơ chế đếm/xóa state; nhận định dựa trên tài liệu chính thức.

## Nếu làm lại, bạn sẽ làm khác điều gì?

- Đặt rõ giả định về kiến trúc LB (L4 hay L7) ngay từ đầu, trước khi thiết kế lớp L4, vì giả định này quyết định L4 đặt ở đâu.
- Dựng lab sớm hơn và đo cả L4 lẫn L7, để mọi nhận định hiệu năng đều có số liệu thật thay vì chỉ so sánh gián tiếp.
- Tìm hiểu kĩ về bản chất và ý nghĩa của từng phần. Thực chất em chưa được làm trực tiếp trong môi trường thực tế về các nội dung trên nên có nhờ AI hỗ trợ nhiều. Nhưng em có chủ động research, kiểm chứng lại để hiểu bản chất hơn.
