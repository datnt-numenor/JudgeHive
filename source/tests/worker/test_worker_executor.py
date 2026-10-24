"""Executor với toolchain thật: bộ bài nộp phá hoại (PLAN mục 8) cho ba ngôn ngữ, qua launcher + sandbox."""

import dataclasses
import os
import resource
import subprocess
import time

import pytest

from _submissions import CE, SUB
from _worker_helpers import FakeCtl, case, grade, make_job
from worker import executor, sandbox
from worker.languages import LANGUAGES

LANGS = ["python", "cpp", "java"]
UID = sandbox.JUDGE_UID
SUM_TESTS = [case("1 2\n", "3\n", True), case("10 20\n", "30\n")]
RE_KINDS = {"EXIT_NONZERO", "SIGSEGV", "SIGFPE", "SIGABRT", "SIGKILL", "OTHER_SIGNAL"}


def no_leftovers():
    assert sandbox.uid_pids(UID) == []


# ------------------------------------------------------------------------------------------- AC / WA

def test_time_and_memory_are_maxima_over_all_tests_not_the_last(work_root):
    tests = [case("0.1", "ok\n"), case("0.8", "ok\n"), case("0.1", "ok\n")]
    body, _ = grade(work_root, "python", SUB["sleep_input"]["python"], tests)
    assert body["verdict"] == "AC" and body["time_ms"] >= 800
    tests = [case("1", "ok\n"), case("60", "ok\n"), case("1", "ok\n")]
    body, _ = grade(work_root, "python", SUB["alloc_mb"]["python"], tests)
    assert body["verdict"] == "AC" and body["memory_kib"] >= 60 * 1024


# ----------------------------------------------------------------------------------------------- TLE

@pytest.mark.parametrize("lang", LANGS)
def test_tle_then_worker_can_grade_next_job(work_root, lang):
    limit_ms = 300
    body, _ = grade(work_root, lang, SUB["loop"][lang], [case("1 2\n", "3\n", True)], time_limit_ms=limit_ms)
    assert body["verdict"] == "TLE" and body["failed_test"] == 1
    assert body["time_ms"] >= limit_ms * LANGUAGES[lang].time_factor  # TLE: >= giới hạn đã nhân hệ số
    no_leftovers()
    body, _ = grade(work_root, lang, SUB["ac"][lang], SUM_TESTS)
    assert body["verdict"] == "AC"


def test_python_time_factor_5_lets_slow_program_pass_where_cpp_factor_1_would_not(work_root):
    body, _ = grade(work_root, "python", SUB["sleep_input"]["python"], [case("2.5", "ok\n")], time_limit_ms=1000)
    assert body["verdict"] == "AC" and body["time_ms"] >= 2500
    body, _ = grade(work_root, "python", SUB["sleep_input"]["python"], [case("6", "ok\n")], time_limit_ms=1000)
    assert body["verdict"] == "TLE" and body["time_ms"] >= 5000


# ------------------------------------------------------------------------------------------- MLE / RE

@pytest.mark.parametrize("case_name", ["big_block", "grow"])
@pytest.mark.parametrize("lang", LANGS)
def test_memory_hogs_are_mle_not_re(work_root, lang, case_name):
    body, _ = grade(work_root, lang, SUB[case_name][lang], [case("1 2\n", "3\n")], time_limit_ms=5000)
    assert body["verdict"] == "MLE", body
    assert body["failed_test"] == 1 and "time_ms" in body and "memory_kib" in body
    no_leftovers()


@pytest.mark.parametrize("lang", LANGS)
def test_signature_pushed_out_of_the_stderr_window_is_re(work_root, lang):
    body, _ = grade(work_root, lang, SUB["fake_oom_flood"][lang], [case("1 2\n", "3\n")])
    assert body["verdict"] == "RE"


@pytest.mark.parametrize("lang,kind", [("python", "EXIT_NONZERO"), ("cpp", "SIGFPE"), ("java", "EXIT_NONZERO")])
def test_division_by_zero_is_re(work_root, lang, kind):
    body, _ = grade(work_root, lang, SUB["div0"][lang], [case("1 0\n", "x\n")])
    assert body["verdict"] == "RE" and body["re_kind"] == kind
    assert "time_ms" not in body and "memory_kib" not in body


# --------------------------------------------------------------------------------------------------- CE

