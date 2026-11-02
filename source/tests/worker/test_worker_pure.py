"""Test thuần (không cần toolchain): checker, languages, phân loại verdict, parse JOB, config."""

import math
import random

import pytest

from common import protocol
from worker import checker, executor, languages, sandbox
from worker.languages import LANGUAGES, ConfigError, validate
from worker.main import Backoff, ConfigError as MainConfigError, load_config
from worker.sandbox import RunResult


# ---------------------------------------------------------------------------------------------- checker

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
def test_outputs_match(actual, expected):
    assert checker.outputs_match(actual, expected)


@pytest.mark.parametrize("actual,expected", [
    (b"4\n", "3\n"),
    (b"3 \n4\n", "3\n4 4\n"),
    (b"a\nb\n", "a\n\nb\n"),   # dòng trống ở giữa vẫn là khác biệt
    (b" 3\n", "3\n"),         # khoảng trắng đầu dòng không được bỏ
    (b"3\n", "3\n3\n"),
    (b"", "0\n"),
])
def test_outputs_differ(actual, expected):
    assert not checker.outputs_match(actual, expected)


def test_cut_text_never_leaves_half_character_and_stays_within_limit():
    text = "ệ" * 10  # mỗi ký tự 3 byte
    for limit in range(0, 40):
        out = checker.cut_text(text.encode(), limit)
        assert len(out.encode("utf-8")) <= limit
        assert out == "ệ" * min(10, limit // 3)  # không có U+FFFD do cắt giữa ký tự


def test_cut_text_replaces_bad_bytes_and_still_respects_limit():
    out = checker.cut_text(bytes([97, 98, 0xFF, 0xFE, 0xFF, 0xFE, 0xFF]) * 10, 20)
    assert len(out.encode("utf-8")) <= 20 and "�" in out


def test_sanitize_ce_replaces_path_before_cutting():
    wd = "/work/job-abc123"
    raw = f"{wd}/Main.cpp:3:5: error: x\n{wd}/Main.cpp:4:1: error: y\n".encode()
    out = checker.sanitize_ce(raw, wd, "Main.cpp")
    assert wd not in out
    assert out.startswith("Main.cpp:3:5: error: x")


def test_sanitize_ce_limits_50_lines_and_4kb():
    many_lines = ("error line\n" * 500).encode()
    assert checker.sanitize_ce(many_lines, "/w", "Main.cpp").count("\n") == 49
    long_line = b"x" * 10_000
    assert len(checker.sanitize_ce(long_line, "/w", "Main.cpp").encode()) == 4096


def test_sanitize_ce_handles_invalid_utf8():
    out = checker.sanitize_ce(b"\xff\xfe bad \xc3\n", "/w", "Main.cpp")
    assert isinstance(out, str) and "�" in out


def test_sanitize_ce_does_not_cut_inside_path():
    # đường dẫn nằm ngay trước ranh giới 4 KB: phải thay trước khi cắt
    wd = "/work/job-" + "p" * 200
    raw = b"y" * 4000 + b"\n" + f"{wd}/Main.cpp:1: error".encode()
    out = checker.sanitize_ce(raw, wd, "Main.cpp")
    assert "/work/job-" not in out


# -------------------------------------------------------------------------------------------- languages

def test_validate_default_config_passes():
    validate()


def test_time_factors_match_readme():
    assert {n: l.time_factor for n, l in LANGUAGES.items()} == {"cpp": 1, "java": 2, "python": 5}


def test_compile_timeout_not_above_max():
    assert all(l.compile_timeout_s <= protocol.COMPILE_TIMEOUT_MAX_S for l in LANGUAGES.values())


def test_validate_rejects_factor_above_max():
    bad = {"python": LANGUAGES["python"].__class__(**{**LANGUAGES["python"].__dict__, "time_factor": 6})}
    with pytest.raises(ConfigError, match="MAX_TIME_FACTOR"):
        validate(bad)


def test_validate_rejects_compile_timeout_above_max():
    bad = {"cpp": LANGUAGES["cpp"].__class__(**{**LANGUAGES["cpp"].__dict__, "compile_timeout_s": 21})}
    with pytest.raises(ConfigError, match="COMPILE_TIMEOUT_MAX"):
        validate(bad)


def test_java_uses_xmx_only_no_address_space_limit_and_no_exit_on_oom():
    java = LANGUAGES["java"]
    assert "as" not in java.run_limits(262144)
    cmd = java.run_cmd(262144)
    assert "-Xmx256m" in cmd
    assert not any("ExitOnOutOfMemoryError" in a for a in cmd)
    assert java.rss_rule is False


def test_python_has_a_compile_only_syntax_check_step_with_approved_limits():
    lang = LANGUAGES["python"]
    assert lang.compile_cmd[:4] == ("python3", "-I", "-S", "-c") and len(lang.compile_cmd) == 5
    src = lang.compile_cmd[4]
    compile(src, "<check>", "exec")  # mã kiểm tự nó phải hợp lệ
    assert "compile(src, 'Main.py', 'exec'" in src  # tên file là hằng, không phải đường dẫn
    assert "exec(" not in src.replace("'exec'", "") and "import_module" not in src  # chỉ biên dịch, không chạy mã
    assert "rb" in src  # đọc bytes để tôn trọng `# coding:` và BOM
    assert lang.compile_timeout_s == 5 and lang.compile_as_bytes == 512 * 1024 * 1024
    assert lang.compile_limits()["as"] == 512 * 1024 * 1024
    validate()  # timeout ≤ COMPILE_TIMEOUT_MAX


def test_python_and_cpp_use_address_space_limit_and_rss_rule():
    for name in ("python", "cpp"):
        lang = LANGUAGES[name]
        assert lang.run_limits(1000)["as"] == 1000 * 1024
        assert lang.rss_rule is True


def test_candidate_runs_dropped_privileges_with_idle_scheduling_and_no_nproc():
    argv = sandbox._wrap(["./main"], {"as": 1024, "nofile": 32, "core": 1}, 61000)
    i = argv.index("setpriv")  # trước setpriv là bước root đặt oom_score_adj (D28)
    assert argv[i:i + 4] == ["setpriv", "--reuid=61000", "--regid=61000", "--clear-groups"]
    assert "--core=1" in argv and "--as=1024" in argv and not any("nproc" in a for a in argv)
    assert argv[-4:] == ["chrt", "--idle", "0", "./main"]


def test_no_nproc_limit_for_candidate_code():
    for lang in LANGUAGES.values():
        assert "nproc" not in lang.run_limits(262144)
        assert "nproc" not in lang.compile_limits()


# ---------------------------------------------------------------------------------------- classification

def run(**kw):
    return RunResult(**{"exit_code": 0, "wall_ms": 10.0, "maxrss_kib": 9000, **kw})


def verdict(lang, res, expected="3\n", ml=262144, scaled=2000):
    return executor.judge_run(LANGUAGES[lang], res, expected, ml, scaled)


def test_ac_and_wa():
    assert verdict("python", run(stdout=b"3\n")) == ("AC", None)
    assert verdict("python", run(stdout=b"4\n")) == ("WA", None)


def test_tle_is_checked_first_and_by_wall_time():
    assert verdict("cpp", run(timed_out=True, exit_code=None, signal=9))[0] == "TLE"
    assert verdict("cpp", run(wall_ms=2500.0, stdout=b"3\n"))[0] == "TLE"  # xong trong khoảng đệm nhưng quá giới hạn
    # TLE thắng cả chữ ký OOM
    assert verdict("python", run(timed_out=True, exit_code=None, signal=9, stderr_tail=b"MemoryError"))[0] == "TLE"


def test_output_over_cap_is_wa_even_though_process_was_killed():
    assert verdict("python", run(output_capped=True, exit_code=None, signal=9)) == ("WA", None)


@pytest.mark.parametrize("sig,kind", [(11, "SIGSEGV"), (8, "SIGFPE"), (6, "SIGABRT"), (9, "SIGKILL"),
                                        (7, "OTHER_SIGNAL"), (4, "OTHER_SIGNAL")])
def test_re_kind_from_signal(sig, kind):
    assert verdict("cpp", run(exit_code=None, signal=sig)) == ("RE", kind)


def test_re_kind_exit_nonzero_has_no_exit_value():
    v, kind = verdict("python", run(exit_code=137))
    assert (v, kind) == ("RE", "EXIT_NONZERO")


@pytest.mark.parametrize("lang,sig", [("python", b"MemoryError"), ("cpp", b"std::bad_alloc"),
                                       ("java", b"java.lang.OutOfMemoryError: Java heap space")])
def test_mle_by_signature(lang, sig):
    assert verdict(lang, run(exit_code=1, stderr_tail=b"...\n" + sig))[0] == "MLE"


def test_mle_signature_for_cpp_abort():
    res = run(exit_code=None, signal=6, stderr_tail=b"terminate called after throwing an instance of 'std::bad_alloc'")
    assert verdict("cpp", res)[0] == "MLE"


@pytest.mark.parametrize("lang", ["python", "cpp"])
def test_mle_by_rss_for_python_and_cpp(lang):
    assert verdict(lang, run(exit_code=None, signal=9, maxrss_kib=math.ceil(0.9 * 262144)))[0] == "MLE"
    assert verdict(lang, run(exit_code=None, signal=9, maxrss_kib=int(0.89 * 262144)))[0] == "RE"


def test_java_ignores_rss_rule():
    assert verdict("java", run(exit_code=1, maxrss_kib=290000)) == ("RE", "EXIT_NONZERO")


def test_signature_of_another_language_does_not_count():
    assert verdict("python", run(exit_code=1, stderr_tail=b"std::bad_alloc"))[0] == "RE"


def test_rss_rule_needs_abnormal_exit():
    assert verdict("python", run(stdout=b"3\n", maxrss_kib=262000)) == ("AC", None)


def test_oom_signature_beyond_stderr_tail_window_is_not_seen():
    # runner chỉ giữ đoạn cuối: chữ ký bị đẩy ra ngoài thì thành RE (D13)
    assert verdict("python", run(exit_code=1, stderr_tail=b"x" * 2048))[0] == "RE"


SLACK_KIB = executor.STATIC_IMAGE_SLACK_KIB


def seg(**kw):
    return run(exit_code=None, signal=11, maxrss_kib=12000, **kw)


def test_segv_with_a_static_image_over_limit_minus_slack_is_mle_for_cpp():
    limit = 262144
    over = limit - SLACK_KIB + 1  # đúng một KiB trên ngưỡng: MLE
    assert executor.judge_run(LANGUAGES["cpp"], seg(), "3\n", limit, 2000, static_kib=over) == ("MLE", None)
    assert executor.judge_run(LANGUAGES["cpp"], seg(), "3\n", limit, 2000, static_kib=300 * 1024) == ("MLE", None)


def test_segv_with_a_static_image_at_or_under_the_threshold_stays_re():
    limit = 262144
    at = limit - SLACK_KIB  # đúng ngưỡng (không lớn hơn): vẫn RE
    assert executor.judge_run(LANGUAGES["cpp"], seg(), "3\n", limit, 2000, static_kib=at) == ("RE", "SIGSEGV")
    assert executor.judge_run(LANGUAGES["cpp"], seg(), "3\n", limit, 2000, static_kib=at - 1) == ("RE", "SIGSEGV")
    assert executor.judge_run(LANGUAGES["cpp"], seg(), "3\n", limit, 2000, static_kib=None) == ("RE", "SIGSEGV")


@pytest.mark.parametrize("sig,kind", [(6, "SIGABRT"), (8, "SIGFPE"), (9, "SIGKILL")])
def test_only_sigsegv_is_reclassified_by_a_large_static_image(sig, kind):
    res = run(exit_code=None, signal=sig, maxrss_kib=12000)
    assert executor.judge_run(LANGUAGES["cpp"], res, "3\n", 262144, 2000, static_kib=300 * 1024) == ("RE", kind)


def test_the_static_image_rule_scales_with_the_limit():
    assert executor.judge_run(LANGUAGES["cpp"], seg(), "3\n", 65536, 2000, static_kib=53 * 1024) == ("MLE", None)
    assert executor.judge_run(LANGUAGES["cpp"], seg(), "3\n", 65536, 2000, static_kib=52 * 1024) == ("RE", "SIGSEGV")


def test_a_normal_exit_or_tle_is_not_changed_by_a_large_static_image():
    assert executor.judge_run(LANGUAGES["cpp"], run(stdout=b"3\n"), "3\n", 262144, 2000, static_kib=900 * 1024) == ("AC", None)
    assert executor.judge_run(LANGUAGES["cpp"], seg(timed_out=True), "3\n", 262144, 2000, static_kib=900 * 1024) == ("TLE", None)


def elf64(loads, *, cls=2, data=1, extra_types=()):
    """File ELF64 giả chỉ có header + bảng program header: `loads` là danh sách p_memsz của các PT_LOAD."""
    import struct
    entries = [(1, m) for m in loads] + [(t, 999999) for t in extra_types]
    ehdr = bytearray(64)
    ehdr[:4] = b"\x7fELF"
    ehdr[4], ehdr[5] = cls, data
    struct.pack_into("<Q", ehdr, 32, 64)  # e_phoff
    struct.pack_into("<HH", ehdr, 54, 56, len(entries))  # e_phentsize, e_phnum
    table = b"".join(struct.pack("<IIQQQQQQ", t, 5, 0, 0, 0, 0, memsz, 4096) for t, memsz in entries)
    return bytes(ehdr) + table


def test_elfinfo_sums_only_pt_load_and_rounds_up_to_kib(tmp_path):
    from worker import elfinfo
    p = tmp_path / "main"
    p.write_bytes(elf64([4096, 300 * 1024 * 1024 + 1], extra_types=(2, 6, 7)))  # DYNAMIC, PHDR, TLS không tính
    assert elfinfo.load_size_kib(str(p)) == 4 + 300 * 1024 + 1


@pytest.mark.parametrize("content", [b"", b"\x7fEL", b"#!/bin/sh\n" * 20, elf64([4096], cls=1), elf64([4096], data=2),
                                     elf64([])])
def test_elfinfo_returns_none_for_anything_but_a_valid_elf64_le(tmp_path, content):
    from worker import elfinfo
    p = tmp_path / "x"
    p.write_bytes(content)
    assert elfinfo.load_size_kib(str(p)) is None


def test_elfinfo_returns_none_for_a_missing_or_truncated_file(tmp_path):
    from worker import elfinfo
    assert elfinfo.load_size_kib(str(tmp_path / "nope")) is None
    p = tmp_path / "trunc"
    p.write_bytes(elf64([4096, 8192])[:100])  # bảng program header bị cắt
    assert elfinfo.load_size_kib(str(p)) is None


def test_elfinfo_reads_a_real_elf_of_this_machine():
    import sys
    from worker import elfinfo
    size = elfinfo.load_size_kib(sys.executable)
    assert size is not None and size > 0


def test_spawn_error_is_executor_error():
    with pytest.raises(executor.ExecutorError):
        verdict("python", RunResult(spawn_error="boom"))


# ---------------------------------------------------------------------------------------------- parse_job

def job_msg(**over):
    msg = {"type": "JOB", "submission_id": 1, "attempt_id": 2, "language": "python", "code": "print(1)",
           "time_limit_ms": 1000, "memory_limit_kib": 1024,
           "tests": [{"input": "", "output": "1\n", "is_sample": True}]}
    msg.update(over)
    return msg


def test_parse_job_ok():
    job = executor.parse_job(job_msg())
    assert job.language.name == "python" and job.tests[0].is_sample is True


# ----------------------------------------------------------------------------------------- config, backoff

def test_secret_file_is_preferred_and_env_is_consumed(tmp_path):
    f = tmp_path / "secret"
    f.write_text("from-file\n")
    env = {"JH_WORKER_SECRET_FILE": str(f), "JH_WORKER_SECRET": "from-env", "JH_WORKER_NAME": "n" * 100}
    cfg = load_config(env, consume_secret=True)
    assert cfg.secret == "from-file" and len(cfg.name) == 64
    assert "JH_WORKER_SECRET" not in env and "JH_WORKER_SECRET_FILE" not in env


def test_secret_from_env_when_no_file():
    env = {"JH_WORKER_SECRET": "abc"}
    assert load_config(env, consume_secret=True).secret == "abc"
    assert "JH_WORKER_SECRET" not in env


def test_missing_secret_is_config_error():
    with pytest.raises(MainConfigError):
        load_config({})
    with pytest.raises(MainConfigError):
        load_config({"JH_WORKER_SECRET_FILE": "/nonexistent/secret"})


@pytest.mark.parametrize("path", ["/tmp/work", "/tmp", "/var/tmp/x", "/dev/shm/w", "/tmp/../tmp/w"])
def test_work_root_inside_tmp_is_rejected(path):
    with pytest.raises(MainConfigError, match="JH_WORK_ROOT"):
        load_config({"JH_WORKER_SECRET": "x", "JH_WORK_ROOT": path})


def test_bad_number_is_config_error():
    with pytest.raises(MainConfigError):
        load_config({"JH_WORKER_SECRET": "x", "JH_WORKER_PORT": "abc"})


def test_backoff_sequence_cap_and_reset():
    b = Backoff(1, 10, 0.0, random.Random(1))
    assert [b.next_delay() for _ in range(6)] == [1, 2, 4, 8, 10, 10]
    b.reset()
    assert b.next_delay() == 1


def test_backoff_jitter_is_added_within_bounds():
    b = Backoff(1, 10, 0.25, random.Random(7))
    for expected in (1, 2, 4, 8, 10, 10):
        d = b.next_delay()
        assert expected <= d <= expected * 1.25


def test_main_returns_nonzero_when_language_config_violates_max(monkeypatch):
    from worker import main as worker_main

    def invalid(*args, **kwargs):
        raise languages.ConfigError("hệ số vượt MAX_TIME_FACTOR")

    monkeypatch.setattr(worker_main.languages, "validate", invalid)
    assert worker_main.main() == 2
