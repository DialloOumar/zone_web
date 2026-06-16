// Zone Web — main.js
// Shared front-end helpers. Domain pages may extend these.

// ── Mobile sidebar toggle ──
(function () {
    const toggle = document.getElementById("menu-toggle");
    const sidebar = document.querySelector(".sidebar");
    const overlay = document.getElementById("sidebar-overlay");
    if (!toggle || !sidebar || !overlay) return;

    function openSidebar() {
        sidebar.classList.add("open");
        overlay.classList.add("open");
    }
    function closeSidebar() {
        sidebar.classList.remove("open");
        overlay.classList.remove("open");
    }

    toggle.addEventListener("click", openSidebar);
    overlay.addEventListener("click", closeSidebar);
    sidebar.querySelectorAll("a").forEach(function (a) {
        a.addEventListener("click", closeSidebar);
    });
})();

// ── Material ripple ──
// Adds a touch ripple to interactive surfaces. Elements need
// position:relative + overflow:hidden (handled in CSS).
(function () {
    function spawn(el, e) {
        var rect = el.getBoundingClientRect();
        var size = Math.max(rect.width, rect.height);
        var x = (e.clientX || rect.left + rect.width / 2) - rect.left - size / 2;
        var y = (e.clientY || rect.top + rect.height / 2) - rect.top - size / 2;
        var r = document.createElement("span");
        r.className = "ripple";
        r.style.width = r.style.height = size + "px";
        r.style.left = x + "px";
        r.style.top = y + "px";
        el.appendChild(r);
        r.addEventListener("animationend", function () { r.remove(); });
    }
    var sel = ".btn, .fab, .sidebar-nav a, .checkbox-item, .logout-btn";
    document.addEventListener("click", function (e) {
        var el = e.target.closest(sel);
        if (el) spawn(el, e);
    });
})();

// ── Auto-dismiss success snackbars ──
(function () {
    document.querySelectorAll(".alert--success").forEach(function (el) {
        setTimeout(function () {
            el.style.transition = "opacity 0.4s";
            el.style.opacity = "0";
            setTimeout(function () { el.remove(); }, 400);
        }, 4000);
    });
})();

// ── Modal dialog layer ──
// Any element with [data-modal-url] opens its target in a dialog instead of
// navigating. The URL returns a bare form partial; submitting it POSTs via
// fetch — JSON success reloads the page (showing the flash + fresh list),
// an HTML response (validation error) is re-injected with inline errors.
// Links keep their href, so without JS everything still works as full pages.
(function () {
    var overlay = null;

    function close() {
        if (!overlay) return;
        document.removeEventListener("keydown", onKey);
        var o = overlay;
        overlay = null;
        o.classList.remove("open");
        setTimeout(function () { o.remove(); }, 200);
    }
    function onKey(e) { if (e.key === "Escape") close(); }

    function build(title) {
        overlay = document.createElement("div");
        overlay.className = "modal-overlay";
        overlay.innerHTML =
            '<div class="modal" role="dialog" aria-modal="true">' +
            '<div class="modal__header"><h2></h2>' +
            '<button class="modal__close" type="button" aria-label="Close">' +
            '<span class="material-symbols-outlined">close</span></button></div>' +
            '<div class="modal__body"><div class="modal__loading">…</div></div></div>';
        overlay.querySelector(".modal__header h2").textContent = title || "";
        document.body.appendChild(overlay);
        requestAnimationFrame(function () { overlay.classList.add("open"); });
        overlay.addEventListener("click", function (e) { if (e.target === overlay) close(); });
        overlay.querySelector(".modal__close").addEventListener("click", close);
        document.addEventListener("keydown", onKey);
    }

    function runScripts(container) {
        container.querySelectorAll("script").forEach(function (old) {
            var s = document.createElement("script");
            s.textContent = old.textContent;
            old.replaceWith(s);
        });
    }

    function bind(body) {
        var form = body.querySelector("form");
        if (!form) return;
        var cancel = body.querySelector("[data-modal-cancel]");
        if (cancel) cancel.addEventListener("click", function (e) { e.preventDefault(); close(); });
        form.addEventListener("submit", function (e) {
            e.preventDefault();
            var btn = form.querySelector("button[type=submit]");
            if (btn) btn.disabled = true;
            fetch(form.action, {
                method: "POST",
                body: new FormData(form),
                headers: { "X-Requested-With": "fetch" }
            }).then(function (r) {
                var ct = r.headers.get("content-type") || "";
                if (ct.indexOf("application/json") >= 0) {
                    return r.json().then(function () { window.location.reload(); });
                }
                return r.text().then(function (html) { inject(html); });
            }).catch(function () { if (btn) btn.disabled = false; });
        });
    }

    function inject(html) {
        if (!overlay) return;
        var body = overlay.querySelector(".modal__body");
        body.innerHTML = html;
        runScripts(body);
        bind(body);
    }

    function open(url, title) {
        build(title);
        fetch(url, { headers: { "X-Requested-With": "fetch" } })
            .then(function (r) { return r.text(); })
            .then(function (html) { inject(html); })
            .catch(function () { close(); window.location = url; });
    }

    document.addEventListener("click", function (e) {
        var trg = e.target.closest("[data-modal-url]");
        if (!trg) return;
        e.preventDefault();
        open(trg.getAttribute("data-modal-url"), trg.getAttribute("data-modal-title"));
    });
})();

// ── Image compression (port from batmex_web) ──
// Used by photo inputs to shrink phone photos before upload so uploads
// still succeed on slow field connections. Output is JPEG ~250 KB from a
// typical 4 MB iPhone shot. Falls back to the original if anything fails.
window.compressImage = function (file, opts) {
    opts = opts || {};
    var maxDim = opts.maxDim || 1600;
    var quality = opts.quality || 0.82;
    var mime = "image/jpeg";

    return new Promise(function (resolve) {
        if (!file || !file.type || file.type.indexOf("image/") !== 0) {
            resolve(file);
            return;
        }
        var url = URL.createObjectURL(file);
        var img = new Image();
        img.onload = function () {
            var w = img.naturalWidth;
            var h = img.naturalHeight;
            var longest = Math.max(w, h);
            if (longest > maxDim) {
                var scale = maxDim / longest;
                w = Math.round(w * scale);
                h = Math.round(h * scale);
            }
            var canvas = document.createElement("canvas");
            canvas.width = w;
            canvas.height = h;
            var ctx = canvas.getContext("2d");
            ctx.fillStyle = "#fff";
            ctx.fillRect(0, 0, w, h);
            ctx.drawImage(img, 0, 0, w, h);
            URL.revokeObjectURL(url);
            canvas.toBlob(function (blob) {
                if (!blob || blob.size >= file.size) { resolve(file); return; }
                var name = (file.name || "photo").replace(/\.[^.]+$/, "") + ".jpg";
                try {
                    resolve(new File([blob], name, { type: mime, lastModified: Date.now() }));
                } catch (e) {
                    resolve(blob);
                }
            }, mime, quality);
        };
        img.onerror = function () {
            URL.revokeObjectURL(url);
            resolve(file);
        };
        img.src = url;
    });
};

console.log("Zone Web booted");
