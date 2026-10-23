# Kết quả spike executor (PR 2)

Thư mục: `20261023T070621Z-bdba35532b3f`; parts: cold3, iso-s1-bomb, iso-s1-victim, iso-s2-bomb, iso-s2-victim, iso-s3-bomb, iso-s3-victim, iso-s4-bomb, iso-s4-victim, main

## Môi trường

| Trường | Giá trị |
| --- | --- |
| utc | 2026-10-23T07:11:58Z |
| hostname | bdba35532b3f |
| os_release | Debian GNU/Linux 13 (trixie) |
| kernel | 6.18.33.2-microsoft-standard-WSL2 |
| cpu_model | 13th Gen Intel(R) Core(TM) i5-13500H |
| cpu_count | 16 |
| cgroup_cpu_max | 100000 100000 |
| cgroup_memory_max | 1073741824 |
| cgroup_pids_max | 512 |
| python | 3.11.17 |
| gxx | g++ (Debian 14.2.0-19) 14.2.0 |
| javac | javac 21.0.12.1 |
| java | openjdk version "21.0.12.1" 2026-08-18 |
| limit_kib | 262144 |
| stderr_tail_bytes | 2048 |

## Tự kiểm (rusage riêng từng lần, dọn nhóm sau killpg)

| Kiểm tra | Kết quả | Chi tiết |
| --- | --- | --- |
| rusage_separation | PASS | {'a_maxrss_kib': 212772, 'b_maxrss_kib': 9244, 'children_maxrss_kib': 236600} |
| killpg_cleanup | PASS | {'procs_at_kill': 4, 'leftover': 0, 'clear_ms': 2.64322600014566} |

`ru_maxrss` (KiB) của hello-world theo cách spawn (nếu `preexec` ≫ `wrap` hoặc ≫ ~1000 KiB cho C++ thì ru_maxrss bị phình bởi RSS của tiến trình điều phối trước `exec`).
RSS của tiến trình điều phối: VmRSS = 22292 KiB, VmHWM = 22292 KiB.

| Ngôn ngữ | Cách spawn | median | max |
| --- | --- | --- | --- |
| cpp | launcher | 9164 | 9284 |
| cpp | preexec | 18288 | 18288 |
| cpp | wrap | 21980 | 21980 |
| java | launcher | 37672 | 38080 |
| java | preexec | 37996 | 38112 |
| java | wrap | 37840 | 38004 |
| python | launcher | 9124 | 9172 |
| python | preexec | 18288 | 18288 |
| python | wrap | 21852 | 21852 |

## D13: ma trận MLE/RE

Giới hạn 262144 KiB. `sig` = thoát bất thường VÀ cuối stderr có chữ ký OOM; `rss` = thoát bất thường VÀ `ru_maxrss` ≥ 90% giới hạn; `gộp` = sig HOẶC rss (luật chốt tạm của PLAN D13). Kỳ vọng: khối lớn/tăng dần = MLE, chia 0 = RE, chữ ký giả = MLE hoặc RE.

