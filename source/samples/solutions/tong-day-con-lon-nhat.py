# Dãy con liên tiếp có tổng lớn nhất (thuật toán Kadane), O(n).
# best_here: tổng lớn nhất của dãy con kết thúc tại phần tử đang xét.
import sys

data = sys.stdin.buffer.read().split()
n = int(data[0])
best = best_here = int(data[1])
for token in data[2:1 + n]:
    x = int(token)
    best_here = max(x, best_here + x)
    best = max(best, best_here)
print(best)
