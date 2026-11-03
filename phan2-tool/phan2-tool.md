# Proxmox Cluster Load Balancer

Tool phân tích snapshot cluster Proxmox VE, phát hiện node quá tải và đề xuất
kế hoạch live-migrate VM để cân bằng tài nguyên.

Viết bằng **Python 3.10+**, **không cần dependency ngoài** (chỉ stdlib).

## Cài đặt

```bash
cd phan2-tool
python3 --version   # cần >= 3.10 (Windows: python --version)
# không cần pip install
```

## Cách chạy

```bash
# Output dạng bảng (mặc định)
python3 proxmox_balance.py ../phan2-proxmox/cluster-pve-hcm-c3.json

# Output JSON (để pipe sang tool/script tự động hóa khác)
python3 proxmox_balance.py ../phan2-proxmox/cluster-pve-hcm-c3.json --format json

# Tùy chỉnh ngưỡng
python3 proxmox_balance.py ../phan2-proxmox/cluster-pve-hcm-c3.json --cpu-threshold 85 --mem-threshold 90 --overcommit-threshold 5

# Chạy unit test
python3 -m unittest test_proxmox_balance -v

# Sinh lại README này
python3 generate_readme.py
```

Mã thoát: `0` khi chạy thành công, `1` khi lỗi đầu vào (file không tồn tại, JSON hỏng, root không phải object). Thông báo lỗi in ra stderr.

## Ngưỡng đánh giá & giả định

| Ngưỡng | Mặc định | Lý do |
|---|---|---|
| CPU thực dùng | **80%** | Trên 80% dễ gây timeout, ảnh hưởng VM khác trên cùng node |
| RAM thực dùng | **85%** | Buffer cho kernel cache và spike đột ngột |
| Overcommit vCPU | **4.0x** | Tỷ lệ phổ biến cho workload hosting; vượt 4x dễ tranh chấp CPU |

Tất cả ngưỡng đều override được qua CLI. Node bị coi là **quá tải khi vượt ngưỡng (`>`)**; node đích phải **không vượt ngưỡng (`<=`)** sau khi nhận VM.

Đây là các con số chọn theo kinh nghiệm chung, chưa được hiệu chỉnh bằng số liệu thực tế của cluster; trong vận hành thật nên điều chỉnh theo SLA và dữ liệu lịch sử.

### Giả định xử lý dữ liệu

Đề bài không nói rõ một số trường hợp biên. Tool áp dụng các giả định sau (đều có cảnh báo rõ ràng, không crash):

1. **`cpu_usage` ngoài [0, 1]:** nếu `1 < cpu_usage <= 100` thì giả định đơn vị là **phần trăm** và chia 100; nếu `> 100` thì clamp về `1.0`. Lý do: dữ liệu thực tế có `31.0`, `98.0`, `20.8`. Đây là suy đoán, vì đề nói trường này chạy từ 0 đến 1; nếu đoán sai, tải node bị ước tính lệch.
2. **`mem_used` là `null`/thiếu/không hợp lệ:** bỏ qua VM đó khỏi phép tính tải và cảnh báo.
3. **`mem_used > mem_max`:** clamp `mem_used` về `mem_max`, cảnh báo.
4. **`vcpus <= 0` hoặc không phải số nguyên, thiếu `vmid`/`node`:** bỏ qua VM, cảnh báo.
5. **VM `stopped`:** không tính vào tải thực (CPU/RAM) và `vcpu_allocated`; không đề xuất migrate.
6. **Node `offline`:** không nhận VM, không đưa vào phân tích quá tải.
7. **Node có `mem_total < 1GB` hoặc `cpu_cores <= 0`:** coi là **dữ liệu nghi hỏng**, hiển thị `n/a`, không làm node nguồn lẫn node đích. Ví dụ `hcm-c3-n06` có `mem_total=262144`; nếu đơn vị thực là MiB thì đúng 256 GiB (nghi exporter xuất sai đơn vị), nhưng tool không tự đoán mà để người vận hành kiểm tra.
8. **VM trỏ tới node không có trong `nodes[]`:** cảnh báo (orphan), VM không được tính vào node nào.
9. **Điều kiện migrate:** VM chỉ chuyển sang node đích nếu node đích đang không quá tải và sau khi nhận vẫn thỏa **cả 3 ngưỡng**. Nếu không có node phù hợp thì **không migrate** và có cảnh báo riêng cho node nguồn.
10. **Đơn vị hiển thị:** `GB` = 10^9 byte (hệ thập phân), nên node 512 GiB hiển thị là `549.76GB`. Phép tính phần trăm dùng byte nên không bị ảnh hưởng.
11. **`vmid` trùng lặp:** `vmid` là duy nhất trong cluster, nên một VM chỉ nằm trên một node. Nếu cùng `vmid` xuất hiện nhiều lần (trong snapshot đề bài, `vmid` 2001 `gitlab-453` có trên cả `hcm-c3-n01` và `hcm-c3-n02`), tool: (a) **phát hiện** và **cảnh báo** kèm tên các node và cho biết nội dung các bản ghi giống hệt hay khác nhau; (b) **chỉ tính 1 lần**, ưu tiên bản ghi `running`, nếu cùng trạng thái thì giữ bản ghi xuất hiện trước; (c) **không đề xuất migrate** VM này.

