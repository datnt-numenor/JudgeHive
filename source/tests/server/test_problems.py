"""problems.py: thư mục đề, hash, giới hạn D3, seed và reimport (D5)."""

import json
import os
import shutil

import pytest

from common import protocol
from server import problems
from server.db import Database
from server.problems import ProblemError, TestCase, compute_tests_hash

SEED_DIR = os.path.join(os.path.dirname(problems.__file__), "problems")


def make_dir(root, problem_id="p1", samples=(("1 2\n", "3\n"),), tests=(("5 5\n", "10\n"),), meta=None,
             statement="de bai"):
    path = os.path.join(root, problem_id)
    os.makedirs(os.path.join(path, "samples"), exist_ok=True)
    os.makedirs(os.path.join(path, "tests"), exist_ok=True)
    meta = {"title": "Tong", "time_limit_ms": 1000, "memory_limit_kib": 65536} if meta is None else meta
    with open(os.path.join(path, "problem.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    with open(os.path.join(path, "statement.txt"), "w", encoding="utf-8") as f:
        f.write(statement)
    for sub, cases in (("samples", samples), ("tests", tests)):
        for number, (inp, out) in enumerate(cases, 1):
            for suffix, data in ((".in", inp), (".out", out)):
                with open(os.path.join(path, sub, f"{number:02d}{suffix}"), "wb") as f:
                    f.write(data if isinstance(data, bytes) else data.encode())
    return path


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "t.db"))
    database.migrate()
    return database


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "problems"
    path.mkdir()
    return str(path)


def test_load_orders_samples_before_hidden_tests_and_each_group_by_name(root):
    path = make_dir(root, samples=(("s1", "1"), ("s2", "2")), tests=(("h1", "3"), ("h2", "4"), ("h3", "5")))
    data = problems.load_problem_dir(path)
    assert [(t.is_sample, t.input) for t in data.tests] == [
        (True, b"s1"), (True, b"s2"), (False, b"h1"), (False, b"h2"), (False, b"h3")]
    assert (data.id, data.title, data.statement, data.time_limit_ms, data.memory_limit_kib) == (
        "p1", "Tong", "de bai", 1000, 65536)


def test_the_shipped_seed_problem_loads(root):
    data = problems.load_problem_dir(os.path.join(SEED_DIR, "aplusb"))
    assert data.id == "aplusb" and [t.is_sample for t in data.tests] == [True, True] + [False] * 5
    assert data.statement == data.statement.lower()  # đề là chữ thường


def test_hash_is_stable_and_sensitive_to_content_order_and_sample_flag():
    a = [TestCase(True, b"1", b"2"), TestCase(False, b"3", b"4")]
    assert compute_tests_hash(a) == compute_tests_hash(list(a))
    assert len(compute_tests_hash(a)) == 64
    assert compute_tests_hash(a) != compute_tests_hash(a[::-1])
    assert compute_tests_hash(a) != compute_tests_hash([TestCase(False, b"1", b"2"), a[1]])
    assert compute_tests_hash(a) != compute_tests_hash([TestCase(True, b"1", b"2"), TestCase(False, b"3", b"5")])


def test_hash_framing_is_not_ambiguous_when_input_and_output_are_shifted():
    assert compute_tests_hash([TestCase(False, b"ab", b"c")]) != compute_tests_hash([TestCase(False, b"a", b"bc")])
    assert compute_tests_hash([TestCase(False, b"a", b"b"), TestCase(False, b"c", b"d")]) \
        != compute_tests_hash([TestCase(False, b"ab", b""), TestCase(False, b"cd", b"")])


