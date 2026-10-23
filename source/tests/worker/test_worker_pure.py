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


def test_python_and_cpp_use_address_space_limit_and_rss_rule():
    for name in ("python", "cpp"):
        lang = LANGUAGES[name]
        assert lang.run_limits(1000)["as"] == 1000 * 1024
        assert lang.rss_rule is True


def test_candidate_runs_dropped_privileges_with_idle_scheduling_and_no_nproc():
    argv = sandbox._wrap(["./main"], {"as": 1024, "nofile": 32, "core": 1}, 61000)
    assert argv[:4] == ["setpriv", "--reuid=61000", "--regid=61000", "--clear-groups"]
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


@pytest.mark.parametrize("over", [
    {"language": "ruby"}, {"language": 1}, {"language": None},
    {"code": None}, {"code": 5}, {"code": "x" * (protocol.MAX_CODE_BYTES + 1)},
    {"submission_id": "1"}, {"submission_id": True}, {"attempt_id": 1.5},
    {"time_limit_ms": 0}, {"time_limit_ms": "1000"}, {"memory_limit_kib": -1},
    {"tests": []}, {"tests": None}, {"tests": [1]},
    {"tests": [{"input": "", "output": "", "is_sample": 1}]},
    {"tests": [{"input": 1, "output": "", "is_sample": True}]},
    {"tests": [{"input": "", "is_sample": True}]},
])
def test_parse_job_rejects(over):
    with pytest.raises(executor.BadJob):
        executor.parse_job(job_msg(**over))


@pytest.mark.parametrize("missing", ["submission_id", "attempt_id", "language", "code", "time_limit_ms",
                                       "memory_limit_kib", "tests"])
def test_parse_job_rejects_missing_field(missing):
    msg = job_msg()
    del msg[missing]
    with pytest.raises(executor.BadJob):
        executor.parse_job(msg)


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
