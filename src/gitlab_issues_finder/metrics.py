"""轻量级进程内 metrics 收集。

设计目标：
  - 零外部依赖（不引入 prometheus_client 等）。
  - 计数器 / 直方图以 dict 形式维护。
  - ``/metrics`` 端点以 Prometheus 文本格式输出，便于任何抓取器消费。

不追求与 prometheus_client 完全兼容（指标命名、# HELP / # TYPE 注释都按
自己的简化规则）。需要完整 Prometheus 生态时，可替换为 prometheus_client。
"""

from __future__ import annotations

import threading
import time


class Metrics:
    """线程安全的小型指标容器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 计数器：标签 -> 值
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        # 直方图：仅记录总和 + 计数 + 最大值（足够做 P50 / P99 之外的简单观测）
        self._hist_sum: dict[str, float] = {}
        self._hist_count: dict[str, float] = {}
        self._hist_max: dict[str, float] = {}
        # 上次启动时间
        self._start_time = time.time()
        # 简单 gauge：标签 -> 值
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    # ---- 计数 ----
    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    # ---- 直方图 ----
    def observe(self, name: str, value: float) -> None:
        with self._lock:
            self._hist_sum[name] = self._hist_sum.get(name, 0.0) + value
            self._hist_count[name] = self._hist_count.get(name, 0.0) + 1
            if value > self._hist_max.get(name, float("-inf")):
                self._hist_max[name] = value

    # ---- gauge ----
    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._gauges[key] = value

    # ---- 渲染 ----
    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            # counters
            counter_names = sorted({n for n, _ in self._counters})
            for name in counter_names:
                lines.append(f"# TYPE {name} counter")
                for (n, labels), v in sorted(self._counters.items()):
                    if n != name:
                        continue
                    if labels:
                        label_str = ",".join(f'{k}="{v_}"' for k, v_ in labels)
                        lines.append(f"{n}{{{label_str}}} {v:g}")
                    else:
                        lines.append(f"{n} {v:g}")
            # gauges
            gauge_names = sorted({n for n, _ in self._gauges})
            for name in gauge_names:
                lines.append(f"# TYPE {name} gauge")
                for (n, labels), v in sorted(self._gauges.items()):
                    if n != name:
                        continue
                    if labels:
                        label_str = ",".join(f'{k}="{v_}"' for k, v_ in labels)
                        lines.append(f"{n}{{{label_str}}} {v:g}")
                    else:
                        lines.append(f"{n} {v:g}")
            # histograms (summary-style: count, sum, max)
            for name in sorted(self._hist_sum):
                lines.append(f"# TYPE {name} summary")
                lines.append(f"{name}_count {self._hist_count[name]:g}")
                lines.append(f"{name}_sum {self._hist_sum[name]:g}")
                lines.append(f"{name}_max {self._hist_max[name]:g}")
        # process metrics
        uptime = time.time() - self._start_time
        lines.append("# TYPE process_uptime_seconds gauge")
        lines.append(f"process_uptime_seconds {uptime:.2f}")
        return "\n".join(lines) + "\n"


# ---- 进程内单例 ----
_SINGLETON: Metrics | None = None
_LOCK = threading.Lock()


def get_metrics() -> Metrics:
    global _SINGLETON
    with _LOCK:
        if _SINGLETON is None:
            _SINGLETON = Metrics()
    return _SINGLETON


def reset_metrics() -> None:
    """测试辅助：重置单例。"""
    global _SINGLETON
    with _LOCK:
        _SINGLETON = None
