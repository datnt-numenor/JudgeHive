"""Executor với toolchain thật: bộ bài nộp phá hoại (PLAN mục 8) cho ba ngôn ngữ, qua `executor.grade`."""

import json
import os
import subprocess

import pytest

from _worker_contract_helpers import FakeCtl, case, grade, make_job
from _worker_contract_submissions import CE, CE_LONG_CPP, CE_VIETNAMESE_CPP, SUB
from worker import executor, sandbox

LANGS = ["python", "cpp", "java"]
UID = sandbox.JUDGE_UID
SUM_TESTS = [case("1 2\n", "3\n", True), case("10 20\n", "30\n")]
SOURCE_FILE = {"cpp": "Main.cpp", "java": "Main.java"}  # tên file trong thông báo CE (README mục RESULT)


def no_leftovers():
    assert sandbox.uid_pids(UID) == []


def emit(output: bytes):
    """Chương trình Python chỉ in đúng các byte `output` ra stdout."""
    return f"import sys\nsys.stdout.buffer.write({output!r})\n"


@pytest.mark.parametrize("actual,expected", [
    (b"3\n", "3\n"),
    (b"3", "3\n"),
    (b"3\n\n\n", "3\n"),
    (b"3   \n", "3\n"),
    (b"3\t\n", "3"),
    (b"1 2\r\n3 4\r\n", "1 2\n3 4\n"),
    (b"a\n\n\nb\n", "a\n\n\nb"),
    (b"", ""),
    (b"\n\n", ""),
    ("Xin chào\n".encode(), "Xin chào\n"),
])
def test_outputs_that_differ_only_in_trailing_whitespace_are_ac(work_root, actual, expected):
    body, _ = grade(work_root, "python", emit(actual), [case("", expected)])
    assert body["verdict"] == "AC", body


@pytest.mark.parametrize("actual,expected", [
    (b"4\n", "3\n"),
    (b"3 \n4\n", "3\n4 4\n"),
    (b"a\nb\n", "a\n\nb\n"),  # dòng trống ở giữa vẫn là khác biệt
    (b" 3\n", "3\n"),  # khoảng trắng đầu dòng không được bỏ
    (b"3\n", "3\n3\n"),
    (b"", "0\n"),
])
def test_outputs_that_differ_are_wa(work_root, actual, expected):
    body, _ = grade(work_root, "python", emit(actual), [case("", expected)])
    assert body["verdict"] == "WA", body


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


def test_time_and_memory_include_the_failing_test(work_root):
    body, _ = grade(work_root, "python", SUB["alloc_mb"]["python"], [case("1", "ok\n"), case("60", "WRONG\n")])
    assert body["verdict"] == "WA" and body["memory_kib"] >= 60 * 1024


@pytest.mark.parametrize("lang", LANGS)
def test_tle_then_worker_can_grade_next_job(work_root, lang):
    limit_ms = 300
    body, _ = grade(work_root, lang, SUB["loop"][lang], [case("1 2\n", "3\n", True)], time_limit_ms=limit_ms)
    assert body["verdict"] == "TLE" and body["failed_test"] == 1
    assert body["time_ms"] >= limit_ms  # README: TLE có time_ms >= giới hạn đã nhân hệ số (hệ số >= 1)
    no_leftovers()
    body, _ = grade(work_root, lang, SUB["ac"][lang], SUM_TESTS)
    assert body["verdict"] == "AC"


@pytest.mark.parametrize("lang", LANGS)
def test_memory_hog_is_mle_not_re(work_root, lang):
    body, _ = grade(work_root, lang, SUB["big_block"][lang], [case("1 2\n", "3\n")], time_limit_ms=5000)
    assert body["verdict"] == "MLE", body
    assert body["failed_test"] == 1 and "time_ms" in body and "memory_kib" in body
    no_leftovers()