| ngôn ngữ | biến thể | ca | n | exit | signal | maxrss KiB (median) | cuối stderr | đầu stdout | sig | rss | gộp | kỳ vọng | khớp |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cpp | as256 | big_block | 5 | None×5 | 6×5 | 9284 | "ter throwing an instance of 'std::bad_alloc'⏎  what():  std::bad_alloc" | '' | MLE×5 | RE×5 | MLE×5 | MLE | ok |
| cpp | as256 | div0 | 5 | None×5 | 8×5 | 9220 | '' | '' | RE×5 | RE×5 | RE×5 | RE | ok |
| cpp | as256 | fake_oom | 5 | 1×5 | None×5 | 9220 | 'std::bad_alloc' | '' | MLE×5 | RE×5 | MLE×5 | MLE\|RE | ok |
| cpp | as256 | fake_oom_flood | 5 | 1×5 | None×5 | 9304 | 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' | '' | RE×5 | RE×5 | RE×5 | MLE\|RE | ok |
| cpp | as256 | grow | 5 | None×5 | 6×5 | 257532 | "ter throwing an instance of 'std::bad_alloc'⏎  what():  std::bad_alloc" | '' | MLE×5 | MLE×5 | MLE×5 | MLE | ok |
| java | as256_xmx256 | big_block | 5 | 1×5 | None×5 | 14328 | '' | 'Error occurred during initialization of VM\nCould not reserve' | RE×5 | RE×5 | RE×5 | MLE | **0/5** |
| java | as256_xmx256 | div0 | 5 | 1×5 | None×5 | 14316 | '' | 'Error occurred during initialization of VM\nCould not reserve' | RE×5 | RE×5 | RE×5 | RE | ok |
| java | as256_xmx256 | fake_oom | 5 | 1×5 | None×5 | 14132 | '' | 'Error occurred during initialization of VM\nCould not reserve' | RE×5 | RE×5 | RE×5 | MLE\|RE | ok |
| java | as256_xmx256 | fake_oom_flood | 5 | 1×5 | None×5 | 14360 | '' | 'Error occurred during initialization of VM\nCould not reserve' | RE×5 | RE×5 | RE×5 | MLE\|RE | ok |
| java | as256_xmx256 | grow | 5 | 1×5 | None×5 | 14092 | '' | 'Error occurred during initialization of VM\nCould not reserve' | RE×5 | RE×5 | RE×5 | MLE | **0/5** |
| java | xmx256 | big_block | 5 | 1×5 | None×5 | 38848 | 'java.lang.OutOfMemoryError: Java heap space⏎\tat Main.main(Main.java:3)' | '' | MLE×5 | RE×5 | MLE×5 | MLE | ok |
| java | xmx256 | div0 | 5 | 1×5 | None×5 | 40940 | 'n" java.lang.ArithmeticException: / by zero⏎\tat Main.main(Main.java:5)' | '' | RE×5 | RE×5 | RE×5 | RE | ok |
| java | xmx256 | fake_oom | 5 | 1×5 | None×5 | 39572 | 'java.lang.OutOfMemoryError' | '' | MLE×5 | RE×5 | MLE×5 | MLE\|RE | ok |
| java | xmx256 | fake_oom_flood | 5 | 1×5 | None×5 | 41984 | 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' | '' | RE×5 | RE×5 | RE×5 | MLE\|RE | ok |
| java | xmx256 | grow | 5 | 1×5 | None×5 | 287804 | 'java.lang.OutOfMemoryError: Java heap space⏎\tat Main.main(Main.java:6)' | '' | MLE×5 | MLE×5 | MLE×5 | MLE | ok |
| java | xmx256_exitoom | big_block | 5 | 3×5 | None×5 | 38360 | '' | 'Terminating due to java.lang.OutOfMemoryError: Java heap spa' | RE×5 | RE×5 | RE×5 | MLE | **0/5** |
| java | xmx256_exitoom | div0 | 5 | 1×5 | None×5 | 40692 | 'n" java.lang.ArithmeticException: / by zero⏎\tat Main.main(Main.java:5)' | '' | RE×5 | RE×5 | RE×5 | RE | ok |
| java | xmx256_exitoom | fake_oom | 5 | 1×5 | None×5 | 39580 | 'java.lang.OutOfMemoryError' | '' | MLE×5 | RE×5 | MLE×5 | MLE\|RE | ok |
| java | xmx256_exitoom | fake_oom_flood | 5 | 1×5 | None×5 | 41980 | 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' | '' | RE×5 | RE×5 | RE×5 | MLE\|RE | ok |
| java | xmx256_exitoom | grow | 5 | 3×5 | None×5 | 286676 | '' | 'Terminating due to java.lang.OutOfMemoryError: Java heap spa' | RE×5 | MLE×5 | MLE×5 | MLE | ok |
| python | as256 | big_block | 5 | 1×5 | None×5 | 9244 | 'ule>⏎    x = bytearray(4 << 30)⏎        ^^^^^^^^^^^^^^^^^^⏎MemoryError' | '' | MLE×5 | RE×5 | MLE×5 | MLE | ok |
| python | as256 | div0 | 5 | 1×5 | None×5 | 9284 | '          ~~^^~~⏎ZeroDivisionError: integer division or modulo by zero' | '' | RE×5 | RE×5 | RE×5 | RE | ok |
| python | as256 | fake_oom | 5 | 1×5 | None×5 | 9220 | 'MemoryError' | '' | MLE×5 | RE×5 | MLE×5 | MLE\|RE | ok |
| python | as256 | fake_oom_flood | 5 | 1×5 | None×5 | 9860 | 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' | '' | RE×5 | RE×5 | RE×5 | MLE\|RE | ok |
| python | as256 | grow | 5 | 1×5 | None×5 | 254068 | 'ule>⏎    b = bytearray(8 << 20)⏎        ^^^^^^^^^^^^^^^^^^⏎MemoryError' | '' | MLE×5 | MLE×5 | MLE×5 | MLE | ok |

