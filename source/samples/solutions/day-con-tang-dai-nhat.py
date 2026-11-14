# Dãy con tăng ngặt dài nhất, O(n log n).
# tails[k] = phần tử cuối nhỏ nhất của một dãy con tăng độ dài k + 1; tìm vị trí bằng chia đôi.
import sys
from bisect import bisect_left

data = sys.stdin.buffer.read().split()
n = int(data[0])
tails = []
for token in data[1:1 + n]:
    x = int(token)
    i = bisect_left(tails, x)
    if i == len(tails):
        tails.append(x)
    else:
        tails[i] = x
print(len(tails))
