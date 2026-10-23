"""Mã nguồn các chương trình thử (python / cpp / java), khóa theo tên chương trình."""

# Mọi chương trình đọc stdin nếu cần; bài chuẩn dùng dòng 1 là n, dòng 2 là n số.
SOURCES = {
    "hello": {
        "python": r'''print("hello")
''',
        "cpp": r'''#include <cstdio>
int main() { puts("hello"); }
''',
        "java": r'''public class Main {
    public static void main(String[] a) { System.out.println("hello"); }
}
''',
    },
    "big_block": {  # một lần cấp phát rất lớn (4 GiB / 2 GiB cho Java)
        "python": r'''x = bytearray(4 << 30)
print(len(x))
''',
        "cpp": r'''#include <cstdio>
int main() {
    volatile char* p = new char[4ULL << 30];
    p[0] = 1;
    printf("%d\n", (int)p[0]);
}
''',
        "java": r'''public class Main {
    public static void main(String[] a) {
        long[] x = new long[1 << 28];
        System.out.println(x.length);
    }
}
''',
    },
    "grow": {  # tăng dần từng khối 8 MiB, chạm trang, đến khi lỗi
        "python": r'''chunks = []
while True:
    b = bytearray(8 << 20)
    for i in range(0, len(b), 4096):
        b[i] = 1
    chunks.append(b)
''',
        "cpp": r'''#include <cstddef>
#include <vector>
volatile long sink;
int main() {
    std::vector<char*> v;
    const size_t size = 8 << 20;
    for (;;) {
        char* p = new char[size];
        for (size_t i = 0; i < size; i += 4096) ((volatile char*)p)[i] = 1;
        v.push_back(p);
        sink = (long)p;
    }
}
''',
        "java": r'''import java.util.*;
public class Main {
    public static void main(String[] a) {
        List<byte[]> l = new ArrayList<>();
        while (true) {
            byte[] b = new byte[8 << 20];
            Arrays.fill(b, (byte) 1);
            l.add(b);
        }
    }
}
''',
    },
    "div0": {  # a / b với "1 0" đọc từ stdin (1 / n bị gcc đổi thành so sánh, không còn phép chia)
        "python": r'''a, b = map(int, input().split())
print(a // b)
''',
        "cpp": r'''#include <cstdio>
int main() { int a, b; scanf("%d %d", &a, &b); printf("%d\n", a / b); }
''',
        "java": r'''public class Main {
    public static void main(String[] args) {
        java.util.Scanner in = new java.util.Scanner(System.in);
        int a = in.nextInt(), b = in.nextInt();
        System.out.println(a / b);
    }
}
''',
    },
    "fake_oom": {  # in giả chữ ký OOM rồi exit(1)
        "python": r'''import sys
sys.stderr.write("MemoryError\n")
sys.exit(1)
''',
        "cpp": r'''#include <cstdio>
int main() { fputs("std::bad_alloc\n", stderr); return 1; }
''',
        "java": r'''public class Main {
    public static void main(String[] a) {
        System.err.println("java.lang.OutOfMemoryError");
        System.exit(1);
    }
}
''',
    },
    "fake_oom_flood": {  # chữ ký giả rồi xả 1 MiB stderr: đoạn cuối cố định có nuốt chữ ký không
        "python": r'''import sys
sys.stderr.write("MemoryError\n")
sys.stderr.write("x" * (1 << 20) + "\n")
sys.exit(1)
''',
        "cpp": r'''#include <cstdio>
#include <cstring>
static char buf[1 << 20];
int main() {
    fputs("std::bad_alloc\n", stderr);
    memset(buf, 'x', sizeof buf);
    fwrite(buf, 1, sizeof buf, stderr);
    fputc('\n', stderr);
    return 1;
}
''',
        "java": r'''public class Main {
    public static void main(String[] a) {
        System.err.println("java.lang.OutOfMemoryError");
        System.err.println("x".repeat(1 << 20));
        System.exit(1);
    }
}
''',
    },
    "sum_io": {  # bài chuẩn I/O: tổng 10^6 số
        "python": r'''import sys
print(sum(map(int, sys.stdin.read().split()[1:])))
''',
        "cpp": r'''#include <iostream>
int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int n; std::cin >> n;
    long long s = 0, x;
    while (n--) { std::cin >> x; s += x; }
    std::cout << s << "\n";
}
''',
        "java": r'''import java.io.*;
import java.util.*;
public class Main {
    public static void main(String[] a) throws IOException {
        BufferedReader r = new BufferedReader(new InputStreamReader(System.in));
        int n = Integer.parseInt(r.readLine().trim());
        StringTokenizer st = new StringTokenizer(r.readLine());
        long s = 0;
        for (int i = 0; i < n; i++) s += Long.parseLong(st.nextToken());
        System.out.println(s);
    }
}
''',
    },
    "cpu_loop": {  # bài chuẩn CPU-bound: vòng lặp lồng 3000 x 3000
        "python": r'''N = 3000
acc = 0
for i in range(N):
    for j in range(N):
        acc = (acc * 31 + (i ^ j)) % 1000003
print(acc)
''',
        "cpp": r'''#include <cstdio>
int main() {
    const int N = 3000;
    long long acc = 0;
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++)
            acc = (acc * 31 + (i ^ j)) % 1000003;
    printf("%lld\n", acc);
}
''',
        "java": r'''public class Main {
    public static void main(String[] a) {
        final int N = 3000;
        long acc = 0;
        for (int i = 0; i < N; i++)
            for (int j = 0; j < N; j++)
                acc = (acc * 31 + (i ^ j)) % 1000003;
        System.out.println(acc);
    }
}
''',
    },
    "printer16": {  # in đúng 16 MiB: 2^20 dòng 16 byte
        "python": r'''import sys
sys.stdout.write("0123456789abcde\n" * 1048576)
''',
        "cpp": r'''#include <cstdio>
int main() { for (int i = 0; i < 1048576; i++) fwrite("0123456789abcde\n", 1, 16, stdout); }
''',
        "java": r'''import java.io.*;
public class Main {
    public static void main(String[] a) throws IOException {
        byte[] line = "0123456789abcde\n".getBytes();
        BufferedOutputStream o = new BufferedOutputStream(new FileOutputStream(FileDescriptor.out), 1 << 16);
        for (int i = 0; i < 1048576; i++) o.write(line);
        o.flush();
    }
}
''',
    },
    "spawn_sleep": {  # sinh 2 tiến trình con rồi ngủ: TLE phải kill cả nhóm
        "python": r'''import subprocess, time
subprocess.Popen(["sleep", "1000"])
subprocess.Popen(["sleep", "1000"])
time.sleep(1000)
''',
        "cpp": r'''#include <unistd.h>
int main() {
    for (int i = 0; i < 2; i++)
        if (fork() == 0) { execlp("sleep", "sleep", "1000", (char*)0); _exit(1); }
    sleep(1000);
}
''',
        "java": r'''public class Main {
    public static void main(String[] a) throws Exception {
        new ProcessBuilder("sleep", "1000").start();
        new ProcessBuilder("sleep", "1000").start();
        Thread.sleep(1000000);
    }
}
''',
    },
    "fork_bomb": {  # Python/C++ fork liên tục; Java tạo thread liên tục (thread cũng tính vào RLIMIT_NPROC)
        "python": r'''import os, sys
warned = False
while True:
    try:
        os.fork()
    except OSError:
        if not warned:
            warned = True
            sys.stderr.write("FORK_EAGAIN\n")
            sys.stderr.flush()
''',
        "cpp": r'''#include <unistd.h>
int main() {
    bool warned = false;
    for (;;)
        if (fork() < 0 && !warned) { warned = true; (void)!write(2, "FORK_EAGAIN\n", 12); }
}
''',
        "java": r'''public class Main {
    public static void main(String[] a) throws Exception {
        boolean warned = false;
        while (true) {
            try {
                new Thread(() -> { try { Thread.sleep(1000000); } catch (Exception e) { } }).start();
            } catch (Throwable e) {
                if (!warned) { warned = true; System.err.println("THREAD_EAGAIN " + e); }
                Thread.sleep(20);
            }
        }
    }
}
''',
    },
    "setsid_escape": {  # tiến trình con thoát nhóm bằng setsid(): killpg không với tới
        "python": r'''import subprocess, time
subprocess.Popen(["sleep", "1000"], start_new_session=True)
time.sleep(1000)
''',
        "cpp": r'''#include <unistd.h>
int main() {
    if (fork() == 0) { setsid(); execlp("sleep", "sleep", "1000", (char*)0); _exit(1); }
    sleep(1000);
}
''',
        "java": r'''public class Main {
    public static void main(String[] a) throws Exception {
        new ProcessBuilder("setsid", "sleep", "1000").start();
        Thread.sleep(1000000);
    }
}
''',
    },
    "medium": {  # chương trình vừa để đo thời gian biên dịch (kiểu bài thi: STL / collections)
        "cpp": r'''#include <bits/stdc++.h>
using namespace std;
struct Edge { int to; long long w; };
int main() {
    mt19937_64 rng(12345);
    int n = 2000;
    vector<vector<Edge>> g(n);
    for (int i = 0; i < 8000; i++) {
        int u = rng() % n, v = rng() % n;
        long long w = rng() % 1000 + 1;
        g[u].push_back({v, w}); g[v].push_back({u, w});
    }
    vector<long long> dist(n, LLONG_MAX);
    priority_queue<pair<long long, int>, vector<pair<long long, int>>, greater<>> pq;
    dist[0] = 0; pq.push({0, 0});
    while (!pq.empty()) {
        auto [d, u] = pq.top(); pq.pop();
        if (d > dist[u]) continue;
        for (auto& e : g[u])
            if (dist[u] + e.w < dist[e.to]) { dist[e.to] = dist[u] + e.w; pq.push({dist[e.to], e.to}); }
    }
    map<long long, int> freq; set<int> seen; unordered_map<string, int> names;
    for (int i = 0; i < n; i++) if (dist[i] != LLONG_MAX) { freq[dist[i] % 10]++; seen.insert(i % 7); }
    stringstream ss; for (auto& [k, v] : freq) ss << k << ":" << v << " ";
    names[ss.str()]++;
    vector<int> idx(n); iota(idx.begin(), idx.end(), 0);
    sort(idx.begin(), idx.end(), [&](int a, int b) { return dist[a] < dist[b]; });
    cout << idx[0] << " " << seen.size() << " " << names.size() << "\n";
}
''',
        "java": r'''import java.util.*;
import java.util.stream.*;
public class Main {
    record Edge(int to, long w) { }
    public static void main(String[] args) {
        Random rng = new Random(12345);
        int n = 2000;
        List<List<Edge>> g = new ArrayList<>();
        for (int i = 0; i < n; i++) g.add(new ArrayList<>());
        for (int i = 0; i < 8000; i++) {
            int u = rng.nextInt(n), v = rng.nextInt(n);
            long w = rng.nextInt(1000) + 1;
            g.get(u).add(new Edge(v, w)); g.get(v).add(new Edge(u, w));
        }
        long[] dist = new long[n];
        Arrays.fill(dist, Long.MAX_VALUE);
        PriorityQueue<long[]> pq = new PriorityQueue<>(Comparator.comparingLong((long[] x) -> x[0]));
        dist[0] = 0; pq.add(new long[]{0, 0});
        while (!pq.isEmpty()) {
            long[] top = pq.poll();
            int u = (int) top[1];
            if (top[0] > dist[u]) continue;
            for (Edge e : g.get(u))
                if (dist[u] + e.w() < dist[e.to()]) { dist[e.to()] = dist[u] + e.w(); pq.add(new long[]{dist[e.to()], e.to()}); }
        }
        Map<Long, Long> freq = Arrays.stream(dist).filter(d -> d != Long.MAX_VALUE)
            .boxed().collect(Collectors.groupingBy(d -> d % 10, TreeMap::new, Collectors.counting()));
        Set<Integer> seen = IntStream.range(0, n).map(i -> i % 7).boxed().collect(Collectors.toSet());
        StringBuilder sb = new StringBuilder();
        freq.forEach((k, v) -> sb.append(k).append(':').append(v).append(' '));
        System.out.println(sb.length() + " " + seen.size());
    }
}
''',
    },
}

SOURCES["alloc200"] = {  # selfcheck rusage: chạm 200 MiB rồi thoát
    "python": r'''x = bytearray(200 << 20)
x[::4096] = b"\1" * len(x[::4096])
print("ok")
''',
}

LANGS = ("python", "cpp", "java")
SIGNATURES = {"python": "MemoryError", "cpp": "std::bad_alloc", "java": "java.lang.OutOfMemoryError"}
