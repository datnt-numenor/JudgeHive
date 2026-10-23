"""Gộp các file JSON thô của một lần chạy thành summary.md (bảng số liệu + đề xuất theo quy tắc ghi trước)."""

import glob
import json
import math
import os
import statistics
from collections import Counter, defaultdict

import programs

D13_EXPECTED = {"big_block": "MLE", "grow": "MLE", "div0": "RE", "fake_oom": "MLE|RE", "fake_oom_flood": "MLE|RE"}
RSS_RATIO = 0.9


def pct(values, p):
    """Nearest-rank percentile."""
    values = sorted(values)
    return values[max(0, math.ceil(p / 100 * len(values)) - 1)] if values else float("nan")


def med(values):
    return statistics.median(values) if values else float("nan")


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in row) + " |" for row in rows]
    return out + [""]


def fmt(x, nd=0):
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def load(path):
    parts = {}
    for f in sorted(glob.glob(os.path.join(path, "*.json"))):
        with open(f) as fh:
            data = json.load(fh)
        parts[data["part"]] = data
    return parts


def runs_of(parts, suite, **match):
    for data in parts.values():
        for r in data["runs"]:
            if r.get("suite") == suite and all(r.get(k) == v for k, v in match.items()):
                yield r


def group(runs, *keys):
    g = defaultdict(list)
    for r in runs:
        g[tuple(r.get(k) for k in keys)].append(r)
    return dict(sorted(g.items(), key=lambda kv: tuple(str(x) for x in kv[0])))


def abnormal(r):
    return r.get("timed_out") or r.get("spawn_error") or r.get("exit_code") != 0


def d13_verdicts(r):
    """Áp luật chốt tạm của PLAN D13 lên một lần chạy: (theo chữ ký, theo RSS, gộp)."""
    if r.get("timed_out"):
        return "TLE", "TLE", "TLE"
    if not abnormal(r):
        return "AC", "AC", "AC"
    sig = "MLE" if programs.SIGNATURES[r["lang"]] in r.get("stderr_tail", "") else "RE"
    rss = "MLE" if r["maxrss_kib"] >= RSS_RATIO * r["limit_kib"] else "RE"
    return sig, rss, "MLE" if "MLE" in (sig, rss) else "RE"


def dist(values):
    return ", ".join(f"{k}×{v}" for k, v in Counter(values).most_common())


def sec_meta(parts):
    meta = parts["main"]["meta"] if "main" in parts else next(iter(parts.values()))["meta"]
    keys = ["utc", "hostname", "os_release", "kernel", "cpu_model", "cpu_count", "cgroup_cpu_max",
            "cgroup_memory_max", "cgroup_pids_max", "python", "gxx", "javac", "java", "limit_kib",
            "stderr_tail_bytes"]
    return ["## Môi trường", ""] + table(["Trường", "Giá trị"], [(k, meta.get(k)) for k in keys])


def sec_selfcheck(parts):
    out = ["## Tự kiểm (rusage riêng từng lần, dọn nhóm sau killpg)", ""]
    checks = [r for r in runs_of(parts, "selfcheck") if r.get("case") == "check"]
    out += table(["Kiểm tra", "Kết quả", "Chi tiết"],
                 [(c["name"], "PASS" if c["passed"] else "**FAIL**",
                   {k: v for k, v in c.items() if k.endswith("_kib") or k in ("leftover", "clear_ms", "procs_at_kill")})
                  for c in checks])
    own = next(iter(runs_of(parts, "selfcheck", case="orchestrator_rss")), None)
    rows = [(lang, mode, fmt(med([r["maxrss_kib"] for r in rs])), fmt(max(r["maxrss_kib"] for r in rs)))
            for (lang, mode), rs in group(runs_of(parts, "selfcheck", case="hello_rss"), "lang", "variant").items()]
    out += ["`ru_maxrss` (KiB) của hello-world theo cách spawn (nếu `preexec` ≫ `wrap` hoặc ≫ ~1000 KiB cho "
            "C++ thì ru_maxrss bị phình bởi RSS của tiến trình điều phối trước `exec`).",
            f"RSS của tiến trình điều phối: VmRSS = {own and own['vmrss_kib']} KiB, "
            f"VmHWM = {own and own['vmhwm_kib']} KiB.", ""]
    return out + table(["Ngôn ngữ", "Cách spawn", "median", "max"], rows)


