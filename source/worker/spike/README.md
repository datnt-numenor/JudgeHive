# Spike executor (PR 2)

Lấy số liệu để chốt D13 (phân loại MLE/RE) và các hằng số D16. Đây không phải executor hoàn chỉnh: mã ở đây
chỉ phục vụ đo, không import `common/` hay `server/`, và chỉ dùng thư viện chuẩn của Python.

## Chạy (chỉ cần Docker, từ thư mục `source/`)

```
docker compose -f worker/spike/compose.yml up --build
```

Một lệnh chạy toàn bộ, tuần tự (khoảng 15–25 phút, chưa kiểm chứng trên máy khác):

1. `main`: biên dịch, tự kiểm, ma trận D13, overhead, bài chuẩn, kill/fork bomb, ngưỡng rlimit.
2. `cold3`: container mới, 3 biên dịch song song ngay lần đầu.
3. `s1`–`s4`: hai container cùng uid, một bên fork bomb, một bên chạy bài bình thường (xem dưới).
4. `report`: gộp thành `summary.md`.

Kết quả nằm ở `worker/spike/results/<thời điểm>-<host>/` (`summary.md` + các file `*.json` thô). Trên máy
Linux, file kết quả thuộc root vì container chạy bằng root. Chạy lại trên máy khác rồi so `meta` trong JSON.
Chạy nhanh để thử: `docker run ... judgehive-spike:trixie-jdk21 --suite main --quick`, hoặc một suite riêng
(`--suite d13|compile|selfcheck|overhead|bench|kill|floors`).

Cấu hình container (ghi trong `meta`): `cpus: 1`, `mem_limit: 1g`, `pids_limit: 512`, `init: true`.
Base image ghim theo bản Debian: `python:3.11-slim-trixie` (Debian 13) + `openjdk-21-jdk-headless`.

## Các thành phần

| File | Vai trò |
| --- | --- |
| `runner.py` | Spawn + đọc stdout/stderr có trần + `os.wait4` + `killpg`. Ba cách đặt giới hạn: `launcher` (mặc định), `wrap`, `preexec` |
| `launcher.py` | Tiến trình cha nhỏ: `posix_spawn`, `wait4`, báo rusage của chính chương trình qua một fd |
| `programs.py` | Mã nguồn các chương trình thử (python / cpp / java) |
| `suites.py` | Các bộ đo |
| `report.py` | Gộp JSON thành `summary.md` và đề xuất theo quy tắc ghi trước |
| `spike.py` | CLI |

## Quy tắc đề xuất (ghi trước khi đo)

- `COMPILE_TIMEOUT_MAX` = 2 × thời gian biên dịch tệ nhất trong các kịch bản lần đầu/song song, làm tròn lên giây.
- `PER_TEST_OVERHEAD` = 2 × (wall tệ nhất khi in 16 MiB + compare tệ nhất), làm tròn lên 0,5 giây.
- Hệ số thời gian theo ngôn ngữ và `MAX_TIME_FACTOR`: không đề xuất, chỉ báo tỷ lệ so với C++.
- D13: bảng ma trận đối chiếu luật chốt tạm (chữ ký OOM HOẶC RSS ≥ 90% giới hạn) với nhãn mong đợi.

## Hạn chế đã biết

- Không có kết quả "cold" thật sự của máy: page cache của host vẫn còn sau `docker build`.
- Thời gian đo bị ảnh hưởng bởi tải máy; cần chạy trên ít nhất hai máy.
- Kịch bản cô lập uid phụ thuộc vào việc các container chia sẻ một kernel và không dùng user namespace
  (Docker Desktop/WSL2 và Docker Engine mặc định đều vậy).
- Cài đặt `normalize` + so sánh ở `suites.py` là bản tham chiếu của spike, không phải mã worker thật.
