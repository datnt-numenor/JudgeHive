# Đếm số nguyên tố không vượt quá N (sàng Eratosthenes), O(N log log N).
import sys

n = int(sys.stdin.readline())
if n < 2:
    print(0)
else:
    sieve = bytearray([1]) * (n + 1)
    sieve[0] = sieve[1] = 0
    p = 2
    while p * p <= n:
        if sieve[p]:
            # Gạch bội của p bắt đầu từ p*p; các bội nhỏ hơn đã bị số nguyên tố nhỏ hơn gạch.
            sieve[p * p::p] = bytes(len(range(p * p, n + 1, p)))
        p += 1
    print(sum(sieve))