def sec_d13(parts):
    out = ["## D13: ma trận MLE/RE", "",
           f"Giới hạn 262144 KiB. `sig` = thoát bất thường VÀ cuối stderr có chữ ký OOM; "
           f"`rss` = thoát bất thường VÀ `ru_maxrss` ≥ {int(RSS_RATIO * 100)}% giới hạn; `gộp` = sig HOẶC rss "
           "(luật chốt tạm của PLAN D13). Kỳ vọng: khối lớn/tăng dần = MLE, chia 0 = RE, chữ ký giả = MLE hoặc RE.", ""]
    rows, gaps = [], []
    for (lang, variant, case), rs in group(runs_of(parts, "d13"), "lang", "variant", "case").items():
        verdicts = [d13_verdicts(r) for r in rs]
        combined = [v[2] for v in verdicts]
        expected = D13_EXPECTED[case]
        matched = sum(v in expected.split("|") for v in combined)
        if matched != len(rs):
            gaps.append((lang, variant, case, f"{matched}/{len(rs)}"))
        tail = rs[0].get("stderr_tail", "").strip().replace("\n", "⏎")
        rows.append((lang, variant, case, len(rs), dist([r.get("exit_code") for r in rs]),
                     dist([r.get("signal") for r in rs]), fmt(med([r["maxrss_kib"] for r in rs])),
                     repr(tail[-70:]), repr(rs[0].get("stdout_head", "").strip()[:60]), dist([v[0] for v in verdicts]), dist([v[1] for v in verdicts]),
                     dist(combined), expected, "ok" if matched == len(rs) else f"**{matched}/{len(rs)}**"))
    out += table(["ngôn ngữ", "biến thể", "ca", "n", "exit", "signal", "maxrss KiB (median)", "cuối stderr", "đầu stdout",
                  "sig", "rss", "gộp", "kỳ vọng", "khớp"], rows)
    out += ["Ô luật gộp không khớp kỳ vọng: " + (", ".join("/".join(g) for g in gaps) or "không có"), ""]
    return out


def compile_stats(parts):
    groups = group(runs_of(parts, "compile"), "lang", "case", "variant")
    rows, worst = [], []
    for (lang, prog, variant), rs in groups.items():
        t = [r["wall_ms"] / 1000 for r in rs]
        rows.append((lang, prog, variant, len(t), fmt(min(t), 2), fmt(med(t), 2), fmt(pct(t, 95), 2), fmt(max(t), 2)))
        if variant in ("cold_solo", "cold_parallel3", "warm_parallel3"):
            worst.append((max(t), lang, prog, variant))
    return rows, worst


def sec_compile(parts):
    rows, worst = compile_stats(parts)
    out = ["## Thời gian biên dịch (giây)", "",
           "`cold_solo`: lần đầu trong container, một mình. `cold_parallel3`: container mới, 3 biên dịch song "
           "song ngay lần đầu. `warm_solo`: các lần sau. `warm_parallel3`: 3 song song, đã ấm.", ""]
    return out + table(["ngôn ngữ", "chương trình", "kịch bản", "n", "min", "median", "p95", "max"], rows)


def sec_overhead(parts):
    out = ["## Overhead mỗi test", ""]
    rows = [(lang, variant, len(rs), fmt(med([r["wall_ms"] for r in rs])), fmt(pct([r["wall_ms"] for r in rs], 95)),
             fmt(max(r["wall_ms"] for r in rs)), fmt(med([r["maxrss_kib"] for r in rs])))
            for (lang, variant), rs in group(runs_of(parts, "overhead", case="startup"), "lang", "variant").items()]
    out += ["Khởi động hello-world (ms, wall từ spawn đến wait4):", ""]
    out += table(["ngôn ngữ", "biến thể", "n", "median", "p95", "max", "maxrss KiB (median)"], rows)
    rows = [(lang, len(rs), fmt(med([r["wall_ms"] for r in rs])), fmt(max(r["wall_ms"] for r in rs)),
             fmt(med([r["compare_ms"] for r in rs])), fmt(max(r["compare_ms"] for r in rs)),
             "ok" if all(r["matched"] for r in rs) else "**SAI**")
            for (lang,), rs in group(runs_of(parts, "overhead", case="print16"), "lang").items()]
    out += ["In 16 MiB: wall gồm chạy chương trình và đọc có trần; `compare` là chuẩn hóa + so với đáp án "
            "(cài đặt tham chiếu của spike, ms):", ""]
    return out + table(["ngôn ngữ", "n", "wall median", "wall max", "compare median", "compare max", "đúng"], rows)