#### Vì sao xử lý `vmid` trùng như vậy

- **Không cộng hai lần:** cộng cả hai bản ghi làm tải cluster cao hơn thực tế và có thể khiến node nhận bị coi là đầy trong khi không phải. Trên snapshot đề bài, trước khi xử lý `hcm-c3-n02` bị cộng thừa 12 vCPU và ~25,6GB RAM (RAM 59,7% so với 55,0% sau khi xử lý).
- **Giữ một bản ghi theo quy tắc cố định, không loại cả hai:** loại cả hai sẽ làm các node trông nhẹ hơn thực tế và dễ đề xuất migrate quá tay; giữ một bản ghi theo quy tắc cố định cho kết quả lặp lại được. Ưu tiên bản `running` vì VM đang chạy mới tiêu tốn tài nguyên.
- **Không migrate VM này:** vì không biết nó đang nằm ở đâu (có thể snapshot lấy giữa lúc migrate), đề xuất chuyển nó có thể sai nguồn hoặc đụng một migrate đang chạy. Tool vẫn tính tải của nó vào một node để node đó không bị đánh giá thấp.
- **Đánh đổi:** node bị bỏ bản ghi sẽ hiện nhẹ hơn (ở đây `hcm-c3-n02`), còn node được giữ bản ghi (`hcm-c3-n01`, đang quá tải) vẫn giữ nguyên tải. Nếu bản ghi giữ lại là bản sai, tải của hai node này lệch; vì vậy cảnh báo luôn được in ra và cần xác minh với Proxmox.

## Ví dụ output thực tế

### 1. Bảng, ngưỡng mặc định (80/85/4)

```bash
python3 proxmox_balance.py ../phan2-proxmox/cluster-pve-hcm-c3.json
```

