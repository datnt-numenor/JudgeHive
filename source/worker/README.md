# Judge worker

Kết nối tới cổng worker của server (README gốc, mục "Giao tiếp" 2), nhận `JOB`, chấm trong sandbox và gửi
`PROGRESS` / `RESULT`. Chỉ dùng thư viện chuẩn của Python.

| File | Vai trò |
| --- | --- |
| `main.py` | Cấu hình, vòng kết nối (REGISTER, backoff), thread đọc (PING/PONG, nhận JOB), thread chấm |
| `executor.py` | Biên dịch, chạy từng test, phân loại verdict (D11, D13, D16), dọn sau mỗi test |
| `sandbox.py`, `launcher.py` | Chạy một chương trình có giới hạn qua launcher nhỏ (`posix_spawn` + `wait4`), kill, dọn theo uid |
| `languages.py` | Cấu hình `python` / `cpp` / `java`, hệ số thời gian, kiểm tra `MAX_TIME_FACTOR` lúc khởi động |
| `checker.py` | Chuẩn hóa và so output, cắt văn bản an toàn UTF-8, làm sạch thông báo CE |
| `spike/` | Mã đo của PR 2 (không import từ `worker/`) |

## Build và chạy

Context build là `source/` (cần cả `common/` và `worker/`):

```
docker build -f worker/Dockerfile --target runtime -t judgehive-worker .
docker build -f worker/Dockerfile --target test -t judgehive-worker-test .
```

Biến môi trường:

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `JH_SERVER_HOST` | `server` | Máy chạy server |
| `JH_WORKER_PORT` | `9000` | Cổng worker của server |
| `JH_WORKER_SECRET_FILE` | | File chứa shared secret (ưu tiên; mode 0400, chủ sở hữu root) |
| `JH_WORKER_SECRET` | | Secret trực tiếp (dùng khi không có file) |
| `JH_WORKER_NAME` | hostname | Tên hiển thị, tối đa 64 ký tự |
| `JH_WORK_ROOT` | `/work` | Thư mục làm việc; không được nằm trong `/tmp`, `/var/tmp`, `/dev/shm` |
| `JH_PEER_DEAD_AFTER_S` | `15` | Số giây không nhận được byte nào thì coi server đã mất |
| `JH_LOG_LEVEL` | `INFO` | Mức log (log không chứa secret, input, output, mã nguồn) |

## Cấu hình container cho compose (bàn giao cho `docker-compose.yml`)

Worker chạy bằng root trong container để hạ quyền con xuống uid 61000 (`judge`); code thí sinh không bao giờ chạy
bằng root. Cấu hình đã kiểm chứng ở PR 3 (lệnh test chuẩn dùng đúng các cờ này):

- `cap_drop: [ALL]`, `cap_add: [SETUID, SETGID, KILL, SYS_RESOURCE]` (`SYS_RESOURCE`: D28, xem bullet dưới), `security_opt: [no-new-privileges:true]`. Không cần
  `CHOWN`, `DAC_OVERRIDE`, `FOWNER`: file của uid thí sinh được xóa bằng một tiến trình chạy dưới chính uid đó.
  Thiếu `KILL` thì không kill được chương trình thí sinh khi TLE.
- **`cap_add` thêm `SYS_RESOURCE` và `sysctls` (D28):** `kernel.shm_rmid_forced: 1`, `kernel.shmmax: 0`. Worker chạy root đặt
  `oom_score_adj` +1000 cho tiến trình thí sinh (kể cả g++/javac) TRƯỚC `setpriv`; vì người ghi có `CAP_SYS_RESOURCE` nên
  kernel khóa sàn giá trị và thí sinh không hạ lại được (không cap thì hạ lại được: đã đo). Sau `setpriv --no-new-privs` thí
  sinh không có capability nào (`CapEff`/`CapPrm` bằng 0; test kiểm). Thiếu cap, sai sysctl, không nạp được bộ lọc, hoặc thí sinh vẫn hạ được `oom_score_adj` / gọi được `memfd_create` (thử bằng hành vi dưới uid thí sinh) thì `verify_guard()` làm worker thoát mã 2
  khi khởi động. Bộ lọc seccomp (`worker/seccomp.py`, nạp bằng `setpriv --seccomp-filter`) chỉ áp cho tiến trình thí sinh:
  `memfd_create`, `shmget`, `io_uring_setup/enter/register` trả `EPERM`; `socket()` chỉ cho `AF_UNIX`, mọi họ địa chỉ khác (kể cả `AF_INET`, `AF_INET6`) trả `EPERM`
  (D33: thí sinh không có mạng, kể cả loopback và DNS); ABI khác kiến trúc máy chủ (i386 `int 0x80`) và bit x32 thì `KILL_PROCESS`.
  Worker vẫn tự dùng `memfd` cho input test. Không đặt `RLIMIT_NPROC` (xem dưới).