def sec_bench(parts):
    out = ["## Bài chuẩn: tỷ lệ so với C++ (không đề xuất hệ số: đó là chính sách)", ""]
    for prog, label in (("sum_io", "Tổng 10^6 số (I/O + parse)"), ("cpu_loop", "Vòng lặp lồng 3000×3000 (CPU-bound)")):
        groups = group(runs_of(parts, "bench", case=prog), "lang")
        base = med([r["wall_ms"] for r in groups.get(("cpp",), [])])
        rows = []
        for lang in ("cpp", "java", "python"):
            rs = groups.get((lang,), [])
            if not rs:
                continue
            w = [r["wall_ms"] for r in rs]
            cpu = [r["utime_ms"] + r["stime_ms"] for r in rs]
            rows.append((lang, len(rs), fmt(med(w)), fmt(pct(w, 95)), fmt(med(cpu)), fmt(med(w) / base, 1),
                         fmt(pct(w, 95) / base, 1), fmt(med([r["maxrss_kib"] for r in rs])),
                         "ok" if all(r.get("correct") for r in rs) else "**SAI/lỗi**"))
        out += [f"**{label}**", ""]
        out += table(["ngôn ngữ", "n", "wall median ms", "wall p95 ms", "CPU median ms", "tỷ lệ median/C++",
                      "tỷ lệ p95/C++ median", "maxrss KiB", "đúng"], rows)
    return out


def sec_kill(parts):
    out = ["## killpg khi TLE, fork bomb, thoát nhóm", ""]
    rows = [(lang, len(rs), sum(bool(r["timed_out"]) for r in rs), max(r["group_procs_at_kill"] or 0 for r in rs),
             max(r["group_leftover"] or 0 for r in rs), fmt(max(r["group_clear_ms"] or 0 for r in rs), 1))
            for (lang,), rs in group(runs_of(parts, "kill", case="spawn_sleep"), "lang").items()]
    out += ["**spawn_sleep** (timeout 2 s): số tiến trình trong nhóm lúc kill, còn sót sau killpg, thời gian nhóm sạch:", ""]
    out += table(["ngôn ngữ", "n", "TLE", "tiến trình lúc kill (max)", "sót sau killpg (max)", "nhóm sạch sau ms (max)"], rows)
    rows = []
    for (lang, variant), rs in group(runs_of(parts, "kill", case="fork_bomb"), "lang", "variant").items():
        rows.append((lang, variant, len(rs), sum(bool(r["timed_out"]) for r in rs),
                     max(r["group_procs_at_kill"] or 0 for r in rs), max(r["group_threads_at_kill"] or 0 for r in rs),
                     sum(bool(r.get("eagain_seen")) for r in rs), max(r["group_leftover"] or 0 for r in rs),
                     max(r.get("uid_procs_after_killpg", 0) for r in rs), dist([r.get("exit_code") for r in rs]),
                     fmt(max(r["group_clear_ms"] or 0 for r in rs), 1)))
    out += ["**fork_bomb** (timeout 3 s; Java là thread bomb): `EAGAIN` thấy trong stderr nghĩa là RLIMIT_NPROC chặn được:", ""]
    out += table(["ngôn ngữ", "NPROC", "n", "TLE", "tiến trình lúc kill (max)", "thread lúc kill (max)",
                  "thấy EAGAIN", "sót sau killpg (max)", "uid còn tiến trình sau killpg (max)", "exit", "sạch sau ms (max)"], rows)
    rows = [(r["lang"], r["timed_out"], r.get("escaped_alive_after_killpg"), r.get("uid_kill_count"),
             r.get("uid_procs_after_uid_kill")) for r in runs_of(parts, "kill", case="setsid_escape")]
    out += ["**setsid_escape**: tiến trình con thoát nhóm; killpg không với tới, kill theo uid thì dọn được:", ""]
    return out + table(["ngôn ngữ", "TLE", "còn sống sau killpg", "số tiến trình kill theo uid", "còn sau kill theo uid"], rows)


def sec_floors(parts):
    out = ["## Ngưỡng tối thiểu để chương trình hello khởi động được", ""]
    rows = []
    for (kind, lang), rs in group(runs_of(parts, "floors"), "case", "lang").items():
        by_value = defaultdict(list)
        for r in rs:
            by_value[r["value"]].append(bool(r["passed"]))
        passing = sorted(v for v, flags in by_value.items() if all(flags))
        rows.append((kind.replace("floor_", ""), lang, min(passing) if passing else "không giá trị nào",
                     " ".join(f"{v}:{sum(f)}/{len(f)}" for v, f in sorted(by_value.items()))))
    return out + table(["giới hạn", "ngôn ngữ", "nhỏ nhất đạt (mọi lần)", "pass theo giá trị"], rows)