```text
=== TẢI TỪNG NODE (TRƯỚC MIGRATE) ===
NODE           STATUS         CPU    CPU%                 MEM    MEM%  vCPU    OC  TÌNH TRẠNG
---------------------------------------------------------------------------------------------
hcm-c3-n01     online     55.0/64   85.9%   511.27GB/549.76GB   93.0%   110 1.72x  QUÁ TẢI      CPU 85.9% > 80% RAM 93.0% > 85%
hcm-c3-n02     online     25.0/64   39.1%   302.37GB/549.76GB   55.0%    80 1.25x  ok
hcm-c3-n03     online     18.2/48   38.0%   193.79GB/412.32GB   47.0%    80 1.67x  ok
hcm-c3-n04     offline     0.0/48    0.0%     0.00GB/412.32GB    0.0%     0 0.00x  offline
hcm-c3-n05     online     44.8/64   70.0%   395.07GB/549.76GB   71.9%    60 0.94x  ok
hcm-c3-n06     online         n/a     n/a                 n/a     n/a    94   n/a  DỮ LIỆU HỎNG mem_total=262144 bytes quá nhỏ (<1GB) - dữ liệu nghi hỏng (nếu đơn vị là MiB thì 262144 MiB = 256 GiB -> có thể exporter xuất sai đơn vị)

=== CẢNH BÁO DỮ LIỆU ===
  [!] node hcm-c3-n04: node offline - không thể nhận VM
  [!] node hcm-c3-n06: mem_total=262144 bytes quá nhỏ (<1GB) - dữ liệu nghi hỏng (nếu đơn vị là MiB thì 262144 MiB = 256 GiB -> có thể exporter xuất sai đơn vị)
  [!] vmid=2067 (wp-host-992): cpu_usage=31.0 ngoài [0,1] -> giả định đơn vị %, chia 100
  [!] vmid=2073 (vps-nvme-430): cpu_usage=98.0 ngoài [0,1] -> giả định đơn vị %, chia 100
  [!] vmid=2065 (cloud-std-682): cpu_usage=20.8 ngoài [0,1] -> giả định đơn vị %, chia 100
  [!] vmid=2001 (gitlab-453): xuất hiện nhiều lần (node hcm-c3-n01 và hcm-c3-n02), nội dung giống hệt; chỉ tính 1 lần theo bản ghi ở node hcm-c3-n01, bỏ bản ghi ở node hcm-c3-n02, không đề xuất migrate VM này
  [!] vmid=2199 (wp-host-377): mem_used=None, bỏ qua
  [!] vmid=2390 (vps-nvme-512): node='hcm-c3-n07' không có trong danh sách nodes

=== KẾ HOẠCH MIGRATE ===
  Đề xuất 1 migration:
  - vmid=2054 (cloud-std-657) hcm-c3-n01 -> hcm-c3-n03 [8vCPU, 66.53GB] - giải phóng 66.5GB khỏi node quá tải

=== TẢI SAU MIGRATE (dự kiến) ===
  hcm-c3-n01: CPU 74.1% | RAM 80.9% | OC 1.59x
  hcm-c3-n02: CPU 39.1% | RAM 55.0% | OC 1.25x
  hcm-c3-n03: CPU 53.8% | RAM 63.1% | OC 1.83x
  hcm-c3-n05: CPU 70.0% | RAM 71.9% | OC 0.94x
  hcm-c3-n06: n/a (dữ liệu nghi hỏng, cần kiểm tra thủ công)
```

Bảng đầu tiên là tải **trước** migrate, nên thấy được node nào đang quá tải. Phần cuối là tải dự kiến **sau** migrate.

### 2. Bảng, ngưỡng chặt (70/70/2)

```bash
python3 proxmox_balance.py ../phan2-proxmox/cluster-pve-hcm-c3.json \
  --cpu-threshold 70 --mem-threshold 70 --overcommit-threshold 2
```

