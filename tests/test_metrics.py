"""metrics.py 单元测试。

覆盖: 计数器 / 直方图 / gauge 的线程安全、render 输出格式、
get_metrics 单例模式、reset_metrics 测试辅助。
"""

from __future__ import annotations

import threading

import pytest

from gitlab_issues_finder.metrics import Metrics, get_metrics, reset_metrics


@pytest.fixture(autouse=True)
def _reset():
    """每个测试前重置单例, 避免串测。"""
    reset_metrics()
    yield
    reset_metrics()


class TestCounter:
    def test_inc_default_value(self):
        m = Metrics()
        m.inc("requests")
        m.inc("requests")
        assert m._counters[("requests", ())] == 2.0

    def test_inc_with_labels(self):
        m = Metrics()
        m.inc("http_requests", method="GET", path="/")
        m.inc("http_requests", method="GET", path="/")
        m.inc("http_requests", method="POST", path="/")
        key = ("http_requests", (("method", "GET"), ("path", "/")))
        assert m._counters[key] == 2.0

    def test_inc_thread_safety(self):
        m = Metrics()
        n = 1000

        def hammer():
            for _ in range(n):
                m.inc("hits")

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert m._counters[("hits", ())] == 4 * n


class TestHistogram:
    def test_observe_records_count_sum_max(self):
        m = Metrics()
        for v in [1.0, 2.0, 5.0, 3.0]:
            m.observe("latency", v)
        assert m._hist_count["latency"] == 4
        assert m._hist_sum["latency"] == 11.0
        assert m._hist_max["latency"] == 5.0

    def test_observe_ignores_lower_values(self):
        m = Metrics()
        m.observe("x", 10.0)
        m.observe("x", 5.0)
        assert m._hist_max["x"] == 10.0


class TestGauge:
    def test_set_gauge(self):
        m = Metrics()
        m.set_gauge("active_users", 42)
        m.set_gauge("active_users", 100)  # 覆盖
        key = ("active_users", ())
        assert m._gauges[key] == 100


class TestRender:
    def test_render_counter_format(self):
        m = Metrics()
        m.inc("requests", method="GET", path="/api")
        out = m.render()
        assert "# TYPE requests counter" in out
        assert "requests{method=\"GET\",path=\"/api\"} 1" in out

    def test_render_histogram_format(self):
        m = Metrics()
        m.observe("latency", 0.5)
        m.observe("latency", 1.5)
        out = m.render()
        assert "# TYPE latency summary" in out
        assert "latency_count 2" in out
        assert "latency_sum 2" in out
        assert "latency_max 1.5" in out

    def test_render_includes_uptime(self):
        m = Metrics()
        out = m.render()
        assert "# TYPE process_uptime_seconds gauge" in out
        assert "process_uptime_seconds " in out

    def test_render_empty(self):
        """空 metrics 仍应输出合法的 Prometheus 文本 (只有 process_uptime)。"""
        m = Metrics()
        out = m.render()
        # 至少有 uptime
        assert "process_uptime_seconds" in out


class TestSingleton:
    def test_get_metrics_returns_same_instance(self):
        a = get_metrics()
        b = get_metrics()
        assert a is b

    def test_reset_metrics_clears_singleton(self):
        a = get_metrics()
        reset_metrics()
        b = get_metrics()
        assert a is not b