@pytest.mark.parametrize("lang", ["cpp", "java"])
def test_compile_error(work_root, lang):
    body, progress = grade(work_root, lang, CE[lang], [case("", "")])
    assert body["verdict"] == "CE" and progress == []  # CE gửi thẳng RESULT, không có tests_done = 0
    msg = body["ce_message"]
    assert LANGUAGES[lang].source_file in msg
    assert work_root not in msg and "job-" not in msg
    assert len(msg.encode()) <= 4096 and msg.count("\n") < 50


def test_compile_timeout_is_ce(work_root):
    slow = dataclasses.replace(LANGUAGES["cpp"], compile_timeout_s=0.01)
    job = executor.parse_job(make_job("cpp", SUB["ac"]["cpp"], SUM_TESTS), {"cpp": slow})
    body = executor.grade(job, FakeCtl(), work_root=work_root)
    assert body["verdict"] == "CE" and "timed out" in body["ce_message"]


# ------------------------------------------------------------------------------------ output, tiếng Việt

@pytest.mark.parametrize("lang", LANGS)
def test_huge_output_is_capped_wa_and_worker_memory_stays_bounded(work_root, lang):
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.monotonic()
    body, _ = grade(work_root, lang, SUB["huge_output"][lang], [case("", "x\n", True)], time_limit_ms=10000)
    assert body["verdict"] == "WA"
    assert time.monotonic() - started < 40
    grown_mib = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 1024
    assert grown_mib < 150, f"worker phình {grown_mib:.0f} MiB"
    assert len(body["sample_stdout"].encode()) <= 4096
    no_leftovers()


# ------------------------------------------------------------------------------------------- an toàn

def test_fork_bomb_is_stopped_and_next_job_still_runs(work_root):
    for lang in ("python", "cpp"):
        body, _ = grade(work_root, lang, SUB["fork_bomb"][lang], [case("", "x\n")], time_limit_ms=1000)
        assert body["verdict"] == "TLE"
        no_leftovers()
        body, _ = grade(work_root, lang, SUB["ac"][lang], SUM_TESTS)
        assert body["verdict"] == "AC"


@pytest.mark.parametrize("lang", ["python", "cpp"])
def test_setsid_escapee_survives_killpg_but_not_uid_kill(work_root, lang):
    # trực tiếp ở sandbox: killpg không với tới tiến trình đã setsid(), kill theo uid thì dọn được
    wd = work_root + "/direct"
    import os
    os.mkdir(wd, 0o1777)
    os.chmod(wd, 0o1777)
    src = "Main.py" if lang == "python" else "Main.cpp"
    with open(f"{wd}/{src}", "w") as f:
        f.write(SUB["setsid_ok"][lang])
    argv = ["python3", src]
    if lang == "cpp":
        compiled = sandbox.run(["g++", "-O2", "-o", "main", src], cwd=wd, timeout_s=30)
        assert compiled.exit_code == 0
        argv = ["./main"]
    res = sandbox.run(argv, cwd=wd, timeout_s=10)
    assert res.exit_code == 0
    assert len(sandbox.uid_pids(UID)) == 1, "tiến trình thoát nhóm phải còn sống sau killpg"
    sandbox.kill_uid(UID)
    assert sandbox.uid_pids(UID) == []


@pytest.mark.parametrize("lang", ["python", "cpp"])
def test_executor_leaves_no_escaped_process_after_job(work_root, lang):
    body, _ = grade(work_root, lang, SUB["setsid_ok"][lang], SUM_TESTS)
    assert body["verdict"] == "AC"
    no_leftovers()


def test_files_in_tmp_do_not_leak_between_submissions(work_root):
    first, _ = grade(work_root, "python", SUB["tmp_write"]["python"], [case("HIDDEN-DATA\n", "ok\n")])
    assert first["verdict"] == "AC"
    assert sandbox.uid_entries(UID) == 0
    second, _ = grade(work_root, "python", SUB["tmp_read"]["python"], [case("", "x\n", True)])
    assert second["sample_stdout"].splitlines() == ["/tmp/jh-leak.txt missing", "/dev/shm/jh-leak.txt missing",
                                                      "/tmp/jh-dir missing"]


def test_candidate_cannot_write_or_delete_outside_its_dir(work_root):
    body, _ = grade(work_root, "python", SUB["fs_attack"]["python"], [case("", "x\n", True)])
    assert body["sample_stdout"].splitlines() == ["denied"] * 4


def test_cancel_stops_running_program_quickly_and_cleans_up(work_root):
    ctl = FakeCtl(cancel_after_progress=0)  # hủy ngay sau PROGRESS tests_done = 0
    started = time.monotonic()
    body, progress = grade(work_root, "python", SUB["loop"]["python"], [case("", "x\n")], time_limit_ms=60000,
                           ctl=ctl)
    assert body is None and progress == [0]
    assert time.monotonic() - started < 5
    no_leftovers()


