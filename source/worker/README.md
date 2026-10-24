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

- `cap_drop: [ALL]`, `cap_add: [SETUID, SETGID, KILL]`, `security_opt: [no-new-privileges:true]`. Không cần
  `CHOWN`, `DAC_OVERRIDE`, `FOWNER`: file của uid thí sinh được xóa bằng một tiến trình chạy dưới chính uid đó.
  Thiếu `KILL` thì không kill được chương trình thí sinh khi TLE.
- `pids_limit` (ví dụ 512): đây là biện pháp chặn fork bomb duy nhất (không đặt `RLIMIT_NPROC`). Khi bomb chiếm hết
  `pids_limit`, worker không tạo được thread hay tiến trình mới cho tới khi bomb bị kill; worker đã tránh việc này
  trong lúc chạy test (thread đọc và thread chấm tạo sẵn) và khôi phục sau khi kill. Kiểm chứng bằng
  `tests/worker/test_worker_container.py` (bomb thật làm cạn `pids_limit`: thread và tiến trình đều bị từ chối).
- `mem_limit`, `cpus`; `init: true` không cần vì image dùng `tini`.
- Nếu dùng `read_only: true`: mount `tmpfs` cho `/tmp`, `/dev/shm` và `/work`. **`/work` cần `exec`** (tmpfs của
  Docker mặc định là `noexec`, C++ sẽ không chạy được file đã biên dịch): ví dụ `tmpfs: ["/work:exec,mode=755"]`.
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
