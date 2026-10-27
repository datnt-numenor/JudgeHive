"""Hàm xếp hạng thuần (README 3.3, "Luật xếp hạng"; D12). Không DB, không đồng hồ. Ứng viên khóa: toàn bộ file này."""

import itertools
import random

import pytest

from server.scoreboard.ranking import WRONG_VERDICTS, Entry, Row, compute_rows


def e(user, problem, sid, at, verdict):
    return Entry(user, f"user{user}", problem, sid, at, verdict)


def view(rows):
    return [(r.rank, r.username, r.solved, r.wrong, r.last_ac_ms) for r in rows]


def test_empty_input_gives_an_empty_board():
    assert compute_rows([]) == []


def test_a_single_ac_without_wrong_attempts():
    assert view(compute_rows([e(1, "p", 1, 1000, "AC")])) == [(1, "user1", 1, 0, 1000)]


def test_only_wrong_attempts_before_the_first_ac_count():
    rows = compute_rows([e(1, "p", 1, 100, "WA"), e(1, "p", 2, 200, "TLE"), e(1, "p", 3, 300, "AC"),
                         e(1, "p", 4, 400, "WA"), e(1, "p", 5, 500, "AC")])
    assert view(rows) == [(1, "user1", 1, 2, 300)]


@pytest.mark.parametrize("verdict", sorted(WRONG_VERDICTS))
def test_each_wrong_verdict_counts_once(verdict):
    assert view(compute_rows([e(1, "p", 1, 1, verdict), e(1, "p", 2, 2, "AC")])) == [(1, "user1", 1, 1, 2)]


@pytest.mark.parametrize("verdict", ["CE", "SE"])
def test_ce_and_se_are_never_wrong_attempts(verdict):
    assert view(compute_rows([e(1, "p", 1, 1, verdict), e(1, "p", 2, 2, "AC")])) == [(1, "user1", 1, 0, 2)]


def test_wrong_attempts_on_an_unsolved_problem_do_not_count():
    rows = compute_rows([e(1, "p", 1, 1, "WA"), e(1, "p", 2, 2, "WA"), e(1, "q", 3, 3, "AC")])
    assert view(rows) == [(1, "user1", 1, 0, 3)]


def test_a_person_with_only_failed_or_compile_error_submissions_still_has_a_row():
    rows = compute_rows([e(1, "p", 1, 1, "CE"), e(2, "p", 2, 2, "WA"), e(3, "p", 3, 3, "SE")])
    assert view(rows) == [(1, "user1", 0, 0, None), (1, "user2", 0, 0, None), (1, "user3", 0, 0, None)]


def test_wrong_attempts_sum_over_solved_problems_and_last_ac_is_the_latest_first_ac():
    rows = compute_rows([
        e(1, "p", 1, 100, "WA"), e(1, "p", 2, 200, "AC"), e(1, "p", 3, 900, "AC"),  # AC sau bị bỏ qua
        e(1, "q", 4, 300, "RE"), e(1, "q", 5, 400, "MLE"), e(1, "q", 6, 700, "AC"),
        e(1, "r", 7, 50, "WA"),  # chưa giải
    ])
    assert view(rows) == [(1, "user1", 2, 3, 700)]


def test_first_ac_is_by_received_time_then_submission_id_not_by_input_order():
    entries = [e(1, "p", 20, 100, "AC"), e(1, "p", 10, 100, "WA"), e(1, "p", 5, 100, "AC"), e(1, "p", 1, 300, "WA")]
    # cùng thời điểm: id 5 (AC) đứng trước id 10 (WA) và id 20 (AC): AC đầu là id 5, nên WA id 10 không tính
    assert view(compute_rows(entries)) == [(1, "user1", 1, 0, 100)]
    entries = [e(1, "p", 11, 100, "AC"), e(1, "p", 10, 100, "WA")]
    assert view(compute_rows(entries)) == [(1, "user1", 1, 1, 100)]  # id 10 (WA) trước id 11 (AC)


def test_ordering_solved_then_wrong_then_last_ac():
    rows = compute_rows([
        e(1, "p", 1, 100, "AC"),                                         # user1: 1 đề, 0 sai, 100
        e(2, "p", 2, 90, "WA"), e(2, "p", 3, 120, "AC"),                  # user2: 1 đề, 1 sai, 120
        e(3, "p", 4, 50, "AC"), e(3, "q", 5, 400, "AC"),                  # user3: 2 đề
        e(4, "p", 6, 60, "AC"),                                           # user4: 1 đề, 0 sai, 60
    ])
    assert [r.username for r in rows] == ["user3", "user4", "user1", "user2"]
    assert [r.rank for r in rows] == [1, 2, 3, 4]