Ô luật gộp không khớp kỳ vọng: java/as256_xmx256/big_block/0/5, java/as256_xmx256/grow/0/5, java/xmx256_exitoom/big_block/0/5

## Thời gian biên dịch (giây)

`cold_solo`: lần đầu trong container, một mình. `cold_parallel3`: container mới, 3 biên dịch song song ngay lần đầu. `warm_solo`: các lần sau. `warm_parallel3`: 3 song song, đã ấm.

| ngôn ngữ | chương trình | kịch bản | n | min | median | p95 | max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cpp | hello | cold_parallel3 | 3 | 0.09 | 0.09 | 0.10 | 0.10 |
| cpp | hello | cold_solo | 1 | 0.04 | 0.04 | 0.04 | 0.04 |
| cpp | hello | warm_solo | 10 | 0.04 | 0.04 | 0.07 | 0.07 |
| cpp | medium | warm_parallel3 | 15 | 5.98 | 6.79 | 8.39 | 8.39 |
| cpp | medium | warm_solo | 10 | 1.75 | 2.33 | 3.43 | 3.43 |
| java | hello | cold_parallel3 | 3 | 2.34 | 2.34 | 2.45 | 2.45 |
| java | hello | cold_solo | 1 | 0.67 | 0.67 | 0.67 | 0.67 |
| java | hello | warm_solo | 10 | 0.73 | 0.81 | 0.98 | 0.98 |
| java | medium | warm_parallel3 | 15 | 4.18 | 4.48 | 6.19 | 6.19 |
| java | medium | warm_solo | 10 | 1.01 | 1.15 | 1.59 | 1.59 |

## Overhead mỗi test

Khởi động hello-world (ms, wall từ spawn đến wait4):

| ngôn ngữ | biến thể | n | median | p95 | max | maxrss KiB (median) |
| --- | --- | --- | --- | --- | --- | --- |
| cpp | default | 20 | 2 | 3 | 3 | 9244 |
| java | default | 20 | 21 | 39 | 40 | 37846 |
| java | tuned | 20 | 21 | 34 | 34 | 36330 |
| python | default | 20 | 9 | 10 | 18 | 9224 |

In 16 MiB: wall gồm chạy chương trình và đọc có trần; `compare` là chuẩn hóa + so với đáp án (cài đặt tham chiếu của spike, ms):

| ngôn ngữ | n | wall median | wall max | compare median | compare max | đúng |
| --- | --- | --- | --- | --- | --- | --- |
| cpp | 5 | 13 | 22 | 121 | 129 | ok |
| java | 5 | 72 | 107 | 122 | 150 | ok |
| python | 5 | 34 | 46 | 127 | 141 | ok |

## Bài chuẩn: tỷ lệ so với C++ (không đề xuất hệ số: đó là chính sách)

**Tổng 10^6 số (I/O + parse)**