```text
=== TẢI TỪNG NODE (TRƯỚC MIGRATE) ===
NODE           STATUS         CPU    CPU%                 MEM    MEM%  vCPU    OC  TÌNH TRẠNG
---------------------------------------------------------------------------------------------
hcm-c3-n01     online     55.0/64   85.9%   511.27GB/549.76GB   93.0%   110 1.72x  QUÁ TẢI      CPU 85.9% > 70% RAM 93.0% > 70%
hcm-c3-n02     online     25.0/64   39.1%   302.37GB/549.76GB   55.0%    80 1.25x  ok
hcm-c3-n03     online     18.2/48   38.0%   193.79GB/412.32GB   47.0%    80 1.67x  ok
hcm-c3-n04     offline     0.0/48    0.0%     0.00GB/412.32GB    0.0%     0 0.00x  offline
hcm-c3-n05     online     44.8/64   70.0%   395.07GB/549.76GB   71.9%    60 0.94x  QUÁ TẢI      RAM 71.9% > 70%
hcm-c3-n06     online         n/a     n/a                 n/a     n/a    94   n/a  DỮ LIỆU HỎNG mem_total=262144 bytes quá nhỏ (<1GB) - dữ liệu nghi hỏng (nếu đơn vị là MiB thì 262144 MiB = 256 GiB -> có thể exporter xuất sai đơn vị)

=== CẢNH BÁO DỮ LIỆU ===
  [!] node hcm-c3-n04: node offline - không thể nhận VM
  [!] node hcm-c3-n06: mem_total=262144 bytes quá nhỏ (<1GB) - dữ liệu nghi hỏng (nếu đơn vị là MiB thì 262144 MiB = 256 GiB -> có thể exporter xuất sai đơn vị)
  [!] vmid=2067 (wp-host-992): cpu_usage=31.0 ngoài [0,1] -> giả định đơn vị %, chia 100
  [!] vmid=2073 (vps-nvme-430): cpu_usage=98.0 ngoài [0,1] -> giả định đơn vị %, chia 100
  [!] vmid=2065 (cloud-std-682): cpu_usage=20.8 ngoài [0,1] -> giả định đơn vị %, chia 100
  [!] vmid=2001 (gitlab-453): xuất hiện nhiều lần (node hcm-c3-n01 và hcm-c3-n02), nội dung giống hệt; chỉ tính 1 lần theo bản ghi ở node hcm-c3-n01, bỏ bản ghi ở node hcm-c3-n02, không đề xuất migrate VM này
  [!] vmid=2199 (wp-host-377): mem_used=None, bỏ qua
  [!] vmid=2390 (vps-nvme-512): node='hcm-c3-n07' không có trong danh sách nodes

=== KẾ HOẠCH MIGRATE ===
  Đề xuất 3 migration:
  - vmid=2054 (cloud-std-657) hcm-c3-n01 -> hcm-c3-n03 [8vCPU, 66.53GB] - giải phóng 66.5GB khỏi node quá tải
  - vmid=2057 (k8s-worker-295) hcm-c3-n01 -> hcm-c3-n02 [8vCPU, 64.80GB] - giải phóng 64.8GB khỏi node quá tải
  - vmid=2239 (gitlab-999) hcm-c3-n05 -> hcm-c3-n03 [4vCPU, 17.93GB] - giải phóng 17.9GB khỏi node quá tải

=== TẢI SAU MIGRATE (dự kiến) ===
  hcm-c3-n01: CPU 62.6% | RAM 69.1% | OC 1.47x
  hcm-c3-n02: CPU 50.6% | RAM 66.8% | OC 1.38x
  hcm-c3-n03: CPU 58.6% | RAM 67.5% | OC 1.92x
  hcm-c3-n05: CPU 66.4% | RAM 68.6% | OC 0.88x
  hcm-c3-n06: n/a (dữ liệu nghi hỏng, cần kiểm tra thủ công)
```

### 3. JSON (rút gọn)

```bash
python3 proxmox_balance.py ../phan2-proxmox/cluster-pve-hcm-c3.json --format json
```

Output đầy đủ có `nodes_before` và `nodes_after` cho từng node. Dưới đây chỉ giữ node đầu tiên, danh sách migrate và cảnh báo:

```json
{
  "summary": {
    "total_nodes": 6,
    "online_nodes": 5,
    "overloaded_nodes": [
      "hcm-c3-n01"
    ],
    "migration_count": 1,
    "warning_count": 6
  },
  "nodes_before": [
    {
      "name": "hcm-c3-n01",
      "status": "online",
      "cpu_cores": 64,
      "mem_total": 549755813888,
      "cpu_used_cores": 54.992,
      "cpu_usage_pct": 85.93,
      "mem_used": 511272906909,
      "mem_usage_pct": 93.0,
      "vcpu_allocated": 110,
      "overcommit_ratio": 1.719,
      "is_overloaded": true,
      "is_eligible_target": true,
      "overload_reasons": [
        "CPU 85.9% > 80%",
        "RAM 93.0% > 85%"
      ],
      "warnings": []
    }
  ],
  "nodes_after": [
    {
      "name": "hcm-c3-n01",
      "status": "online",
      "cpu_cores": 64,
      "mem_total": 549755813888,
      "cpu_used_cores": 47.416,
      "cpu_usage_pct": 74.09,
      "mem_used": 444743779097,
      "mem_usage_pct": 80.9,
      "vcpu_allocated": 102,
      "overcommit_ratio": 1.594,
      "is_overloaded": false,
      "is_eligible_target": true,
      "overload_reasons": [],
      "warnings": []
    }
  ],
  "migrations": [
    {
      "vmid": 2054,
      "name": "cloud-std-657",
      "from": "hcm-c3-n01",
      "to": "hcm-c3-n03",
      "vcpus": 8,
      "mem_used": 66529127812,
      "reason": "giải phóng 66.5GB khỏi node quá tải"
    }
  ],
  "warnings": [
    "vmid=2067 (wp-host-992): cpu_usage=31.0 ngoài [0,1] -> giả định đơn vị %, chia 100",
    "vmid=2073 (vps-nvme-430): cpu_usage=98.0 ngoài [0,1] -> giả định đơn vị %, chia 100",
    "vmid=2065 (cloud-std-682): cpu_usage=20.8 ngoài [0,1] -> giả định đơn vị %, chia 100",
    "vmid=2001 (gitlab-453): xuất hiện nhiều lần (node hcm-c3-n01 và hcm-c3-n02), nội dung giống hệt; chỉ tính 1 lần theo bản ghi ở node hcm-c3-n01, bỏ bản ghi ở node hcm-c3-n02, không đề xuất migrate VM này",
    "vmid=2199 (wp-host-377): mem_used=None, bỏ qua",
    "vmid=2390 (vps-nvme-512): node='hcm-c3-n07' không có trong danh sách nodes"
  ]
}
```

## Unit test

```bash
python3 -m unittest test_proxmox_balance -v
```