@pytest.mark.parametrize("break_it", [
    pytest.param(lambda p: os.remove(os.path.join(p, "problem.json")), id="no-problem-json"),
    pytest.param(lambda p: os.remove(os.path.join(p, "statement.txt")), id="no-statement"),
    pytest.param(lambda p: os.remove(os.path.join(p, "tests", "01.out")), id="input-without-output"),
    pytest.param(lambda p: os.remove(os.path.join(p, "tests", "01.in")), id="output-without-input"),
    pytest.param(lambda p: open(os.path.join(p, "tests", "01.in"), "wb").write(b"\xff\xfe"), id="not-utf8"),
    pytest.param(lambda p: open(os.path.join(p, "problem.json"), "w").write("{broken"), id="broken-json"),
    pytest.param(lambda p: open(os.path.join(p, "problem.json"), "w").write("[1]"), id="json-not-object"),
])
def test_broken_problem_directories_are_rejected(root, break_it):
    path = make_dir(root)
    break_it(path)
    with pytest.raises(ProblemError):
        problems.load_problem_dir(path)


@pytest.mark.parametrize("meta", [
    {"title": "", "time_limit_ms": 1000, "memory_limit_kib": 1024},
    {"time_limit_ms": 1000, "memory_limit_kib": 1024},
    {"title": "t", "time_limit_ms": 0, "memory_limit_kib": 1024},
    {"title": "t", "time_limit_ms": "1000", "memory_limit_kib": 1024},
    {"title": "t", "time_limit_ms": True, "memory_limit_kib": 1024},
    {"title": "t", "time_limit_ms": 1000, "memory_limit_kib": -5},
    {"title": "t", "time_limit_ms": 1000},
])
def test_bad_metadata_is_rejected(root, meta):
    with pytest.raises(ProblemError):
        problems.load_problem_dir(make_dir(root, meta=meta))


def test_problem_without_any_test_is_rejected(root):
    with pytest.raises(ProblemError):
        problems.load_problem_dir(make_dir(root, samples=(), tests=()))


@pytest.mark.parametrize("name", ["Upper", "has space", "x" * 65, "dot.dot"])
def test_bad_directory_names_are_rejected(root, name):
    with pytest.raises(ProblemError):
        problems.load_problem_dir(make_dir(root, problem_id=name))


