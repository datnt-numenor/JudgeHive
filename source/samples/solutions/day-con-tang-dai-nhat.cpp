// Dãy con tăng ngặt dài nhất, O(n log n): tails[k] là phần tử cuối nhỏ nhất của dãy tăng độ dài k + 1.
#include <algorithm>
#include <cstdio>
#include <vector>

int main() {
    int n;
    if (std::scanf("%d", &n) != 1) return 0;
    std::vector<int> tails;
    for (int i = 0; i < n; ++i) {
        int x;
        std::scanf("%d", &x);
        auto it = std::lower_bound(tails.begin(), tails.end(), x);
        if (it == tails.end()) tails.push_back(x);
        else *it = x;
    }
    std::printf("%zu\n", tails.size());
    return 0;
}