def test_cancel_during_run_kills_program(work_root):
    import threading
    ctl = FakeCtl()
    threading.Timer(0.7, ctl.cancel.set).start()
    started = time.monotonic()
    body, _ = grade(work_root, "python", SUB["loop"]["python"], [case("", "x\n")], time_limit_ms=60000, ctl=ctl)
    assert body is None and time.monotonic() - started < 6
    no_leftovers()
    assert sandbox.uid_entries(UID) == 0


def test_result_is_delivered_before_cleanup_and_progress_before_the_cleanup_between_tests(work_root, monkeypatch):
    """D16: thời gian dọn (kill, xóa file) không được ăn vào hạn chót của server. Test sai: RESULT đi trước mọi
    việc dọn; test đúng: PROGRESS(i) đi trước việc dọn giữa các test; test cuối: RESULT đi trước việc dọn cuối."""
    events = []
    real = sandbox.cleanup_uid_files

    def spy(*args, **kwargs):
        events.append("cleanup")
        return real(*args, **kwargs)

    monkeypatch.setattr(sandbox, "cleanup_uid_files", spy)

    class RecordingCtl(FakeCtl):
        def progress(self, n):
            events.append(f"progress:{n}")
            return super().progress(n)

        def deliver(self, body):
            events.append(f"deliver:{body['verdict']}")

    body, _ = grade(work_root, "python", SUB["ac"]["python"], [case("1 2", "4")], ctl=RecordingCtl())
    assert body is None and events[:2] == ["progress:0", "deliver:WA"] and events[2:] == ["cleanup"]
    events.clear()
    body, _ = grade(work_root, "python", SUB["ac"]["python"], [case("1 2", "3"), case("2 2", "4")], ctl=RecordingCtl())
    assert body is None
    assert events == ["progress:0", "progress:1", "cleanup", "progress:2", "deliver:AC", "cleanup"]


def test_kill_uid_reports_whether_the_uid_is_clean():
    assert sandbox.kill_uid(UID) is True  # không có gì để kill
    holder = subprocess.Popen(["setpriv", "--reuid=61000", "--regid=61000", "--clear-groups", "sleep", "60"])
    deadline = time.monotonic() + 5
    while not sandbox.uid_pids(UID) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert sandbox.uid_pids(UID)
    assert sandbox.kill_uid(UID) is True and sandbox.uid_pids(UID) == []
    holder.wait()


def test_spawn_is_retried_when_pids_are_briefly_exhausted(work_root, monkeypatch):
    calls = {"n": 0}
    real = subprocess.Popen

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise BlockingIOError(11, "Resource temporarily unavailable")
        return real(*args, **kwargs)

    monkeypatch.setattr(sandbox.subprocess, "Popen", flaky)
    body, _ = grade(work_root, "python", SUB["ac"]["python"], [case("1 2", "3")])
    assert body["verdict"] == "AC" and calls["n"] >= 3


def test_cleanup_between_tests_is_bounded_by_its_budget(work_root, monkeypatch):
    """D16: việc dọn giữa các test nằm trong cửa sổ watchdog của test kế tiếp nên phải có chặn trên."""
    kills, cleans = [], []
    monkeypatch.setattr(sandbox, "kill_uid", lambda uid, timeout_s=2.0: kills.append(timeout_s) or True)
    monkeypatch.setattr(sandbox, "cleanup_uid_files",
                        lambda uid, roots=(), timeout_s=5: cleans.append(timeout_s) or True)
    body, _ = grade(work_root, "python", SUB["ac"]["python"], [case("1 2", "3"), case("2 2", "4")])
    assert body["verdict"] == "AC"
    assert len(kills) == 2 and len(cleans) == 2  # một lần giữa hai test, một lần cuối bài (sau RESULT)
    assert kills[0] <= 1.5 and cleans[0] <= 1.5  # giữa các test: có chặn trên
    assert kills[1] > kills[0]  # dọn cuối bài rộng rãi hơn vì RESULT đã đi


def test_surviving_candidate_process_between_tests_is_se(work_root, monkeypatch):
    monkeypatch.setattr(sandbox, "kill_uid", lambda uid, timeout_s=2.0: False)
    body, _ = grade(work_root, "python", SUB["ac"]["python"], [case("1 2", "3"), case("2 2", "4")])
    assert body["verdict"] == "SE"