def sec_isolation(parts):
    out = ["## Hai container cùng uid: fork bomb có ảnh hưởng bài bình thường không", ""]
    names = sorted({p.split("-")[1] for p in parts if p.startswith("iso-")})
    summary, facts = [], []
    for name in names:
        victim, bomb = parts.get(f"iso-{name}-victim"), parts.get(f"iso-{name}-bomb")
        if not victim or not bomb:
            continue
        window = next(r for r in bomb["runs"] if r.get("case") == "bomb_window")
        va, ba = victim["meta"]["args"], bomb["meta"]["args"]
        cfg = (f"victim uid {va['uid']} NPROC {va['nproc'] or 'không'}, pids_limit {victim['meta']['cgroup_pids_max']}; "
               f"bomb uid {ba['uid']} NPROC {ba['nproc'] or 'không'}, pids_limit {bomb['meta']['cgroup_pids_max']}")
        runs = [r for r in victim["runs"] if r.get("case") == "victim_run"]
        total_fail_in_window = 0
        for lang in ("java", "python", "cpp"):
            row = [name, cfg if lang == "java" else "", lang]
            for phase in ("trước", "trong", "sau"):
                rs = [r for r in runs if r["lang"] == lang and
                      ((phase == "trước" and r["t_epoch"] < window["t_start"]) or
                       (phase == "trong" and window["t_start"] <= r["t_epoch"] <= window["t_end"]) or
                       (phase == "sau" and r["t_epoch"] > window["t_end"]))]
                bad = [r for r in rs if not r["ok"]]
                if phase == "trong":
                    total_fail_in_window += len(bad)
                row.append(f"{len(bad)}/{len(rs)}")
            summary.append(row)
        sample = next((r for r in runs if not r["ok"]), None)
        facts.append(f"- `{name}`: bomb {'thấy EAGAIN' if window.get('eagain_seen') else 'không thấy EAGAIN'}, "
                     f"{window.get('group_procs_at_kill')} tiến trình lúc kill; victim lỗi trong cửa sổ: "
                     f"**{total_fail_in_window}**" + (f"; ví dụ lỗi: exit {sample['exit_code']}, "
                                                      f"spawn_error {sample['spawn_error']}, stderr {sample['stderr_tail'][-120:]!r}"
                                                      if sample else ""))
    out += table(["kịch bản", "cấu hình", "ngôn ngữ", "lỗi/lần trước bomb", "lỗi/lần trong bomb", "lỗi/lần sau bomb"], summary)
    return out + facts + [""]


def sec_proposals(parts):
    out = ["## Đề xuất theo quy tắc ghi trước (chỉ đề xuất, chưa quyết)", ""]
    _, worst = compile_stats(parts)
    if worst:
        w = max(worst)
        out += [f"- **COMPILE_TIMEOUT_MAX** = 2 × thời gian biên dịch tệ nhất trong các kịch bản lần đầu/song song "
                f"({w[0]:.2f} s, {w[1]}/{w[2]}/{w[3]}) làm tròn lên giây = **{math.ceil(2 * w[0])} s** "
                "(giá trị hiện tại trong README: 10 s)."]
    prints = list(runs_of(parts, "overhead", case="print16"))
    if prints:
        sample = max(r["wall_ms"] for r in prints) + max(r["compare_ms"] for r in prints)
        proposal = math.ceil(2 * sample / 1000 * 2) / 2
        startup = max((r["wall_ms"] for r in runs_of(parts, "overhead", case="startup")), default=0)
        out += [f"- **PER_TEST_OVERHEAD** = 2 × (wall tệ nhất khi in 16 MiB + compare tệ nhất) = 2 × {sample:.0f} ms, "
                f"làm tròn lên 0,5 s = **{proposal:g} s** (n nhỏ nên p99 ≈ max; khởi động tệ nhất {startup:.0f} ms; "
                "giá trị hiện tại: 3 s)."]
    out += ["- **Hệ số thời gian / MAX_TIME_FACTOR**: không đề xuất; xem bảng tỷ lệ so với C++ ở trên.",
            "- **D13**: xem bảng ma trận và danh sách ô luật gộp không khớp ở mục D13.", ""]
    return out


def build(path):
    parts = load(path)
    lines = ["# Kết quả spike executor (PR 2)", "", f"Thư mục: `{os.path.basename(path)}`; parts: " + ", ".join(parts), ""]
    failed = [c["name"] for c in runs_of(parts, "selfcheck") if c.get("case") == "check" and not c["passed"]]
    if failed:
        lines += [f"> **SELFCHECK FAIL: {', '.join(failed)}**: số liệu liên quan không đáng tin.", ""]
    for section in (sec_meta, sec_selfcheck, sec_d13, sec_compile, sec_overhead, sec_bench, sec_kill, sec_floors,
                    sec_isolation, sec_proposals):
        lines += section(parts)
    with open(os.path.join(path, "summary.md"), "w") as f:
        f.write("\n".join(lines))
