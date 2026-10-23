"""Executor với toolchain thật: bộ bài nộp phá hoại (PLAN mục 8) cho ba ngôn ngữ, qua launcher + sandbox."""

import dataclasses
import json
import os
import resource
import subprocess
import time

import pytest

from _submissions import CE, CE_LONG_CPP, CE_VIETNAMESE_CPP, SUB
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

@pytest.mark.parametrize("lang", LANGS)
def test_ac(work_root, lang):
    body, progress = grade(work_root, lang, SUB["ac"][lang], SUM_TESTS)
    assert body["verdict"] == "AC"
    assert set(body) == {"verdict", "time_ms", "memory_kib"}
    assert isinstance(body["time_ms"], int) and body["memory_kib"] > 0
    assert progress == [0, 1, 2]  # tests_done = 0 bắt buộc, rồi một PROGRESS sau mỗi test
    no_leftovers()


@pytest.mark.parametrize("lang", LANGS)
def test_trailing_whitespace_and_blank_lines_still_ac(work_root, lang):
    body, _ = grade(work_root, lang, SUB["ws"][lang], SUM_TESTS)
    assert body["verdict"] == "AC"


def test_crlf_output_is_ac(work_root):
    body, _ = grade(work_root, "python", SUB["crlf"]["python"], SUM_TESTS)
    assert body["verdict"] == "AC"


@pytest.mark.parametrize("lang", LANGS)
def test_wa_reports_first_failing_hidden_test_without_sample_stdout(work_root, lang):
    tests = [case("1 2\n", "3\n", True), case("10 20\n", "31\n"), case("5 5\n", "10\n")]
    body, progress = grade(work_root, lang, SUB["ac"][lang], tests)
    assert body["verdict"] == "WA"
    assert body["failed_test"] == 2 and body["failed_is_sample"] is False
    assert "sample_stdout" not in body
    assert progress == [0, 1]  # test sai gửi thẳng RESULT, không có PROGRESS cho nó; test 3 không chạy


def test_wa_on_sample_returns_program_stdout_and_stops_before_hidden_tests(work_root):
    tests = [case("1 2\n", "4\n", True), case("10 20\n", "30\n")]
    body, progress = grade(work_root, "python", SUB["ac"]["python"], tests)
    assert body["verdict"] == "WA" and body["failed_test"] == 1 and body["failed_is_sample"] is True
    assert body["sample_stdout"] == "3\n"
    assert progress == [0]


def test_sample_stdout_is_cut_at_4kb_on_utf8_boundary(work_root):
    body, _ = grade(work_root, "python", SUB["print_big_vietnamese"]["python"], [case("", "x\n", True)])
    assert body["verdict"] == "WA"
    assert len(body["sample_stdout"].encode()) <= 4096
    assert body["sample_stdout"].startswith("ệ")


def test_time_and_memory_are_maxima_over_all_tests_not_the_last(work_root):
    tests = [case("0.1", "ok\n"), case("0.8", "ok\n"), case("0.1", "ok\n")]
    body, _ = grade(work_root, "python", SUB["sleep_input"]["python"], tests)
    assert body["verdict"] == "AC" and body["time_ms"] >= 800
    tests = [case("1", "ok\n"), case("60", "ok\n"), case("1", "ok\n")]
    body, _ = grade(work_root, "python", SUB["alloc_mb"]["python"], tests)
    assert body["verdict"] == "AC" and body["memory_kib"] >= 60 * 1024


def test_time_and_memory_include_the_failing_test(work_root):
    body, _ = grade(work_root, "python", SUB["alloc_mb"]["python"], [case("1", "ok\n"), case("60", "WRONG\n")])
    assert body["verdict"] == "WA" and body["memory_kib"] >= 60 * 1024


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
def test_fake_oom_signature_is_mle_or_re_without_leaking_data(work_root, lang):
    body, _ = grade(work_root, lang, SUB["fake_oom"][lang], [case("secret-input-123\n", "3\n")])
    assert body["verdict"] in ("MLE", "RE")
    assert "secret-input-123" not in json.dumps(body)


