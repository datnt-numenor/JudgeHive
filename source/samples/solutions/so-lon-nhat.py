# Số lớn nhất trong dãy: duyệt một lượt, O(n).
import sys

data = sys.stdin.buffer.read().split()
n = int(data[0])
print(max(map(int, data[1:1 + n])))