@pytest.mark.parametrize("lang", ["python", "cpp"])
def test_using_the_whole_memory_limit_is_mle_and_using_half_is_re(work_root, lang):
    """Chỉ hai ca rõ (giới hạn 256 MiB): chạm 100% rồi lỗi thì MLE, chạm 50% rồi lỗi thì RE. Ngưỡng sát 90% không khóa."""
    mle, _ = grade(work_root, lang, SUB["touch_then_fail"][lang], [case("256\n", "ok\n")], time_limit_ms=5000)
    assert mle["verdict"] == "MLE", mle
    re, _ = grade(work_root, lang, SUB["touch_then_fail"][lang], [case("128\n", "ok\n")], time_limit_ms=5000)
    assert re["verdict"] == "RE" and re["re_kind"] == "EXIT_NONZERO", re


@pytest.mark.parametrize("lang", LANGS)
def test_fake_oom_signature_is_mle_or_re_without_leaking_data(work_root, lang):
    body, _ = grade(work_root, lang, SUB["fake_oom"][lang], [case("secret-input-123\n", "3\n")])
    assert body["verdict"] in ("MLE", "RE")
    assert "secret-input-123" not in json.dumps(body)


@pytest.mark.parametrize("lang", ["python", "java"])
def test_division_by_zero_is_re(work_root, lang):
    body, _ = grade(work_root, lang, SUB["div0"][lang], [case("1 0\n", "x\n")])
    assert body["verdict"] == "RE" and body["re_kind"] == "EXIT_NONZERO"
    assert "time_ms" not in body and "memory_kib" not in body


@pytest.mark.parametrize("lang", ["python", "cpp"])
def test_segfault_reports_signal_kind(work_root, lang):
    body, _ = grade(work_root, lang, SUB["segv"][lang], [case("", "x\n")])
    assert body["verdict"] == "RE" and body["re_kind"] == "SIGSEGV"


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


@pytest.mark.parametrize("lang", ["cpp", "java"])
def test_compile_error(work_root, lang):
    body, progress = grade(work_root, lang, CE[lang], [case("", "")])
    assert body["verdict"] == "CE" and progress == []  # CE gửi thẳng RESULT, không có tests_done = 0
    msg = body["ce_message"]
    assert SOURCE_FILE[lang] in msg
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


def test_unwritable_work_root_is_se_without_leaking_details():
    job = executor.parse_job(make_job("python", SUB["ac"]["python"], [case("secret-input-9\n", "3\n")]))
    body = executor.grade(job, FakeCtl(), work_root="/proc/nope/work")
    assert body["verdict"] == "SE" and "secret-input-9" not in json.dumps(body)


@pytest.mark.parametrize("lang", LANGS)
def test_vietnamese_source_and_output(work_root, lang):
    body, _ = grade(work_root, lang, SUB["vietnamese"][lang], [case("", "Xin chào Việt Nam\n")])
    assert body["verdict"] == "AC", body


def test_java_with_many_standard_libraries_runs_under_the_nofile_limit(work_root):
    """Collections, streams, regex, BufferedReader và thread pool chạy được dưới giới hạn số file đang mở."""
    text = "the quick brown fox 12 jumps over the lazy dog 30\nthe end 8\n"
    expected = "words=9 digits=50 top=the=3,brown=1,dog=1 first=the total=102018000\n"
    body, _ = grade(work_root, "java", SUB["java_libs"]["java"], [case(text, expected, True)], time_limit_ms=5000)
    assert body["verdict"] == "AC", body


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


def test_candidate_cannot_write_or_delete_outside_its_dir(work_root):
    body, _ = grade(work_root, "python", SUB["fs_attack"]["python"], [case("", "x\n", True)])
    assert "ALLOWED" not in body["sample_stdout"].splitlines()


def test_work_dir_is_removed_after_job(work_root):
    grade(work_root, "python", SUB["ac"]["python"], SUM_TESTS)
    grade(work_root, "cpp", SUB["ac"]["cpp"], SUM_TESTS)
    assert os.listdir(work_root) == []


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
