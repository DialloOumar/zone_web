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