```text
test_high_cpu_marks_overloaded (test_proxmox_balance.TestAnalyze.test_high_cpu_marks_overloaded) ... ok
test_high_mem_marks_overloaded (test_proxmox_balance.TestAnalyze.test_high_mem_marks_overloaded) ... ok
test_overcommit_marks_overloaded (test_proxmox_balance.TestAnalyze.test_overcommit_marks_overloaded) ... ok
test_tiny_mem_node_not_analyzed (test_proxmox_balance.TestAnalyze.test_tiny_mem_node_not_analyzed)
Node dữ liệu hỏng không bị đánh dấu quá tải (tránh nhiễu). ... ok
test_json_structure (test_proxmox_balance.TestJsonOutput.test_json_structure) ... ok
test_cpu_usage_above_100_clamped (test_proxmox_balance.TestLoadSnapshot.test_cpu_usage_above_100_clamped) ... ok
test_cpu_usage_percent_converted (test_proxmox_balance.TestLoadSnapshot.test_cpu_usage_percent_converted) ... ok
test_invalid_json_raises (test_proxmox_balance.TestLoadSnapshot.test_invalid_json_raises) ... ok
test_load_valid (test_proxmox_balance.TestLoadSnapshot.test_load_valid) ... ok
test_mem_used_clamped_to_max (test_proxmox_balance.TestLoadSnapshot.test_mem_used_clamped_to_max) ... ok
test_missing_file_raises (test_proxmox_balance.TestLoadSnapshot.test_missing_file_raises) ... ok
test_offline_node_not_eligible (test_proxmox_balance.TestLoadSnapshot.test_offline_node_not_eligible) ... ok
test_orphan_node_warning (test_proxmox_balance.TestLoadSnapshot.test_orphan_node_warning) ... ok
test_skip_vm_invalid_vcpus (test_proxmox_balance.TestLoadSnapshot.test_skip_vm_invalid_vcpus) ... ok
test_skip_vm_missing_mem_used (test_proxmox_balance.TestLoadSnapshot.test_skip_vm_missing_mem_used) ... ok
test_stopped_vm_not_counted_in_load (test_proxmox_balance.TestLoadSnapshot.test_stopped_vm_not_counted_in_load) ... ok
test_tiny_mem_node_not_eligible (test_proxmox_balance.TestLoadSnapshot.test_tiny_mem_node_not_eligible)
Node có mem_total < 1GB (dữ liệu hỏng) không được làm target. ... ok
test_migration_does_not_overload_target (test_proxmox_balance.TestPlanMigrations.test_migration_does_not_overload_target) ... ok
test_migration_success (test_proxmox_balance.TestPlanMigrations.test_migration_success) ... ok
test_offline_node_never_target (test_proxmox_balance.TestPlanMigrations.test_offline_node_never_target) ... ok
test_overload_reasons_kept_when_no_target (test_proxmox_balance.TestPlanMigrations.test_overload_reasons_kept_when_no_target)
Nếu không migrate được, reasons của node quá tải phải giữ nguyên. ... ok
test_overload_reasons_recomputed (test_proxmox_balance.TestPlanMigrations.test_overload_reasons_recomputed)
Sau migrate thành công, reasons của node nguồn phải rỗng. ... ok
test_tiny_mem_node_never_target (test_proxmox_balance.TestPlanMigrations.test_tiny_mem_node_never_target) ... ok
test_tiny_mem_node_not_in_migration_source (test_proxmox_balance.TestPlanMigrations.test_tiny_mem_node_not_in_migration_source)
Node hỏng không được sinh migration (dù có VM). ... ok
test_ambiguous_vm_never_migrated (test_proxmox_balance.TestVmidHandling.test_ambiguous_vm_never_migrated)
VM trùng vmid không được đề xuất migrate, dù là VM lớn nhất. ... ok
test_duplicate_prefers_running_record (test_proxmox_balance.TestVmidHandling.test_duplicate_prefers_running_record)
Nếu một bản ghi stopped, một running: giữ bản running. ... ok
test_duplicate_vmid_counted_once (test_proxmox_balance.TestVmidHandling.test_duplicate_vmid_counted_once)
Cùng vmid trên 2 node: chỉ tính 1 lần, cảnh báo, đánh dấu mơ hồ. ... ok
test_duplicate_vmid_different_content_warns (test_proxmox_balance.TestVmidHandling.test_duplicate_vmid_different_content_warns) ... ok
test_duplicate_vmid_same_node_counted_once (test_proxmox_balance.TestVmidHandling.test_duplicate_vmid_same_node_counted_once) ... ok
test_skip_vm_missing_vmid (test_proxmox_balance.TestVmidHandling.test_skip_vm_missing_vmid) ... ok
test_accepts_within_thresholds (test_proxmox_balance.TestWouldStayHealthy.test_accepts_within_thresholds) ... ok
test_rejects_when_mem_over (test_proxmox_balance.TestWouldStayHealthy.test_rejects_when_mem_over) ... ok

----------------------------------------------------------------------
Ran 32 tests in 0.006s

OK
```

Có **32 test case** cho các phần logic cốt lõi:

- Đọc JSON hợp lệ / JSON hỏng / thiếu file (thoát với `SystemExit`)
- Bỏ qua VM có `mem_used=None` hoặc `vcpus=0`
- Chuyển `cpu_usage` từ % sang tỷ lệ, clamp khi `> 100`
- Clamp `mem_used > mem_max`; cảnh báo orphan node
- VM `stopped` không tính vào tải
- Node `mem_total < 1GB` hoặc `offline` không được làm node đích/nguồn
- Phát hiện quá tải theo CPU / RAM / overcommit
- Migrate thành công mà không đẩy node đích vào quá tải
- `overload_reasons` được tính lại sau migrate (cả khi migrate được và không được)
- Cấu trúc JSON output (`nodes_before`, `nodes_after`, `null` cho node không hợp lệ)
- VM thiếu `vmid` bị bỏ qua và có cảnh báo
- `vmid` trùng: chỉ tính 1 lần (cả khi trùng khác node và cùng node), cảnh báo giống hệt/khác nhau, ưu tiên bản `running`, đánh dấu mơ hồ, không bao giờ bị đề xuất migrate