| ngôn ngữ | n | wall median ms | wall p95 ms | CPU median ms | tỷ lệ median/C++ | tỷ lệ p95/C++ median | maxrss KiB | đúng |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cpp | 10 | 33 | 37 | 31 | 1.0 | 1.1 | 9208 | ok |
| java | 10 | 208 | 310 | 211 | 6.3 | 9.3 | 84598 | ok |
| python | 10 | 211 | 243 | 202 | 6.3 | 7.3 | 88078 | ok |

**Vòng lặp lồng 3000×3000 (CPU-bound)**

| ngôn ngữ | n | wall median ms | wall p95 ms | CPU median ms | tỷ lệ median/C++ | tỷ lệ p95/C++ median | maxrss KiB | đúng |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cpp | 10 | 32 | 35 | 31 | 1.0 | 1.1 | 9224 | ok |
| java | 10 | 58 | 72 | 57 | 1.8 | 2.2 | 38998 | ok |
| python | 10 | 691 | 749 | 687 | 21.6 | 23.4 | 9246 | ok |

## killpg khi TLE, fork bomb, thoát nhóm

**spawn_sleep** (timeout 2 s): số tiến trình trong nhóm lúc kill, còn sót sau killpg, thời gian nhóm sạch:

| ngôn ngữ | n | TLE | tiến trình lúc kill (max) | sót sau killpg (max) | nhóm sạch sau ms (max) |
| --- | --- | --- | --- | --- | --- |
| cpp | 3 | 3 | 4 | 0 | 2.5 |
| java | 3 | 3 | 4 | 0 | 2.7 |
| python | 3 | 3 | 4 | 0 | 2.7 |

**fork_bomb** (timeout 3 s; Java là thread bomb): `EAGAIN` thấy trong stderr nghĩa là RLIMIT_NPROC chặn được:

| ngôn ngữ | NPROC | n | TLE | tiến trình lúc kill (max) | thread lúc kill (max) | thấy EAGAIN | sót sau killpg (max) | uid còn tiến trình sau killpg (max) | exit | sạch sau ms (max) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cpp | nproc128 | 3 | 3 | 129 | 129 | 3 | 0 | 0 | None×3 | 99.1 |
| cpp | nproc32 | 3 | 3 | 33 | 33 | 3 | 0 | 0 | None×3 | 97.8 |
| cpp | nproc64 | 3 | 3 | 65 | 65 | 3 | 0 | 0 | None×3 | 101.0 |
| java | nproc128 | 3 | 3 | 2 | 129 | 3 | 0 | 0 | None×3 | 0.5 |
| java | nproc32 | 3 | 3 | 2 | 33 | 3 | 0 | 0 | None×3 | 0.3 |
| java | nproc64 | 3 | 3 | 2 | 65 | 3 | 0 | 0 | None×3 | 95.1 |
| python | nproc128 | 3 | 3 | 129 | 129 | 3 | 0 | 0 | None×3 | 102.7 |
| python | nproc32 | 3 | 3 | 33 | 33 | 3 | 0 | 0 | None×3 | 98.1 |
| python | nproc64 | 3 | 3 | 65 | 65 | 3 | 0 | 0 | None×3 | 101.3 |

**setsid_escape**: tiến trình con thoát nhóm; killpg không với tới, kill theo uid thì dọn được:

| ngôn ngữ | TLE | còn sống sau killpg | số tiến trình kill theo uid | còn sau kill theo uid |
| --- | --- | --- | --- | --- |
| python | True | 1 | 1 | 0 |
| cpp | True | 1 | 1 | 0 |
| java | True | 1 | 1 | 0 |

## Ngưỡng tối thiểu để chương trình hello khởi động được

