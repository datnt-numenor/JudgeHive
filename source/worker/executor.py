"""Chấm một `JOB`: biên dịch, chạy từng test, phân loại verdict (README mục RESULT, PLAN D11, D13, D16)."""

import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass

from common import protocol

from . import checker, sandbox
from .languages import LANGUAGES

log = logging.getLogger("worker.executor")

OUTPUT_CAP = 16 * 1024 * 1024  # trần stdout của chương trình thí sinh (PLAN mục 4); vượt thì kill và xếp WA
STDERR_TAIL = 2048  # D13: chỉ đọc một đoạn cuối stderr cố định
COMPILE_CAPTURE = 64 * 1024  # đọc tối đa chừng này byte đầu của thông báo biên dịch rồi mới cắt (D11)
COMPILE_OUTPUT_CAP = 1024 * 1024
LAUNCH_ALLOWANCE_S = 0.75  # hạn điều phối = giới hạn đã nhân hệ số + chừng này; TLE xét theo wall của chính chương trình
RSS_MLE_RATIO = 0.9  # D13

RE_SIGNALS = {11: "SIGSEGV", 8: "SIGFPE", 6: "SIGABRT", 9: "SIGKILL"}


class BadJob(Exception):
    """`JOB` sai định dạng: worker đóng kết nối rồi kết nối lại (D16)."""


class ExecutorError(Exception):
    """Lỗi của chính máy chấm: verdict `SE`."""


@dataclass(frozen=True)
class Case:
    input: str
    output: str
    is_sample: bool


@dataclass(frozen=True)
class Job:
    submission_id: int
    attempt_id: int
    language: object
    code: str
    time_limit_ms: int
    memory_limit_kib: int
    tests: tuple


def _int(msg, key):
    value = msg.get(key)
    if type(value) is not int:
        raise BadJob(f"{key} thiếu hoặc không phải số nguyên")
    return value


def parse_job(msg, languages=LANGUAGES):
    """Kiểm tra field của `JOB` (README mục JOB). Trả Job, hoặc BadJob."""
    if msg.get("type") != "JOB":
        raise BadJob("không phải JOB")
    language = languages.get(msg.get("language")) if isinstance(msg.get("language"), str) else None
    if language is None:
        raise BadJob("language không hợp lệ")
    code = msg.get("code")
    if not isinstance(code, str) or len(code.encode("utf-8", errors="surrogatepass")) > protocol.MAX_CODE_BYTES:
        raise BadJob("code thiếu, không phải chuỗi hoặc quá dài")
    time_limit_ms, memory_limit_kib = _int(msg, "time_limit_ms"), _int(msg, "memory_limit_kib")
    if time_limit_ms < 1 or memory_limit_kib < 1:
        raise BadJob("giới hạn phải dương")
    raw_tests = msg.get("tests")
    if not isinstance(raw_tests, list) or not raw_tests:
        raise BadJob("tests thiếu hoặc rỗng")
    tests = []
    for t in raw_tests:
        if (not isinstance(t, dict) or not isinstance(t.get("input"), str) or not isinstance(t.get("output"), str)
                or type(t.get("is_sample")) is not bool):
            raise BadJob("phần tử của tests sai định dạng")
        tests.append(Case(t["input"], t["output"], t["is_sample"]))
    return Job(_int(msg, "submission_id"), _int(msg, "attempt_id"), language, code, time_limit_ms,
               memory_limit_kib, tuple(tests))


def re_kind(res):
    """Loại lỗi thô gửi client (D11); không bao giờ gửi giá trị mã thoát cụ thể."""
    if res.signal is None:
        return "EXIT_NONZERO"
    return RE_SIGNALS.get(res.signal, "OTHER_SIGNAL")


def is_oom(language, res, memory_limit_kib):
    """D13: chữ ký OOM trong đoạn cuối stderr; Python/C++ thêm vế RSS đỉnh ≥ 90% giới hạn (Java thì không)."""
    if language.oom_signature.encode() in res.stderr_tail:
        return True
    return language.rss_rule and res.maxrss_kib >= RSS_MLE_RATIO * memory_limit_kib