Chưa có test riêng cho ngưỡng đúng bằng giá trị biên (quá tải khi `>` ngưỡng, node đích phải `<=` ngưỡng).

### Xử lý ngoại lệ an toàn

**Case 1: file không tồn tại**

```bash
python3 proxmox_balance.py khong-ton-tai.json; echo "exit code: $?"
```

```text
LỖI: không tìm thấy file khong-ton-tai.json
exit code: 1
```

**Case 2: file JSON hỏng** (`bad.json` chứa `{not valid json`)

```bash
python3 proxmox_balance.py bad.json; echo "exit code: $?"
```

```text
LỖI: file JSON không hợp lệ (bad.json): Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
exit code: 1
```

**Case 3: JSON thiếu trường: không crash, có cảnh báo từng bản ghi**

`partial.json`:

```json
{
  "nodes": [
    {
      "node": "n1",
      "status": "online",
      "cpu_cores": 8,
      "mem_total": 16000000000
    }
  ],
  "vms": [
    {
      "vmid": 1,
      "name": "ok",
      "node": "n1",
      "status": "running",
      "vcpus": 2,
      "cpu_usage": 0.5,
      "mem_max": 4000000000,
      "mem_used": 2000000000
    },
    {
      "vmid": 2,
      "name": "no_mem_used",
      "node": "n1",
      "status": "running",
      "vcpus": 2,
      "cpu_usage": 0.1,
      "mem_max": 4000000000,
      "mem_used": null
    },
    {
      "vmid": 3,
      "name": "bad_vcpus",
      "node": "n1",
      "status": "running",
      "vcpus": 0,
      "cpu_usage": 0.1,
      "mem_max": 4000000000,
      "mem_used": 1000000000
    },
    {
      "name": "no_vmid",
      "node": "n1",
      "status": "running",
      "vcpus": 2,
      "cpu_usage": 0.1,
      "mem_max": 4000000000,
      "mem_used": 1000000000
    }
  ]
}
```

```bash
python3 proxmox_balance.py partial.json; echo "exit code: $?"
```

```text
=== TẢI TỪNG NODE (TRƯỚC MIGRATE) ===
NODE           STATUS         CPU    CPU%                 MEM    MEM%  vCPU    OC  TÌNH TRẠNG
---------------------------------------------------------------------------------------------
n1             online       1.0/8   12.5%      2.00GB/16.00GB   12.5%     2 0.25x  ok

=== CẢNH BÁO DỮ LIỆU ===
  [!] vmid=2 (no_mem_used): mem_used=None, bỏ qua
  [!] vmid=3 (bad_vcpus): vcpus=0 không hợp lệ, bỏ qua
  [!] vms[3]: thiếu/không hợp lệ 'vmid', bỏ qua

=== KẾ HOẠCH MIGRATE ===
  Không cần migrate - cluster đã cân bằng.

=== TẢI SAU MIGRATE (dự kiến) ===
  n1: CPU 12.5% | RAM 12.5% | OC 0.25x
exit code: 0
```

**Case 4: `vmid` trùng trên hai node**: VM `vmid=7` xuất hiện ở cả `n1` và `n2`, tool chỉ tính một lần (`n1` có 2 vCPU, `n2` có 0)

`dup.json`:

```json
{
  "nodes": [
    {
      "node": "n1",
      "status": "online",
      "cpu_cores": 8,
      "mem_total": 16000000000
    },
    {
      "node": "n2",
      "status": "online",
      "cpu_cores": 8,
      "mem_total": 16000000000
    }
  ],
  "vms": [
    {
      "vmid": 7,
      "name": "web-a",
      "node": "n1",
      "status": "running",
      "vcpus": 2,
      "cpu_usage": 0.5,
      "mem_max": 4000000000,
      "mem_used": 2000000000
    },
    {
      "vmid": 7,
      "name": "web-a",
      "node": "n2",
      "status": "running",
      "vcpus": 2,
      "cpu_usage": 0.5,
      "mem_max": 4000000000,
      "mem_used": 2000000000
    }
  ]
}
```