| giới hạn | ngôn ngữ | nhỏ nhất đạt (mọi lần) | pass theo giá trị |
| --- | --- | --- | --- |
| nofile | cpp | 8 | 8:2/2 16:2/2 32:2/2 64:2/2 128:2/2 256:2/2 1024:2/2 |
| nofile | java | 8 | 8:2/2 16:2/2 32:2/2 64:2/2 128:2/2 256:2/2 1024:2/2 |
| nofile | python | 8 | 8:2/2 16:2/2 32:2/2 64:2/2 128:2/2 256:2/2 1024:2/2 |
| nproc | cpp | 8 | 8:2/2 16:2/2 32:2/2 64:2/2 128:2/2 256:2/2 |
| nproc | java | 16 | 8:0/2 16:2/2 32:2/2 64:2/2 128:2/2 256:2/2 |
| nproc | python | 8 | 8:2/2 16:2/2 32:2/2 64:2/2 128:2/2 256:2/2 |

## Hai container cùng uid: fork bomb có ảnh hưởng bài bình thường không

| kịch bản | cấu hình | ngôn ngữ | lỗi/lần trước bomb | lỗi/lần trong bomb | lỗi/lần sau bomb |
| --- | --- | --- | --- | --- | --- |
| s1 | victim uid 61000 NPROC 64, pids_limit 512; bomb uid 61000 NPROC 64, pids_limit 512 | java | 0/79 | 56/56 | 0/48 |
| s1 |  | python | 0/79 | 0/56 | 0/48 |
| s1 |  | cpp | 0/79 | 0/56 | 0/47 |
| s2 | victim uid 61000 NPROC 64, pids_limit 512; bomb uid 61001 NPROC 64, pids_limit 512 | java | 0/77 | 0/59 | 0/47 |
| s2 |  | python | 0/77 | 0/59 | 0/46 |
| s2 |  | cpp | 0/77 | 0/58 | 0/47 |
| s3 | victim uid 61000 NPROC không, pids_limit 512; bomb uid 61000 NPROC không, pids_limit 128 | java | 0/77 | 0/75 | 0/25 |
| s3 |  | python | 0/77 | 0/74 | 0/25 |
| s3 |  | cpp | 0/77 | 0/74 | 0/25 |
| s4 | victim uid 61000 NPROC 64, pids_limit 512; bomb uid 61000 NPROC không, pids_limit 128 | java | 0/79 | 68/68 | 0/34 |
| s4 |  | python | 0/79 | 0/68 | 0/33 |
| s4 |  | cpp | 0/78 | 0/69 | 0/33 |

- `s1`: bomb thấy EAGAIN, 65 tiến trình lúc kill; victim lỗi trong cửa sổ: **56**; ví dụ lỗi: exit 1, spawn_error None, stderr ''
- `s2`: bomb thấy EAGAIN, 65 tiến trình lúc kill; victim lỗi trong cửa sổ: **0**
- `s3`: bomb thấy EAGAIN, 126 tiến trình lúc kill; victim lỗi trong cửa sổ: **0**
- `s4`: bomb thấy EAGAIN, 126 tiến trình lúc kill; victim lỗi trong cửa sổ: **68**; ví dụ lỗi: exit 1, spawn_error None, stderr ''

## Đề xuất theo quy tắc ghi trước (chỉ đề xuất, chưa quyết)

- **COMPILE_TIMEOUT_MAX** = 2 × thời gian biên dịch tệ nhất trong các kịch bản lần đầu/song song (8.39 s, cpp/medium/warm_parallel3) làm tròn lên giây = **17 s** (giá trị hiện tại trong README: 10 s).
- **PER_TEST_OVERHEAD** = 2 × (wall tệ nhất khi in 16 MiB + compare tệ nhất) = 2 × 257 ms, làm tròn lên 0,5 s = **1 s** (n nhỏ nên p99 ≈ max; khởi động tệ nhất 40 ms; giá trị hiện tại: 3 s).
- **Hệ số thời gian / MAX_TIME_FACTOR**: không đề xuất; xem bảng tỷ lệ so với C++ ở trên.
- **D13**: xem bảng ma trận và danh sách ô luật gộp không khớp ở mục D13.
