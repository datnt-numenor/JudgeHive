// Đường đi ngắn nhất từ đỉnh 1 tới đỉnh n trên đồ thị vô hướng có trọng số (Dijkstra), O((n + m) log n).
#include <cstdio>
#include <functional>
#include <queue>
#include <utility>
#include <vector>

int main() {
    int n, m;
    if (std::scanf("%d %d", &n, &m) != 2) return 0;
    std::vector<std::vector<std::pair<int, long long>>> graph(n + 1);
    for (int i = 0; i < m; ++i) {
        int u, v;
        long long w;
        std::scanf("%d %d %lld", &u, &v, &w);
        graph[u].push_back({v, w});
        graph[v].push_back({u, w});
    }
    const long long INF = (1LL << 62);
    std::vector<long long> dist(n + 1, INF);
    std::priority_queue<std::pair<long long, int>, std::vector<std::pair<long long, int>>, std::greater<>> heap;
    dist[1] = 0;
    heap.push({0, 1});
    while (!heap.empty()) {
        auto [d, u] = heap.top();
        heap.pop();
        if (d > dist[u]) continue;  // bản ghi cũ
        for (auto [v, w] : graph[u]) {
            if (d + w < dist[v]) {
                dist[v] = d + w;
                heap.push({dist[v], v});
            }
        }
    }
    std::printf("%lld\n", dist[n] == INF ? -1LL : dist[n]);
    return 0;
}