@pytest.mark.parametrize("lang", LANGS)
def test_signature_pushed_out_of_the_stderr_window_is_re(work_root, lang):
    body, _ = grade(work_root, lang, SUB["fake_oom_flood"][lang], [case("1 2\n", "3\n")])
    assert body["verdict"] == "RE"


@pytest.mark.parametrize("lang,kind", [("python", "EXIT_NONZERO"), ("cpp", "SIGFPE"), ("java", "EXIT_NONZERO")])
def test_division_by_zero_is_re(work_root, lang, kind):
    body, _ = grade(work_root, lang, SUB["div0"][lang], [case("1 0\n", "x\n")])
    assert body["verdict"] == "RE" and body["re_kind"] == kind
    assert "time_ms" not in body and "memory_kib" not in body


@pytest.mark.parametrize("lang,kind", [("python", "SIGSEGV"), ("cpp", "SIGSEGV")])
def test_segfault_reports_signal_kind(work_root, lang, kind):
    body, _ = grade(work_root, lang, SUB["segv"][lang], [case("", "x\n")])
    assert body["verdict"] == "RE" and body["re_kind"] == kind


@pytest.mark.parametrize("lang,kind", [("python", "EXIT_NONZERO"), ("cpp", "SIGABRT"), ("java", "EXIT_NONZERO")])
def test_out_of_bounds_is_re(work_root, lang, kind):
    body, _ = grade(work_root, lang, SUB["oob"][lang], [case("", "x\n")])
    assert body["verdict"] == "RE" and body["re_kind"] == kind


@pytest.mark.parametrize("lang", LANGS)
def test_exit_code_value_never_reaches_the_client(work_root, lang):
    body, _ = grade(work_root, lang, SUB["exit_byte"][lang], [case("A", "")])  # exit(65)
    assert body["verdict"] == "RE" and body["re_kind"] == "EXIT_NONZERO"
    text = json.dumps(body)
    assert "65" not in text and "exit_code" not in text and set(body) <= {
        "verdict", "failed_test", "failed_is_sample", "re_kind"}


# --------------------------------------------------------------------------------------------------- CE

@pytest.mark.parametrize("lang", ["cpp", "java"])
def test_compile_error(work_root, lang):
    body, progress = grade(work_root, lang, CE[lang], [case("", "")])
    assert body["verdict"] == "CE" and progress == []  # CE gửi thẳng RESULT, không có tests_done = 0
    msg = body["ce_message"]
    assert LANGUAGES[lang].source_file in msg
    assert work_root not in msg and "job-" not in msg
    assert len(msg.encode()) <= 4096 and msg.count("\n") < 50


def test_long_compile_error_is_cut_to_50_lines(work_root):
    body, _ = grade(work_root, "cpp", CE_LONG_CPP, [case("", "")])
    assert body["verdict"] == "CE"
    assert body["ce_message"].count("\n") <= 49 and len(body["ce_message"].encode()) <= 4096
    assert work_root not in body["ce_message"]


def test_vietnamese_compile_error_is_valid_utf8_and_cut_at_4kb(work_root):
    body, _ = grade(work_root, "cpp", CE_VIETNAMESE_CPP, [case("", "")])
    assert body["verdict"] == "CE"
    msg = body["ce_message"]
    assert len(msg.encode()) <= 4096 and msg.startswith("Main.cpp:1")
    json.dumps(body, ensure_ascii=False).encode("utf-8")  # không vỡ JSON


def test_compile_timeout_is_ce(work_root):
    slow = dataclasses.replace(LANGUAGES["cpp"], compile_timeout_s=0.01)
    job = executor.parse_job(make_job("cpp", SUB["ac"]["cpp"], SUM_TESTS), {"cpp": slow})
    body = executor.grade(job, FakeCtl(), work_root=work_root)
    assert body["verdict"] == "CE" and "timed out" in body["ce_message"]


