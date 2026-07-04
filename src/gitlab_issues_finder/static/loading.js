
// static/loading.js
//
// Loading overlay + top progress bar.
//
// 用法 (任选其一即可):
//   - <a href="..." data-loading-text="正在打开...">点我</a>
//   - <button data-loading-text="保存中...">提交</button>
//   - <form data-loading-text="查询中...">...</form>
//
// 行为:
//   - 点击 / 提交时显示遮罩与顶部进度条, 并使用 data-loading-text 作为文案。
//   - 页面 pageshow 事件触发时自动收起 (覆盖浏览器前进/后退)。
//   - 浏览器原生表单验证失败时不会触发显示 (form.checkValidity())。
//   - 提供 window.showLoading(text) / window.hideLoading() 供程序化调用。
//
// 设计原则: DOM 已存在时立即接管; 不依赖任何外部库; CSS 主题变量复用。

(function () {
    "use strict";

    function ready(fn) {
        if (document.readyState !== "loading") {
            fn();
        } else {
            document.addEventListener("DOMContentLoaded", fn);
        }
    }

    ready(function () {
        const overlay = document.getElementById("loading-overlay");
        const bar = document.getElementById("loading-bar");
        if (!overlay && !bar) {
            return;
        }

        const textEl = overlay ? overlay.querySelector(".loading-text") : null;

        function show(text) {
            if (overlay) {
                if (text && textEl) textEl.textContent = text;
                overlay.classList.add("visible");
            }
            if (bar) {
                bar.classList.add("indeterminate");
            }
        }

        function hide() {
            if (overlay) overlay.classList.remove("visible");
            if (bar) bar.classList.remove("indeterminate");
        }

        window.showLoading = show;
        window.hideLoading = hide;

        window.addEventListener("pageshow", hide);
        window.addEventListener("pagehide", hide);

        document.addEventListener("click", function (e) {
            const target = e.target.closest("[data-loading-text]");
            if (!target) return;
            if (target.hasAttribute("disabled") || target.getAttribute("aria-disabled") === "true") return;
            if (target.tagName === "A" && target.target === "_blank") return;
            show(target.getAttribute("data-loading-text") || "加载中…");
        }, true);

        document.addEventListener("submit", function (e) {
            const form = e.target;
            if (!(form instanceof HTMLFormElement)) return;
            const text = form.getAttribute("data-loading-text");
            if (!text) return;
            if (typeof form.checkValidity === "function" && !form.checkValidity()) return;
            show(text);
        }, true);
    });
})();