- `pids_limit` (ví dụ 512): đây là biện pháp chặn fork bomb duy nhất (không đặt `RLIMIT_NPROC`). Khi bomb chiếm hết
  `pids_limit`, worker không tạo được thread hay tiến trình mới cho tới khi bomb bị kill; worker đã tránh việc này
  trong lúc chạy test (thread đọc và thread chấm tạo sẵn) và khôi phục sau khi kill. Kiểm chứng bằng
  `tests/worker/test_worker_container.py` (bomb thật làm cạn `pids_limit`: thread và tiến trình đều bị từ chối).
- `mem_limit`, `cpus`; `init: true` không cần vì image dùng `tini`.
- Nếu dùng `read_only: true`: mount `tmpfs` cho `/tmp`, `/dev/shm` và `/work`. **`/work` cần `exec`** (tmpfs của
  Docker mặc định là `noexec`, C++ sẽ không chạy được file đã biên dịch).
- **Mỗi tmpfs phải có trần `size=` và `nr_inodes=`** (D27). Tmpfs tính vào bộ nhớ của cgroup; không có trần thì
  chương trình thí sinh ghi file liên tục sẽ làm OOM killer giết cả worker (đã đo). Cấu hình compose:
  `/tmp:size=16m,nr_inodes=2048`, `/dev/shm:size=16m,nr_inodes=2048`, `/work:exec,mode=755,size=64m,nr_inodes=2048`
  (đã đo với đúng các giá trị này, D27), cùng `memswap_limit` bằng `mem_limit` để tmpfs không tràn sang swap.
  Nhu cầu thật của ba ngôn ngữ dưới 0,1 MiB.
- Input của test không nằm trên filesystem: worker ghi vào `memfd` (bộ nhớ ẩn danh, không tính vào tmpfs) rồi cho
  tiến trình thí sinh nhận làm stdin. Vì vậy `/work` đầy không làm hỏng việc ghi input của test kế tiếp. fd này
  là `CLOEXEC` ở mọi nơi trừ stdin của con; thí sinh chạy uid khác nên không mở được `/proc/<pid>/fd/*` của worker.
  `sandbox.run` mở `/proc/self/fd/N` trong worker (root), trước khi hạ uid; con chỉ nhận fd đã mở làm stdin. memfd
  mode 0400 nên con không mở lại `/proc/self/fd/0` được.
- **Giới hạn đã biết (D27 mục 5, chưa sửa):** trần tmpfs không chặn bộ nhớ ẩn danh do chính thí sinh tạo. Thí sinh gọi
  `memfd_create` nhiều lần và ghi 64 MiB mỗi cái (`RLIMIT_FSIZE`) thì OOM killer giết worker (đã đo: container thoát
  137). Xử lý ở PR riêng.
- Secret qua `JH_WORKER_SECRET_FILE`. `os.environ.pop` không xóa bản gốc trong `/proc/<pid>/environ`; bảo vệ thật là
  tiến trình thí sinh chạy uid khác nên không đọc được file đó (test đọc thử thật dưới uid thí sinh).

## Test

Một lệnh test chuẩn duy nhất cho cả `tests/invariants/` và `tests/worker/` (xem `CLAUDE.md`, "Lệnh môi trường"):
image test có `g++`, JDK 21, `setpriv`, `tini` và chạy với đúng cấu hình container ở trên.