def test_unwritable_work_root_is_se_without_leaking_details(work_root):
    job = executor.parse_job(make_job("python", SUB["ac"]["python"], [case("secret-input-9\n", "3\n")]))
    body = executor.grade(job, FakeCtl(), work_root="/proc/nope/work")
    assert body["verdict"] == "SE" and "secret-input-9" not in json.dumps(body)


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


@pytest.mark.parametrize("lang", LANGS)
def test_vietnamese_source_and_output(work_root, lang):
    body, _ = grade(work_root, lang, SUB["vietnamese"][lang], [case("", "Xin chào Việt Nam\n")])
    assert body["verdict"] == "AC", body


def test_java_with_many_standard_libraries_runs_under_the_nofile_limit(work_root):
    """Collections, streams, regex, BufferedReader và thread pool dưới RLIMIT_NOFILE của bài chạy (32)."""
    assert LANGUAGES["java"].run_limits(262144)["nofile"] == 32
    text = "the quick brown fox 12 jumps over the lazy dog 30\nthe end 8\n"
    expected = "words=9 digits=50 top=the=3,brown=1,dog=1 first=the total=102018000\n"
    body, _ = grade(work_root, "java", SUB["java_libs"]["java"], [case(text, expected, True)], time_limit_ms=5000)
    assert body["verdict"] == "AC", body


# ------------------------------------------------------------------------------------------- an toàn

@pytest.mark.parametrize("lang", LANGS)
def test_hidden_test_data_never_appears_in_result(work_root, lang):
    marker = "HIDDEN-INPUT-MARKER-7731"
    body, _ = grade(work_root, lang, SUB["echo_streams"][lang], [case(marker + "\n", "x\n", False)])
    assert body["verdict"] == "RE"
    text = json.dumps(body)
    assert marker not in text and "sample_stdout" not in body


def test_sample_test_returns_only_stdout_never_stderr(work_root):
    marker = "SAMPLE-MARKER-5512"
    body, _ = grade(work_root, "python", 'import sys\nprint("out-line")\nsys.stderr.write("' + marker + '")\n',
                    [case("", "different\n", True)])
    assert body["sample_stdout"] == "out-line\n" and marker not in json.dumps(body)


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


def test_work_dir_is_removed_after_job(work_root):
    import os
    grade(work_root, "python", SUB["ac"]["python"], SUM_TESTS)
    grade(work_root, "cpp", SUB["ac"]["cpp"], SUM_TESTS)
    assert os.listdir(work_root) == []


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


def test_candidate_cannot_read_environment_of_root_processes_or_root_only_files(work_root):
    """Không phụ thuộc tên biến môi trường: một tiến trình root mang secret giả trong environ và một file root-only
    0400; chương trình thí sinh (uid khác) đọc cả hai phải bị từ chối."""
    canary = "CANARY-5e3a91"
    secret_file = os.path.join(work_root, "secret-file")
    with open(secret_file, "w") as f:
        f.write(canary)
    os.chmod(secret_file, 0o400)
    holder = subprocess.Popen(["sleep", "60"], env={"PATH": os.environ["PATH"], "ANY_NAME": canary})
    code = f'''
import os
for path in ("/proc/1/environ", "/proc/{holder.pid}/environ", {secret_file!r}):
    try:
        data = open(path, "rb").read()
        print(path, "READ", "LEAK" if {canary!r}.encode() in data else "noleak")
    except Exception as e:
        print(path, type(e).__name__)
print("env", "LEAK" if any({canary!r} in v for v in os.environ.values()) else "clean")
'''
    try:
        body, _ = grade(work_root, "python", code, [case("", "different", True)])
    finally:
        holder.kill()
        holder.wait()
    out = body["sample_stdout"]
    assert canary not in out and "LEAK" not in out, out
    assert "/proc/1/environ PermissionError" in out
    assert f"/proc/{holder.pid}/environ PermissionError" in out
    assert f"{secret_file} PermissionError" in out
    assert "env clean" in out
