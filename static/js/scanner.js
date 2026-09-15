/* ── Invoice scanner ──────────────────────────────────────────────────────────
   A photo of a paper invoice, flattened the way a scanner would do it: the
   person drags four handles onto the corners of the sheet, and the page comes
   back as an upright rectangle with the paper brightened to white. Several
   pages can be scanned one after another; the server binds them into one PDF.

   The corners are placed by hand, not found automatically. Finding them takes
   a computer-vision library of several megabytes, downloaded onto a phone on
   mobile data before anything can be scanned -- and it still picks the wrong
   sheet on a cluttered desk. Four handles take a few seconds and are never
   wrong about which sheet is meant.

   Everything happens in the page, so the preview is exactly what gets saved,
   and only the flattened pages travel to the server, not the phone's full
   photo. It also means a format the server cannot read (an iPhone's HEIC) is
   fine: the browser decodes it and hands on a JPEG.

   The maths is exported on its own under Node, so it can be checked without a
   browser.
*/
(function (root, factory) {
    var api = factory(root);
    if (typeof module === "object" && module.exports) module.exports = api;
    else root.ZoneScan = api;
})(typeof window !== "undefined" ? window : this, function (root) {
    "use strict";

    // The longest side a page is worked at. A phone photo runs to 4000 pixels
    // and more; flattening that pixel by pixel is slow on a cheap phone, and
    // 2000 is plenty to read an invoice. The server keeps photos at about this.
    var MAX_SIDE = 2000;
    var MAX_PAGES = 20;          // the server holds the same limit
    var MAX_PDF_BYTES = 15 * 1024 * 1024;   // and this one
    var JPEG_QUALITY = 0.88;
    var EDGE_INSET = 0.01;       // how far the corners are pulled in before flattening

    // ── The maths ───────────────────────────────────────────────────────────

    // Solve an n×n linear system by Gaussian elimination with partial pivoting.
    // Null when it has no single answer -- three corners on one line.
    function solve(A, b) {
        var n = b.length;
        var M = A.map(function (row, i) { return row.concat([b[i]]); });
        for (var col = 0; col < n; col++) {
            var pivot = col;
            for (var r = col + 1; r < n; r++) {
                if (Math.abs(M[r][col]) > Math.abs(M[pivot][col])) pivot = r;
            }
            if (Math.abs(M[pivot][col]) < 1e-12) return null;
            var swap = M[col]; M[col] = M[pivot]; M[pivot] = swap;
            for (r = col + 1; r < n; r++) {
                var f = M[r][col] / M[col][col];
                for (var c = col; c <= n; c++) M[r][c] -= f * M[col][c];
            }
        }
        var x = new Array(n);
        for (var i = n - 1; i >= 0; i--) {
            var s = M[i][n];
            for (var j = i + 1; j < n; j++) s -= M[i][j] * x[j];
            x[i] = s / M[i][i];
        }
        return x;
    }

    // The perspective transform taking each of four points in `from` onto the
    // matching point in `to`, as the nine entries of a 3×3 matrix.
    function homography(from, to) {
        var A = [], b = [];
        for (var i = 0; i < 4; i++) {
            var u = from[i][0], v = from[i][1], x = to[i][0], y = to[i][1];
            A.push([u, v, 1, 0, 0, 0, -u * x, -v * x]); b.push(x);
            A.push([0, 0, 0, u, v, 1, -u * y, -v * y]); b.push(y);
        }
        var h = solve(A, b);
        return h ? h.concat([1]) : null;
    }

    function project(H, u, v) {
        var w = H[6] * u + H[7] * v + H[8];
        return [(H[0] * u + H[1] * v + H[2]) / w, (H[3] * u + H[4] * v + H[5]) / w];
    }

    function dist(a, b) {
        var dx = a[0] - b[0], dy = a[1] - b[1];
        return Math.sqrt(dx * dx + dy * dy);
    }

    // How big the flat page comes out. Each side takes the longer of the two
    // edges facing each other on the photo -- the one nearer the camera looks
    // longer and is the truer measure -- so a sheet photographed at a slant
    // comes back in its own proportions, not squashed.
    // quad: top-left, top-right, bottom-right, bottom-left.
    function outputSize(quad) {
        var w = Math.max(dist(quad[0], quad[1]), dist(quad[3], quad[2]));
        var h = Math.max(dist(quad[0], quad[3]), dist(quad[1], quad[2]));
        var s = Math.min(1, MAX_SIDE / Math.max(w, h, 1));
        return [Math.max(1, Math.round(w * s)), Math.max(1, Math.round(h * s))];
    }

    // Four corners that go round the sheet in order without crossing over. A
    // crossed shape would flatten into a mirrored tangle, so it cannot be kept.
    function isConvex(quad) {
        var sign = 0;
        for (var i = 0; i < 4; i++) {
            var a = quad[i], b = quad[(i + 1) % 4], c = quad[(i + 2) % 4];
            var z = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
            if (Math.abs(z) < 1e-6) return false;
            var s = z > 0 ? 1 : -1;
            if (sign && s !== sign) return false;
            sign = s;
        }
        return true;
    }

    // Flatten what lies inside `quad` on `src` into an upright rectangle. `src`
    // is anything shaped like ImageData. Each pixel of the result is looked up
    // where it falls on the photo and blended from its four neighbours. Whatever
    // falls outside the photo -- a corner placed beyond a torn edge -- comes out
    // white, as the paper would have been.
    function warp(src, quad) {
        var size = outputSize(quad), W = size[0], Ht = size[1];
        var out = new Uint8ClampedArray(W * Ht * 4);
        var H = homography([[0, 0], [W - 1, 0], [W - 1, Ht - 1], [0, Ht - 1]], quad);
        if (!H) { out.fill(255); return { width: W, height: Ht, data: out }; }
        var sw = src.width, sh = src.height, sd = src.data;
        for (var y = 0; y < Ht; y++) {
            for (var x = 0; x < W; x++) {
                var w = H[6] * x + H[7] * y + H[8];
                var fx = (H[0] * x + H[1] * y + H[2]) / w;
                var fy = (H[3] * x + H[4] * y + H[5]) / w;
                var o = (y * W + x) * 4;
                out[o + 3] = 255;
                if (!(fx >= 0 && fy >= 0 && fx <= sw - 1 && fy <= sh - 1)) {
                    out[o] = out[o + 1] = out[o + 2] = 255;
                    continue;
                }
                var x0 = fx | 0, y0 = fy | 0;
                var x1 = x0 < sw - 1 ? x0 + 1 : x0;
                var y1 = y0 < sh - 1 ? y0 + 1 : y0;
                var ax = fx - x0, ay = fy - y0;
                var i00 = (y0 * sw + x0) * 4, i10 = (y0 * sw + x1) * 4;
                var i01 = (y1 * sw + x0) * 4, i11 = (y1 * sw + x1) * 4;
                for (var ch = 0; ch < 3; ch++) {
                    var top = sd[i00 + ch] + (sd[i10 + ch] - sd[i00 + ch]) * ax;
                    var bot = sd[i01 + ch] + (sd[i11 + ch] - sd[i01 + ch]) * ax;
                    out[o + ch] = top + (bot - top) * ay;
                }
            }
        }
        return { width: W, height: Ht, data: out };
    }

    // Make a flattened page read like a scan: paper pushed to white, ink to
    // dark. The paper is most of a page, so its brightness is read from the top
    // end of the brightness spread rather than from the single brightest pixel,
    // which is usually a glare spot.
    //   "color"    -- keeps the stamp and the signature in their own colours
    //   "document" -- black on white, the sharpest to read and to print
    function enhance(img, mode) {
        var d = img.data, n = d.length / 4, hist = new Uint32Array(256), i;
        for (i = 0; i < d.length; i += 4) {
            hist[(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]) | 0]++;
        }
        function percentile(p) {
            var target = n * p, acc = 0;
            for (var k = 0; k < 256; k++) {
                acc += hist[k];
                if (acc >= target) return k;
            }
            return 255;
        }
        var black = percentile(0.02), white = percentile(0.90);
        var out = new Uint8ClampedArray(d.length);
        // A page with almost no contrast (blank, or badly overexposed) is left
        // as it is: stretching nothing only turns noise into grain.
        if (white - black < 24) {
            out.set(d);
            return { width: img.width, height: img.height, data: out };
        }
        var scale = 255 / (white - black);
        for (i = 0; i < d.length; i += 4) {
            out[i + 3] = 255;
            if (mode === "document") {
                var lum = (0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2] - black) * scale;
                lum = lum <= 0 ? 0 : lum >= 255 ? 255 : 255 * Math.pow(lum / 255, 1.35);
                out[i] = out[i + 1] = out[i + 2] = lum;
            } else {
                out[i] = (d[i] - black) * scale;
                out[i + 1] = (d[i + 1] - black) * scale;
                out[i + 2] = (d[i + 2] - black) * scale;
            }
        }
        return { width: img.width, height: img.height, data: out };
    }

    // Where the handles start: a little inside the photo's edges, so each one is
    // plainly visible and easy to grab, never lost against the border.
    function defaultQuad(w, h) {
        var mx = w * 0.08, my = h * 0.08;
        return [[mx, my], [w - mx, my], [w - mx, h - my], [mx, h - my]];
    }

    // Pull the four corners a hair towards the middle before flattening. A
    // corner placed with a thumb lands a few pixels off, and the sheet's own
    // edge on the photo is blurred into the desk: taken exactly, that edge
    // comes out as a dark ragged line all round the scan. A fraction of a
    // percent inside, the page loses nothing anyone reads and the line is gone.
    function insetQuad(quad, fraction) {
        var cx = 0, cy = 0;
        quad.forEach(function (p) { cx += p[0] / 4; cy += p[1] / 4; });
        return quad.map(function (p) {
            return [p[0] + (cx - p[0]) * fraction, p[1] + (cy - p[1]) * fraction];
        });
    }

    var api = {
        solve: solve, homography: homography, project: project,
        outputSize: outputSize, isConvex: isConvex, warp: warp,
        enhance: enhance, defaultQuad: defaultQuad, insetQuad: insetQuad,
        EDGE_INSET: EDGE_INSET,
        MAX_SIDE: MAX_SIDE, MAX_PAGES: MAX_PAGES, MAX_PDF_BYTES: MAX_PDF_BYTES
    };

    if (!root || !root.document) return api;   // under Node: the maths only

    // ── The screen ──────────────────────────────────────────────────────────
    var doc = root.document;
    var SVG = "http://www.w3.org/2000/svg";

    function el(tag, cls, text) {
        var e = doc.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }

    function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }

    // A canvas to a JPEG file, synchronously. The asynchronous toBlob would be
    // simpler, but "add a page" has to open the camera straight away, and a
    // browser only lets a page open the camera from inside the click itself.
    function canvasToFile(canvas, name) {
        var url = canvas.toDataURL("image/jpeg", JPEG_QUALITY);
        var bin = root.atob(url.split(",")[1]);
        var bytes = new Uint8Array(bin.length);
        for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        return new File([bytes], name, { type: "image/jpeg" });
    }

    // A photo, drawn onto a canvas no bigger than MAX_SIDE. The browser applies
    // the photo's own rotation flag when it decodes it, so a picture taken with
    // the phone held sideways arrives the right way up.
    function loadPhoto(file, done) {
        var url = URL.createObjectURL(file), img = new Image();
        img.onload = function () {
            var s = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
            var c = doc.createElement("canvas");
            c.width = Math.max(1, Math.round(img.naturalWidth * s));
            c.height = Math.max(1, Math.round(img.naturalHeight * s));
            c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
            URL.revokeObjectURL(url);
            done(null, c);
        };
        img.onerror = function () { URL.revokeObjectURL(url); done(new Error("unreadable")); };
        img.src = url;
    }

    // Pages scanned into a form that comes back with an error -- a missing
    // amount, say -- must not be lost: the modal redraws the form, and scanning
    // three pages again because of an empty field is exactly what drives people
    // away. So the pages are kept here, against the form they belong to, until
    // the form is cancelled or saved (saving reloads the page, which clears it).
    var stash = null;
    doc.addEventListener("click", function (e) {
        if (e.target.closest && e.target.closest("[data-modal-cancel], .modal__close")) stash = null;
    }, true);
    doc.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !doc.querySelector(".scan-editor")) stash = null;
    });

    function Scanner(field) {
        var L = field.dataset;                   // the words, written by the template
        var form = field.closest("form");
        var action = form ? form.getAttribute("action") : "";
        var camera = field.querySelector("[data-scan-camera]");
        var output = field.querySelector("[data-scan-output]");
        var list = field.querySelector("[data-scan-pages]");
        var addLabel = field.querySelector("[data-scan-add-label]");
        var removeFlag = field.querySelector("[data-scan-remove]");
        var current = field.querySelector("[data-scan-current]");
        var replaceNote = field.querySelector("[data-scan-replace-note]");
        var errorBox = field.querySelector("[data-scan-error]");
        var pdfInput = field.querySelector("[data-scan-pdf]");
        var pdfChip = field.querySelector("[data-scan-pdf-chosen]");

        // A scan and a PDF are two ways of giving the same document. Whichever
        // was chosen last is the one kept, and the other is let go.
        var kept = (stash && stash.action === action) ? stash : null;
        var pages = kept ? kept.pages : [];
        var pdf = kept ? kept.pdf : null;
        stash = { action: action, pages: pages, pdf: pdf };

        function showError(msg) {
            if (!errorBox) return;
            errorBox.textContent = msg || "";
            errorBox.hidden = !msg;
        }

        // The pages ride in an ordinary file field, so neither the form nor the
        // modal that sends it needs to know anything about scanning.
        function sync() {
            try {
                var dt = new DataTransfer();
                pages.forEach(function (p) { dt.items.add(p.file); });
                output.files = dt.files;
                if (pdfInput) {
                    var dp = new DataTransfer();
                    if (pdf) dp.items.add(pdf);
                    pdfInput.files = dp.files;
                }
            } catch (err) {
                showError(L.lUnsupported);
            }
            if (stash && stash.pages === pages) stash.pdf = pdf;
            render();
        }

        function render() {
            list.innerHTML = "";
            pages.forEach(function (p, i) {
                var item = el("div", "scan-thumb");
                var img = el("img");
                img.src = p.url;
                img.alt = L.lPage + " " + (i + 1);
                var num = el("span", "scan-thumb__num", String(i + 1));
                var rm = el("button", "scan-thumb__remove", "×");
                rm.type = "button";
                rm.title = L.lRemovePage;
                rm.setAttribute("aria-label", L.lRemovePage + " " + (i + 1));
                rm.addEventListener("click", function () {
                    URL.revokeObjectURL(p.url);
                    pages.splice(i, 1);
                    sync();
                });
                item.appendChild(img);
                item.appendChild(num);
                item.appendChild(rm);
                list.appendChild(item);
            });
            addLabel.textContent = pages.length ? L.lAddPage : L.lStart;

            if (pdfChip) {
                pdfChip.innerHTML = "";
                pdfChip.hidden = !pdf;
                if (pdf) {
                    pdfChip.appendChild(el("span", "scan-pdf-chosen__name", pdf.name));
                    var drop = el("button", "scan-thumb__remove", "×");
                    drop.type = "button";
                    drop.title = L.lRemovePdf;
                    drop.setAttribute("aria-label", L.lRemovePdf);
                    drop.addEventListener("click", function () { pdf = null; sync(); });
                    pdfChip.appendChild(drop);
                }
            }
            if (replaceNote) {
                replaceNote.hidden = !((pages.length || pdf) && current && !current.hidden);
            }
        }

        if (pdfInput) {
            pdfInput.addEventListener("change", function () {
                var file = pdfInput.files && pdfInput.files[0];
                if (!file) { sync(); return; }
                showError("");
                if (!(file.type === "application/pdf" || /\.pdf$/i.test(file.name))) {
                    pdf = null; sync(); showError(L.lNotPdf); return;
                }
                if (file.size > MAX_PDF_BYTES) {
                    pdf = null; sync(); showError(L.lTooBig); return;
                }
                pages.forEach(function (p) { URL.revokeObjectURL(p.url); });
                pages.length = 0;
                pdf = file;
                sync();
            });
        }

        var removeCurrent = field.querySelector("[data-scan-remove-current]");
        if (removeCurrent) {
            removeCurrent.addEventListener("click", function () {
                removeFlag.value = "1";
                current.hidden = true;
                render();
            });
        }

        camera.addEventListener("change", function () {
            var file = camera.files && camera.files[0];
            camera.value = "";                  // so the same photo can be picked again
            if (!file) return;
            showError("");
            if (pages.length >= MAX_PAGES) { showError(L.lTooMany); return; }
            openEditor(file);
        });

        function openEditor(file) {
            var ov = el("div", "scan-editor");
            ov.setAttribute("role", "dialog");
            ov.setAttribute("aria-modal", "true");
            var head = el("div", "scan-editor__head",
                          L.lTitle + " — " + L.lPage + " " + (pages.length + 1));
            var stage = el("div", "scan-editor__stage");
            var tip = el("div", "scan-editor__tip", L.lWorking);
            var foot = el("div", "scan-editor__foot");
            ov.appendChild(head);
            ov.appendChild(stage);
            ov.appendChild(tip);
            ov.appendChild(foot);
            doc.body.appendChild(ov);
            doc.documentElement.classList.add("scan-open");

            // Escape leaves the scan only. Left to itself it would reach the
            // modal underneath and close the whole form.
            function onKey(e) {
                if (e.key !== "Escape") return;
                e.stopPropagation();
                e.preventDefault();
                close();
            }
            root.addEventListener("keydown", onKey, true);

            function close() {
                root.removeEventListener("keydown", onKey, true);
                if (ov.parentNode) ov.parentNode.removeChild(ov);
                doc.documentElement.classList.remove("scan-open");
            }

            function button(label, cls, fn) {
                var b = el("button", "btn " + cls, label);
                b.type = "button";
                b.addEventListener("click", fn);
                return b;
            }

            loadPhoto(file, function (err, work) {
                if (err) { close(); showError(L.lBadPhoto); return; }
                corners(work, null);
            });

            // Step one: four handles on the photo, dragged onto the sheet.
            function corners(work, quad) {
                stage.innerHTML = "";
                foot.innerHTML = "";
                tip.textContent = L.lCorners;
                quad = quad || defaultQuad(work.width, work.height);

                var box = el("div", "scan-editor__box");
                work.className = "scan-editor__photo";
                var svg = doc.createElementNS(SVG, "svg");
                svg.setAttribute("class", "scan-editor__lines");
                svg.setAttribute("viewBox", "0 0 " + work.width + " " + work.height);
                svg.setAttribute("preserveAspectRatio", "none");
                var poly = doc.createElementNS(SVG, "polygon");
                svg.appendChild(poly);
                box.appendChild(work);
                box.appendChild(svg);

                var ok = button(L.lStraighten, "btn--primary", function () {
                    if (isConvex(quad)) flatten(work, quad);
                });

                var handles = quad.map(function (pt, i) {
                    var h = el("div", "scan-handle");
                    h.setAttribute("data-corner", String(i));
                    h.setAttribute("aria-label", L.lCorners);
                    box.appendChild(h);
                    h.addEventListener("pointerdown", function (e) {
                        e.preventDefault();
                        if (h.setPointerCapture) h.setPointerCapture(e.pointerId);
                        h.classList.add("is-dragging");
                        function move(ev) {
                            var r = work.getBoundingClientRect();
                            quad[i] = [
                                clamp((ev.clientX - r.left) / r.width * work.width, 0, work.width - 1),
                                clamp((ev.clientY - r.top) / r.height * work.height, 0, work.height - 1)
                            ];
                            place();
                        }
                        function up() {
                            h.classList.remove("is-dragging");
                            h.removeEventListener("pointermove", move);
                            h.removeEventListener("pointerup", up);
                            h.removeEventListener("pointercancel", up);
                        }
                        h.addEventListener("pointermove", move);
                        h.addEventListener("pointerup", up);
                        h.addEventListener("pointercancel", up);
                    });
                    return h;
                });

                function place() {
                    poly.setAttribute("points", quad.map(function (p) {
                        return p[0] + "," + p[1];
                    }).join(" "));
                    handles.forEach(function (h, i) {
                        h.style.left = (quad[i][0] / work.width * 100) + "%";
                        h.style.top = (quad[i][1] / work.height * 100) + "%";
                    });
                    // Crossed corners cannot flatten into a page; the button
                    // says so by refusing rather than by an error afterwards.
                    var good = isConvex(quad);
                    ok.disabled = !good;
                    box.classList.toggle("is-crossed", !good);
                }

                stage.appendChild(box);
                foot.appendChild(button(L.lCancel, "btn--outlined", close));
                foot.appendChild(button(L.lRetake, "btn--outlined", function () {
                    close();
                    camera.click();
                }));
                foot.appendChild(ok);
                place();
            }

            // Step two: the flat page, with a choice of colour or black and white.
            function flatten(work, quad) {
                stage.innerHTML = "";
                foot.innerHTML = "";
                tip.textContent = L.lWorking;
                // Let "working" paint before the long loop takes the thread.
                root.setTimeout(function () {
                    var src = work.getContext("2d").getImageData(0, 0, work.width, work.height);
                    var flat = warp(src, insetQuad(quad, EDGE_INSET));
                    var mode = "color";
                    var canvas = el("canvas", "scan-editor__photo");
                    canvas.width = flat.width;
                    canvas.height = flat.height;

                    var modes = el("div", "scan-editor__modes");
                    var colorBtn = button(L.lColor, "btn--sm btn--tonal", function () { mode = "color"; paint(); });
                    var docBtn = button(L.lDocument, "btn--sm btn--tonal", function () { mode = "document"; paint(); });
                    colorBtn.setAttribute("data-mode", "color");
                    docBtn.setAttribute("data-mode", "document");
                    modes.appendChild(colorBtn);
                    modes.appendChild(docBtn);

                    function paint() {
                        var e2 = enhance(flat, mode);
                        canvas.getContext("2d").putImageData(new ImageData(e2.data, e2.width, e2.height), 0, 0);
                        colorBtn.classList.toggle("is-on", mode === "color");
                        docBtn.classList.toggle("is-on", mode === "document");
                    }

                    function keep() {
                        var file = canvasToFile(canvas, "page-" + (pages.length + 1) + ".jpg");
                        pdf = null;          // scanning after choosing a PDF means the scan
                        pages.push({ file: file, url: URL.createObjectURL(file) });
                        sync();
                        close();
                    }

                    stage.appendChild(canvas);
                    tip.textContent = "";
                    tip.appendChild(modes);
                    paint();

                    foot.appendChild(button(L.lRedo, "btn--outlined", function () { corners(work, quad); }));
                    foot.appendChild(button(L.lAddPage, "btn--outlined", function () {
                        keep();
                        if (pages.length < MAX_PAGES) camera.click();
                    }));
                    foot.appendChild(button(L.lFinish, "btn--primary", keep));
                }, 30);
            }
        }

        sync();   // draws the pages kept from before, if the form came back
    }

    function bindAll() {
        doc.querySelectorAll("[data-scanner]").forEach(function (f) {
            if (f.dataset.scanBound) return;
            f.dataset.scanBound = "1";
            Scanner(f);
        });
    }

    bindAll();
    // The form usually arrives in the modal after the page has loaded.
    new MutationObserver(function (records) {
        for (var i = 0; i < records.length; i++) {
            if (records[i].addedNodes.length) { bindAll(); return; }
        }
    }).observe(doc.body, { childList: true, subtree: true });

    return api;
});