def test_total_raw_size_over_8_mb_is_rejected():
    big = TestCase(False, b"a" * (protocol.MAX_TESTS_TOTAL_BYTES // 2 + 1), b"b" * (protocol.MAX_TESTS_TOTAL_BYTES // 2))
    with pytest.raises(ProblemError, match="vượt"):
        problems.check_limits([big])


def test_json_escape_can_push_the_job_line_over_16_mb_even_under_8_mb_raw():
    """8 MB thô toàn ký tự điều khiển phình 6 lần khi escape: D3 chỉ cho qua nếu dòng JOB xấu nhất vẫn vừa 16 MB."""
    nasty = TestCase(False, b"\x01" * 3_900_000, b"\x01" * 3_900_000)  # 7,8 MB thô, khoảng 47 MB sau escape
    with pytest.raises(ProblemError, match="JOB"):
        problems.check_limits([nasty])


def test_a_big_but_plain_problem_still_fits():
    problems.check_limits([TestCase(False, b"a" * 4_000_000, b"b" * 4_000_000)])


def test_make_job_message_decodes_utf8_and_keeps_order():
    msg = problems.make_job_message(7, 2, "python", "code", 1000, 2048,
                                    [(1, "đầu vào".encode(), b"ra"), (0, b"x", b"y")])
    assert msg["tests"] == [{"input": "đầu vào", "output": "ra", "is_sample": True},
                            {"input": "x", "output": "y", "is_sample": False}]
    assert (msg["submission_id"], msg["attempt_id"], msg["type"]) == (7, 2, "JOB")


def test_seed_inserts_missing_problems_only(db, root):
    make_dir(root, "one")
    make_dir(root, "two")
    assert problems.seed_problems(db, root) == ["one", "two"]
    assert problems.seed_problems(db, root) == []
    row = db.conn().execute("SELECT * FROM problems WHERE id = 'one'").fetchone()
    assert row["hidden"] == 0 and row["title"] == "Tong" and len(row["tests_hash"]) == 64
    count = db.conn().execute("SELECT COUNT(*) FROM tests WHERE problem_id = 'one'").fetchone()[0]
    assert count == 2


def test_seed_never_overwrites_an_existing_problem_or_its_hidden_flag(db, root):
    make_dir(root, "one")
    problems.seed_problems(db, root)
    with db.write() as c:  # admin sửa đề qua giao diện
        c.execute("UPDATE problems SET hidden = 1, title = 'Đã sửa' WHERE id = 'one'")
    before = db.conn().execute("SELECT tests_hash FROM problems WHERE id = 'one'").fetchone()[0]
    make_dir(root, "one", tests=(("9 9\n", "18\n"),), meta={"title": "Mới", "time_limit_ms": 5, "memory_limit_kib": 5})
    assert problems.seed_problems(db, root) == []
    row = db.conn().execute("SELECT * FROM problems WHERE id = 'one'").fetchone()
    assert (row["hidden"], row["title"], row["tests_hash"]) == (1, "Đã sửa", before)


def test_seed_skips_a_broken_problem_and_still_loads_the_others(db, root):
    make_dir(root, "bad", meta={"title": "x"})
    make_dir(root, "good")
    os.makedirs(os.path.join(root, ".hidden"))
    assert problems.seed_problems(db, root) == ["good"]


def test_seed_with_missing_directory_is_a_noop(db, tmp_path):
    assert problems.seed_problems(db, str(tmp_path / "nope")) == []


def test_reimport_replaces_content_and_hash_but_keeps_the_hidden_flag(db, root):
    make_dir(root, "one")
    problems.seed_problems(db, root)
    with db.write() as c:
        c.execute("UPDATE problems SET hidden = 1 WHERE id = 'one'")
    old = db.conn().execute("SELECT tests_hash FROM problems WHERE id = 'one'").fetchone()[0]
    make_dir(root, "one", samples=(("a", "b"),), tests=(("c", "d"), ("e", "f")),
             meta={"title": "Mới", "time_limit_ms": 2000, "memory_limit_kib": 4096})
    problems.reimport_problem(db, root, "one")
    row = db.conn().execute("SELECT * FROM problems WHERE id = 'one'").fetchone()
    assert (row["hidden"], row["title"], row["time_limit_ms"], row["memory_limit_kib"]) == (1, "Mới", 2000, 4096)
    assert row["tests_hash"] != old
    assert db.conn().execute("SELECT COUNT(*) FROM tests WHERE problem_id = 'one'").fetchone()[0] == 3


def test_reimport_inserts_when_the_problem_is_not_in_the_db_yet(db, root):
    make_dir(root, "fresh")
    problems.reimport_problem(db, root, "fresh")
    assert db.conn().execute("SELECT COUNT(*) FROM problems WHERE id = 'fresh'").fetchone()[0] == 1


@pytest.mark.parametrize("problem_id", ["missing", "../etc", "a/b", ""])
def test_reimport_rejects_unknown_or_unsafe_ids(db, root, problem_id):
    with pytest.raises(ProblemError):
        problems.reimport_problem(db, root, problem_id)


def test_reimport_of_a_broken_directory_leaves_the_db_untouched(db, root):
    make_dir(root, "one")
    problems.seed_problems(db, root)
    before = db.conn().execute("SELECT tests_hash FROM problems WHERE id = 'one'").fetchone()[0]
    shutil.rmtree(os.path.join(root, "one", "tests"))
    os.remove(os.path.join(root, "one", "statement.txt"))
    with pytest.raises(ProblemError):
        problems.reimport_problem(db, root, "one")
    assert db.conn().execute("SELECT tests_hash FROM problems WHERE id = 'one'").fetchone()[0] == before
    assert db.conn().execute("SELECT COUNT(*) FROM tests WHERE problem_id = 'one'").fetchone()[0] == 2


def test_store_problem_is_one_transaction(db):
    data = problems.ProblemData("p", "t", "s", 1, 1, (TestCase(False, b"a", b"b"),))
    with pytest.raises(RuntimeError):
        with db.write() as c:
            problems.store_problem(c, data, replace=False)
            raise RuntimeError("giữa chừng")
    assert db.conn().execute("SELECT COUNT(*) FROM problems").fetchone()[0] == 0
    assert db.conn().execute("SELECT COUNT(*) FROM tests").fetchone()[0] == 0