## Dùng bởi test đã khóa — không đổi chữ ký

Các test worker được khóa (`tests/invariants/test_worker_contract_*.py`, tag `m2-tests-locked`) chỉ được dùng đúng
bề mặt dưới đây. Sau khi khóa, đổi tên, đổi tham số hay đổi kiểu trả về của những mục này là phá test khóa: code phải
giữ nguyên, hoặc dừng lại hỏi chủ repo. Mọi thứ khác trong `worker/` (kể cả `judge_run`, `RunResult`,
`checker.*`, `Backoff`, `languages.*`, `Grader`, hàm của `sandbox` ngoài hai mục dưới) tự do đổi; test của chúng ở
lại `tests/worker/`, không khóa.

| Mục | Hợp đồng test khóa dựa vào |
| --- | --- |
| `executor.parse_job(msg)` | Trả `Job`, test chỉ dùng `Job.tests` (dãy, `len` = số test); message sai thì raise `executor.BadJob` |
| `executor.grade(job, ctl, *, work_root)` | Trả dict body của `RESULT` (chưa có `type`, `submission_id`, `attempt_id`) khi `ctl` không có `deliver` |
| `ctl` (đối tượng truyền vào `grade`) | `ctl.progress(n)`: gọi với `tests_done = n`, trả `False` khi phải dừng; `ctl.cancel`: `threading.Event`; `ctl.deliver(body)` không bắt buộc |
| `main.Worker(cfg, *, grade_fn=executor.grade, sleep=None)` | `.run()` chạy vòng kết nối cho tới khi `.stop()`; `sleep(giây)` thay hàm chờ giữa hai lần kết nối lại; `grade_fn(job, ctl, **kwargs)` thay `executor.grade` |
| `main.Config` (chỉ các field này) | `host`, `port`, `secret`, `name`, `work_root`, `backoff_base_s`, `backoff_cap_s`, `jitter` (khoảng chờ kết nối lại 1, 2, 4, ... trần 10 giây khi `jitter = 0`) |
| `sandbox.uid_pids(uid)` | Trả danh sách PID (không zombie) của `uid` |
| `sandbox.JUDGE_UID` | uid chạy code thí sinh |

Ngoài `worker/`, test khóa còn dùng `common.protocol` (`encode`, `LineDecoder`, `Message`, `PROTOCOL_VERSION`,
`MAX_CODE_BYTES`, `MAX_LINE_SERVER_TO_WORKER`), vốn đã khóa ở `m1-tests-locked`. Helper (`MockServer`,
`FakeCtl`, `grade`, `make_job`) nằm trong test, không thuộc bề mặt này.

## Hạn chế đã biết

- Code thí sinh vẫn đọc được các file hệ thống ai cũng đọc được (`/etc`, `/usr`); `/app` (mã worker) và secret
  thì không. Mạng không bị chặn (xem PLAN mục 4).
- `memory_kib` có sàn khoảng 9 MB (RSS của launcher Python); Python/C++ dưới `RLIMIT_AS` và Java dưới `-Xmx`.
- Hệ số Python 5 không bù đủ cho bài thiên về CPU; giới hạn thời gian của đề phải đủ cho lời giải Python chuẩn.
- Chương trình thí sinh chạy với `chrt --idle 0` (SCHED_IDLE) để fork bomb không làm chính worker đói CPU (TLE mất 33 giây
  dưới `--cpus=2` nếu không có). Đánh đổi: khi máy có tải tranh CPU, wall time của thí sinh dài hơn (đo ở PR 3: Java median
  798 ms so với 492 ms khi không đặt gì; `nice -n 19` ở giữa: 531 ms), nên có thể TLE oan. Chưa chốt; xem `STATE.md`.
- `RLIMIT_CORE` đặt 1 chứ không phải 0: khi `core_pattern` là một pipe (WSL2, apport), giá trị 0 vẫn để kernel đẩy
  core dump qua pipe và làm `abort()` với RSS lớn mất vài giây.
