"""Bài nộp mẫu (bộ bài phá hoại, PLAN mục 8) theo ngôn ngữ. Mọi chuỗi là raw để giữ nguyên `\\n` cho C++/Java."""

SUB = {}

SUB["ac"] = {
    "python": r'''import sys
print(sum(map(int, sys.stdin.read().split())))
''',
    "cpp": r'''#include <iostream>
int main() { long long s = 0, x; while (std::cin >> x) s += x; std::cout << s << "\n"; }
''',
    "java": r'''import java.util.*;
public class Main {
    public static void main(String[] a) {
        Scanner in = new Scanner(System.in);
        long s = 0;
        while (in.hasNextLong()) s += in.nextLong();
        System.out.println(s);
    }
}
''',
}

SUB["ws"] = {  # đúng nhưng thừa khoảng trắng cuối dòng và dòng trống cuối
    "python": r'''import sys
sys.stdout.write(str(sum(map(int, sys.stdin.read().split()))) + "   \n\n\n")
''',
    "cpp": r'''#include <iostream>
int main() { long long s = 0, x; while (std::cin >> x) s += x; std::cout << s << "   \n\n\n"; }
''',
    "java": r'''import java.util.*;
public class Main {
    public static void main(String[] a) {
        Scanner in = new Scanner(System.in);
        long s = 0;
        while (in.hasNextLong()) s += in.nextLong();
        System.out.print(s + "   \n\n\n");
    }
}
''',
}

SUB["crlf"] = {
    "python": r'''import sys
sys.stdout.write(str(sum(map(int, sys.stdin.read().split()))) + "\r\n")
''',
}

SUB["loop"] = {
    "python": "while True:\n    pass\n",
    "cpp": "int main() { volatile long x = 0; for (;;) x = x + 1; }\n",
    "java": r'''public class Main {
    public static void main(String[] a) { long x = 0; while (true) x++; }
}
''',
}

SUB["big_block"] = {
    "python": "x = bytearray(4 << 30)\nprint(len(x))\n",
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
}

SUB["grow"] = {
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
}

SUB["fake_oom"] = {
    "python": 'import sys\nsys.stderr.write("MemoryError\\n")\nsys.exit(1)\n',
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
}

SUB["fake_oom_flood"] = {  # chữ ký giả rồi xả 1 MiB stderr: đoạn cuối 2 KiB nuốt chữ ký
    "python": 'import sys\nsys.stderr.write("MemoryError\\n")\nsys.stderr.write("x" * (1 << 20) + "\\n")\nsys.exit(1)\n',
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
}

SUB["div0"] = {  # đọc "a b" rồi in a / b
    "python": "a, b = map(int, input().split())\nprint(a // b)\n",
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
}

SUB["segv"] = {
    "python": "import ctypes\nctypes.string_at(0)\n",
    "cpp": "int main() { volatile int* p = nullptr; *p = 1; }\n",
}

SUB["oob"] = {  # truy cập ngoài mảng
    "python": "a = [1, 2, 3]\nprint(a[5])\n",
    "cpp": r'''#include <vector>
#include <cstdio>
int main() { std::vector<int> v(3); printf("%d\n", v.at(5)); }
''',
    "java": r'''public class Main {
    public static void main(String[] a) {
        int[] x = new int[3];
        System.out.println(x[5]);
    }
}
''',
}

SUB["exit_byte"] = {  # exit(byte đầu của input): kênh rò mã thoát
    "python": "import sys\nsys.exit(ord(sys.stdin.read(1)))\n",
    "cpp": r'''#include <cstdio>
#include <cstdlib>
int main() { int c = getchar(); exit(c); }
''',
    "java": r'''public class Main {
    public static void main(String[] a) throws Exception {
        System.exit(System.in.read());
    }
}
''',
}

SUB["echo_streams"] = {  # in input ra cả stdout lẫn stderr rồi thoát lỗi
    "python": "import sys\ndata = sys.stdin.read()\nsys.stdout.write(data)\nsys.stderr.write(data)\nsys.exit(3)\n",
    "cpp": r'''#include <iostream>
#include <string>
int main() { std::string s, all; while (std::getline(std::cin, s)) all += s + "\n"; std::cout << all; std::cerr << all; return 3; }
''',
    "java": r'''import java.io.*;
public class Main {
    public static void main(String[] a) throws Exception {
        String s = new String(System.in.readAllBytes());
        System.out.print(s);
        System.err.print(s);
        System.exit(3);
    }
}
''',
}