def test_ties_share_a_rank_and_the_next_rank_is_skipped():
    rows = compute_rows([e(1, "p", 1, 100, "AC"), e(2, "p", 2, 100, "AC"), e(3, "p", 3, 100, "AC"),
                         e(4, "p", 4, 200, "AC")])
    assert [(r.rank, r.username) for r in rows] == [(1, "user1"), (1, "user2"), (1, "user3"), (4, "user4")]


def test_no_solved_problems_rank_below_everyone_who_solved_something_and_share_a_rank():
    rows = compute_rows([e(1, "p", 1, 1, "WA"), e(2, "p", 2, 5, "AC"), e(3, "p", 3, 1, "CE")])
    assert [(r.rank, r.username) for r in rows] == [(1, "user2"), (2, "user1"), (2, "user3")]


def test_same_rank_rows_are_ordered_by_username_bytes():
    entries = [Entry(i, name, "p", i, 100, "AC") for i, name in enumerate(["b", "a", "B", "é", "Z", "z"], 1)]
    assert [r.username for r in compute_rows(entries)] == ["B", "Z", "a", "b", "z", "é"]  # byte UTF-8, không theo locale


def test_millisecond_differences_matter():
    rows = compute_rows([e(1, "p", 1, 1001, "AC"), e(2, "p", 2, 1000, "AC")])
    assert [(r.rank, r.username) for r in rows] == [(1, "user2"), (2, "user1")]


def test_input_is_not_modified_and_row_is_immutable():
    entries = [e(2, "p", 2, 2, "AC"), e(1, "p", 1, 1, "WA")]
    snapshot = list(entries)
    compute_rows(entries)
    assert entries == snapshot
    with pytest.raises(Exception):
        compute_rows(entries)[0].rank = 9


def test_any_order_of_the_input_gives_the_same_board():
    entries = [e(1, "p", 1, 10, "WA"), e(1, "p", 2, 20, "AC"), e(2, "p", 3, 15, "AC"), e(2, "q", 4, 30, "AC"),
               e(3, "q", 5, 5, "RE"), e(3, "q", 6, 6, "AC")]
    expected = compute_rows(entries)
    for perm in itertools.islice(itertools.permutations(entries), 200):
        assert compute_rows(list(perm)) == expected


# ---- đối chiếu với một cài đặt tham chiếu viết độc lập (brute force) -------------------------------------------

def reference(entries):
    users = sorted({x.username for x in entries})
    stat = {}
    for u in users:
        solved = wrong = 0
        last = None
        for p in sorted({x.problem_id for x in entries if x.username == u}):
            subs = sorted((x for x in entries if x.username == u and x.problem_id == p),
                          key=lambda x: (x.received_ms, x.submission_id))
            w = 0
            for x in subs:
                if x.verdict == "AC":
                    solved += 1
                    wrong += w
                    last = x.received_ms if last is None else max(last, x.received_ms)
                    break
                if x.verdict in ("WA", "TLE", "MLE", "RE"):
                    w += 1
        stat[u] = (solved, wrong, last)

    def key(u):
        s, w, last = stat[u]
        return (-s, w, last is None, last or 0)

    out = []
    for u in sorted(users, key=lambda u: key(u) + (u.encode("utf-8"),)):
        rank = 1 + sum(1 for v in users if key(v) < key(u))
        out.append((rank, u) + stat[u])
    return out


def test_matches_an_independent_reference_on_random_data():
    rnd = random.Random(20261027)
    verdicts = ["AC", "WA", "TLE", "MLE", "RE", "CE", "SE"]
    for _ in range(400):
        names = {}
        entries = []
        for sid in range(1, rnd.randint(1, 80)):
            uid = rnd.randint(1, 9)
            name = names.setdefault(uid, rnd.choice(["u", "U", "é", "z"]) + str(uid))
            entries.append(Entry(uid, name, f"p{rnd.randint(1, 4)}", sid, rnd.randint(0, 6), rnd.choice(verdicts)))
        got = [(r.rank, r.username, r.solved, r.wrong, r.last_ac_ms) for r in compute_rows(entries)]
        assert got == [tuple(x) for x in reference(entries)]


def test_row_type_fields():
    assert Row(1, "a", 1, 0, 5) == Row(rank=1, username="a", solved=1, wrong=0, last_ac_ms=5)
