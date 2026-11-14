# Đường đi ngắn nhất từ đỉnh 1 tới đỉnh n (Dijkstra với hàng đợi ưu tiên), O((n + m) log n).
import heapq
import sys

data = sys.stdin.buffer.read().split()
n, m = int(data[0]), int(data[1])
graph = [[] for _ in range(n + 1)]
for i in range(m):
    u, v, w = int(data[2 + 3 * i]), int(data[3 + 3 * i]), int(data[4 + 3 * i])
    graph[u].append((v, w))
    graph[v].append((u, w))  # đồ thị vô hướng

INF = float("inf")
dist = [INF] * (n + 1)
dist[1] = 0
heap = [(0, 1)]
while heap:
    d, u = heapq.heappop(heap)
    if d > dist[u]:
        continue  # bản ghi cũ, đỉnh đã được chốt với khoảng cách nhỏ hơn
    for v, w in graph[u]:
        if d + w < dist[v]:
            dist[v] = d + w
            heapq.heappush(heap, (dist[v], v))
print(dist[n] if dist[n] != INF else -1)