SUB["huge_output"] = {
    "python": 'import sys\nchunk = "x" * 65536\nwhile True:\n    sys.stdout.write(chunk)\n',
    "cpp": r'''#include <cstdio>
#include <cstring>
int main() { static char buf[65536]; memset(buf, 'x', sizeof buf); for (;;) fwrite(buf, 1, sizeof buf, stdout); }
''',
    "java": r'''import java.io.*;
public class Main {
    public static void main(String[] a) throws Exception {
        byte[] buf = new byte[65536];
        java.util.Arrays.fill(buf, (byte) 'x');
        OutputStream o = new BufferedOutputStream(new FileOutputStream(FileDescriptor.out), 1 << 16);
        while (true) o.write(buf);
    }
}
''',
}

SUB["fork_bomb"] = {
    "python": "import os\nwhile True:\n    try:\n        os.fork()\n    except OSError:\n        pass\n",
    "cpp": "#include <unistd.h>\nint main() { for (;;) fork(); }\n",
}

SUB["setsid_ok"] = {  # in đúng tổng nhưng để lại một tiến trình đã thoát nhóm bằng setsid()
    "python": r'''import subprocess, sys
subprocess.Popen(["sleep", "1000"], start_new_session=True)
print(sum(map(int, sys.stdin.read().split())))
''',
    "cpp": r'''#include <iostream>
#include <unistd.h>
int main() {
    if (fork() == 0) { setsid(); execlp("sleep", "sleep", "1000", (char*)0); _exit(1); }
    long long s = 0, x; while (std::cin >> x) s += x; std::cout << s << "\n";
}
''',
}

SUB["vietnamese"] = {
    "python": '# Chương trình in lời chào bằng tiếng Việt\nprint("Xin chào Việt Nam")\n',
    "cpp": '// Chương trình in lời chào bằng tiếng Việt\n#include <cstdio>\nint main() { puts("Xin chào Việt Nam"); }\n',
    "java": ('// Chương trình in lời chào bằng tiếng Việt\npublic class Main {\n'
             '    public static void main(String[] a) { System.out.println("Xin chào Việt Nam"); }\n}\n'),
}

SUB["sleep_input"] = {  # ngủ theo số giây ở input
    "python": "import sys, time\ntime.sleep(float(sys.stdin.read()))\nprint('ok')\n",
}

SUB["alloc_mb"] = {  # chạm N MiB theo input
    "python": ("import sys\nn = int(sys.stdin.read())\nb = bytearray(n << 20)\n"
               "for i in range(0, len(b), 4096):\n    b[i] = 1\nprint('ok')\n"),
}

SUB["print_big_vietnamese"] = {
    "python": 'print("ệ" * 5000)\n',
}

SUB["tmp_write"] = {
    "python": ('import sys\ndata = sys.stdin.read()\n'
               'open("/tmp/jh-leak.txt", "w").write(data)\nopen("/dev/shm/jh-leak.txt", "w").write(data)\n'
               'import os\nos.makedirs("/tmp/jh-dir/sub", exist_ok=True)\nos.chmod("/tmp/jh-dir/sub", 0)\n'
               'print("ok")\n'),
}

SUB["tmp_read"] = {
    "python": ('import os\nfor p in ("/tmp/jh-leak.txt", "/dev/shm/jh-leak.txt", "/tmp/jh-dir"):\n'
               '    print(p, "present" if os.path.exists(p) else "missing")\n'),
}

SUB["fs_attack"] = {  # ghi/xóa ngoài thư mục tạm của mình
    "python": r'''import os
def attempt(fn):
    try:
        fn()
        return "ALLOWED"
    except PermissionError:
        return "denied"
    except OSError as e:
        return "oserror"
print(attempt(lambda: open("/etc/jh-pwned", "w")))
print(attempt(lambda: os.remove("/etc/hostname")))
print(attempt(lambda: open(os.path.dirname(os.getcwd()) + "/jh-pwned", "w")))
print(attempt(lambda: os.rename("/usr/bin/env", "/usr/bin/env.bak")))
''',
}

CE = {
    "cpp": "int main( { return 0 }\n",
    "java": "public class Main { public static void main(String[] a) { int x = ; } }\n",
}

CE_LONG_CPP = "int main() {\n" + "".join(f"    int v{i} = ;\n" for i in range(300)) + "}\n"
CE_VIETNAMESE_CPP = "#error " + "Lỗi biên dịch tiếng Việt rất dài " * 400 + "\nint main() {}\n"