```bash
python3 proxmox_balance.py dup.json; echo "exit code: $?"
```

```text
=== TẢI TỪNG NODE (TRƯỚC MIGRATE) ===
NODE           STATUS         CPU    CPU%                 MEM    MEM%  vCPU    OC  TÌNH TRẠNG
---------------------------------------------------------------------------------------------
n1             online       1.0/8   12.5%      2.00GB/16.00GB   12.5%     2 0.25x  ok
n2             online       0.0/8    0.0%      0.00GB/16.00GB    0.0%     0 0.00x  ok

=== CẢNH BÁO DỮ LIỆU ===
  [!] vmid=7 (web-a): xuất hiện nhiều lần (node n1 và n2), nội dung giống hệt; chỉ tính 1 lần theo bản ghi ở node n1, bỏ bản ghi ở node n2, không đề xuất migrate VM này

=== KẾ HOẠCH MIGRATE ===
  Không cần migrate - cluster đã cân bằng.

=== TẢI SAU MIGRATE (dự kiến) ===
  n1: CPU 12.5% | RAM 12.5% | OC 0.25x
  n2: CPU 0.0% | RAM 0.0% | OC 0.00x
exit code: 0
```

## Thuật toán migrate

Tool dùng **greedy worst-fit**:

1. Chạy `analyze` để đánh dấu node quá tải, **chụp lại trạng thái trước migrate**.
2. Sắp xếp node quá tải theo `mem_usage_pct` giảm dần (nặng nhất trước).
3. Với mỗi node quá tải, duyệt VM theo `mem_used` giảm dần, **bỏ qua VM trùng `vmid`** (vị trí chưa rõ).
4. Với mỗi VM, chọn các node đích thỏa **tất cả**: eligible (online, dữ liệu hợp lệ), không đang quá tải, và sau khi nhận VM vẫn thỏa cả 3 ngưỡng.
5. Trong các node đích hợp lệ, chọn node có `mem_usage_pct` **thấp nhất** (worst-fit, dàn đều tải), áp dụng migrate vào mô hình rồi tính lại trạng thái node nguồn.
6. Dừng khi node nguồn hết quá tải hoặc không còn VM nào chuyển được; node nào vẫn quá tải sẽ có cảnh báo riêng.

### Hạn chế đã biết

- Greedy không đảm bảo tối ưu toàn cục và ưu tiên VM RAM lớn nhất, không tính chi phí live-migrate (VM RAM lớn migrate lâu và chịu dirty page nhiều hơn). Với cluster nhỏ (<= 10 node) thì dễ hiểu và đủ dùng; nếu cần tối ưu hơn có thể dùng ILP / min-cost flow.
- Chỉ xét CPU, RAM và overcommit vCPU; chưa xét storage, network, affinity, hay cấu hình CPU type/tương thích giữa các node.
- Số liệu của một snapshot duy nhất; không có xu hướng theo thời gian (peak/trung bình).

- Khi `vmid` trùng, tool chọn bản ghi theo quy tắc cố định (xem giả định 11) nhưng **không thể biết VM thật đang nằm ở node nào**; người vận hành cần đối chiếu với Proxmox trước khi dựa vào số liệu của hai node liên quan.

## Cấu trúc thư mục

```text
phan2-tool/
├── proxmox_balance.py        # tool chính (585 dòng)
├── test_proxmox_balance.py   # 32 unit test (423 dòng)
├── generate_readme.py        # sinh README này bằng output thật
└── README.md                 # file này
```

## Hướng phát triển (ngoài phạm vi bài test)

- Đọc trực tiếp từ Proxmox API (`/api2/json/cluster/resources`) thay vì file JSON.
- Chạy định kỳ qua cron/systemd timer, gửi cảnh báo qua Alertmanager.
- Thêm ràng buộc anti-affinity (không đặt 2 VM cùng dịch vụ trên 1 node), có xét chi phí migrate.
- Tự suy luận dữ liệu sai đơn vị (hiện mới cảnh báo), tối ưu bằng ILP cho cluster lớn.