def judge_run(language, res, expected, memory_limit_kib, scaled_limit_ms):
    """Verdict của một lần chạy: (verdict, re_kind). Thứ tự: TLE, output vượt trần, MLE/RE, so output."""
    if res.spawn_error:
        raise ExecutorError(res.spawn_error)
    if res.timed_out or res.wall_ms > scaled_limit_ms:
        return "TLE", None
    if res.output_capped:
        return "WA", None
    if res.abnormal:
        if is_oom(language, res, memory_limit_kib):
            return "MLE", None
        return "RE", re_kind(res)
    return ("AC" if checker.outputs_match(res.stdout, expected) else "WA"), None


class _Workdir:
    """Thư mục tạm của một bài: root sở hữu, sticky 1777 (uid thí sinh tạo được file của nó, root xóa được mục
    cấp một mà không cần cap đặc biệt); input test nằm ngoài thư mục này, root-only, truyền vào bằng fd."""

    def __init__(self, work_root, uid):
        os.makedirs(work_root, mode=0o755, exist_ok=True)
        self.root, self.uid = work_root, uid
        self.path = tempfile.mkdtemp(prefix="job-", dir=work_root)
        os.chmod(self.path, 0o1777)
        self._input = None

    def write_source(self, name, text):
        path = os.path.join(self.path, name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.chmod(path, 0o444)

    def write_input(self, text):
        self.drop_input()
        fd, self._input = tempfile.mkstemp(prefix=".input-", dir=self.root)
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8", errors="surrogatepass"))
        return self._input

    def drop_input(self):
        if self._input:
            try:
                os.unlink(self._input)
            except OSError:
                pass
            self._input = None

    def reset_candidate(self, roots=sandbox.TMP_ROOTS, budget_s=1.5):
        """Kill mọi tiến trình của uid thí sinh rồi xóa file của nó ở /tmp, /var/tmp, /dev/shm, trong `budget_s` giây.
        Giữa các test, thời gian này nằm trong cửa sổ watchdog của test kế tiếp (D16) nên phải có chặn trên; việc dọn
        triệt để làm ở `remove()` sau khi RESULT đã đi. Không xóa thư mục tạm của bài giữa chừng vì file thực thi do
        uid thí sinh biên dịch nằm ở đó. Trả (không còn tiến trình, dọn file xong)."""
        self.drop_input()
        started = time.monotonic()
        clean = sandbox.kill_uid(self.uid, timeout_s=budget_s)
        files_ok = sandbox.cleanup_uid_files(self.uid, roots, timeout_s=max(0.3, budget_s - (time.monotonic() - started)))
        if not (clean and files_ok):
            log.warning("chưa dọn sạch uid thí sinh (tiến trình sạch=%s, file sạch=%s)", clean, files_ok)
        return clean, files_ok

    def remove(self):
        self.reset_candidate((self.path, *sandbox.TMP_ROOTS), budget_s=10)
        shutil.rmtree(self.path, ignore_errors=True)
        if os.path.exists(self.path):
            log.warning("không xóa được thư mục tạm của bài")


def _compile(job, wd, uid, cancel):
    """Biên dịch. Trả None nếu ổn, hoặc (None, True) khi bị hủy, hoặc body CE."""
    lang = job.language
    res = sandbox.run(list(lang.compile_cmd), cwd=wd.path, timeout_s=lang.compile_timeout_s,
                      limits=lang.compile_limits(), uid=uid, stdout_cap=COMPILE_OUTPUT_CAP,
                      stdout_keep=COMPILE_CAPTURE, stderr_head=COMPILE_CAPTURE, cancel=cancel)
    if res.cancelled:
        return "cancelled"
    if res.spawn_error:
        raise ExecutorError(res.spawn_error)
    if res.timed_out:
        return {"verdict": "CE", "ce_message": "Compilation timed out"}
    if res.output_capped or res.abnormal:
        raw = res.stderr_head + b"\n" + res.stdout
        message = checker.sanitize_ce(raw, wd.path, lang.source_file) or "Compilation failed"
        return {"verdict": "CE", "ce_message": message}
    if lang.name == "cpp" and not os.path.exists(os.path.join(wd.path, "main")):
        raise ExecutorError("trình biên dịch không tạo file thực thi")
    return None


def grade(job, ctl, *, work_root, uid=sandbox.JUDGE_UID):
    """Chấm `job`. `ctl.cancel` (Event) hủy bài; `ctl.progress(n)` gửi PROGRESS và trả False khi kết nối đã mất.

    Body của RESULT (verdict và các field, chưa có type/submission_id/attempt_id) được giao cho `ctl.deliver(body)`
    NGAY khi có, trước khi dọn dẹp nặng (kill, xóa file): thời gian dọn không được ăn vào hạn chót của server (D16).
    Nếu `ctl` không có `deliver` thì trả body. Trả None khi bị hủy hoặc đã giao."""
    try:
        wd = _Workdir(work_root, uid)
    except OSError as e:
        return _deliver_or_return(ctl, {"verdict": "SE", "se_detail": f"không tạo được thư mục tạm: {type(e).__name__}"})
    try:
        return _deliver_or_return(ctl, _guarded(job, ctl, wd, uid))
    finally:
        wd.remove()


def _deliver_or_return(ctl, body):
    deliver = getattr(ctl, "deliver", None)
    if body is not None and deliver is not None:
        deliver(body)
        return None
    return body


def _guarded(job, ctl, wd, uid):
    try:
        return _grade(job, ctl, wd, uid)
    except ExecutorError as e:
        log.error("SE bài %s: %s", job.submission_id, e)
        return {"verdict": "SE", "se_detail": str(e)[:200]}
    except Exception as e:  # lỗi lập trình trong worker: SE, không đổ lỗi cho thí sinh
        log.exception("lỗi không lường trước khi chấm bài %s", job.submission_id)
        return {"verdict": "SE", "se_detail": type(e).__name__}  # chỉ tên kiểu: thông điệp có thể chứa dữ liệu bài


def _grade(job, ctl, wd, uid):
    lang = job.language
    wd.write_source(lang.source_file, job.code)
    if lang.compile_cmd:
        outcome = _compile(job, wd, uid, ctl.cancel)
        if outcome == "cancelled":
            return None
        if outcome is not None:
            return outcome
    if not ctl.progress(0):
        return None

    scaled_ms = job.time_limit_ms * lang.time_factor
    argv = lang.run_cmd(job.memory_limit_kib)
    limits = lang.run_limits(job.memory_limit_kib)
    max_ms = max_kib = 0
    for i, test in enumerate(job.tests, 1):
        stdin_path = wd.write_input(test.input)
        res = sandbox.run(argv, cwd=wd.path, timeout_s=scaled_ms / 1000 + LAUNCH_ALLOWANCE_S, limits=limits,
                          uid=uid, stdin_path=stdin_path, stdout_cap=OUTPUT_CAP, stdout_keep=OUTPUT_CAP,
                          stderr_tail=STDERR_TAIL, cancel=ctl.cancel)
        if res.cancelled:
            return None
        verdict, kind = judge_run(lang, res, test.output, job.memory_limit_kib, scaled_ms)
        max_ms = max(max_ms, round(res.wall_ms))
        max_kib = max(max_kib, res.maxrss_kib)
        if verdict != "AC":
            return _failure(verdict, kind, i, test, res, max_ms, max_kib, scaled_ms)
        if not ctl.progress(i):
            return None
        if i < len(job.tests):
            # sau khi đã báo PROGRESS (test sai thì RESULT đi trước, dọn ở wd.remove()); tiến trình thí sinh còn sống
            # sang test sau thì không chấm tiếp được một cách tin cậy
            if not wd.reset_candidate()[0]:
                raise ExecutorError("không kill hết được tiến trình của uid thí sinh sau test")
    return {"verdict": "AC", "time_ms": max_ms, "memory_kib": max_kib}


def _failure(verdict, kind, index, test, res, max_ms, max_kib, scaled_ms):
    body = {"verdict": verdict, "failed_test": index, "failed_is_sample": test.is_sample}
    if verdict in ("WA", "TLE", "MLE"):
        body["time_ms"] = max(max_ms, scaled_ms) if verdict == "TLE" else max_ms
        body["memory_kib"] = max_kib
    if verdict == "RE":
        body["re_kind"] = kind
    if test.is_sample:
        body["sample_stdout"] = checker.cut_text(res.stdout, checker.SAMPLE_STDOUT_MAX_BYTES)
    return body
