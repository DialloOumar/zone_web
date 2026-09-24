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

// ── Workspace menu (drawer top) ──
// Opens on its button, closes on a click elsewhere or Escape.
(function () {
    const btn = document.getElementById("ws-switch-btn");
    const menu = document.getElementById("ws-switch-menu");
    if (!btn || !menu) return;

    function setOpen(open) {
        menu.hidden = !open;
        btn.setAttribute("aria-expanded", open ? "true" : "false");
    }
    btn.addEventListener("click", function (e) {
        e.stopPropagation();
        setOpen(menu.hidden);
    });
    document.addEventListener("click", function (e) {
        if (!menu.hidden && !menu.contains(e.target)) setOpen(false);
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !menu.hidden) { setOpen(false); btn.focus(); }
    });
})();

// ── Filtering a list of tick boxes as you type ──
// <input data-filter-list="#list"> keeps only the list's children whose text
// contains what was typed. A ticked item always stays: what you chose must
// not vanish because you narrowed the list to find the next one. Delegated,
// so a form loaded into a modal later is covered too.
(function () {
    function apply(input) {
        var list = document.querySelector(input.getAttribute("data-filter-list"));
        if (!list) return;
        var needle = input.value.trim().toLowerCase();
        Array.prototype.forEach.call(list.children, function (item) {
            var box = item.querySelector('input[type="checkbox"]');
            var keep = !needle || (box && box.checked) ||
                       item.textContent.toLowerCase().indexOf(needle) !== -1;
            item.hidden = !keep;
        });
    }
    document.addEventListener("input", function (e) {
        var input = e.target;
        if (input && input.matches && input.matches("[data-filter-list]")) apply(input);
    });
    document.addEventListener("change", function (e) {
        // Unticking inside a narrowed list: the item may now be filtered out.
        var box = e.target;
        if (!box || box.type !== "checkbox") return;
        var input = document.querySelector("[data-filter-list]");
        if (input && input.value) apply(input);
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

// ── Flash toasts ──
// The messages laid over the page go on their own: a success after four
// seconds, an error or a warning after eight, so there is time to read it.
// A click on one dismisses it at once. They are outside the page's flow, so
// nothing moves when they go.
(function () {
    function leave(el) {
        if (el.dataset.leaving) return;
        el.dataset.leaving = "1";
        el.classList.add("is-leaving");
        setTimeout(function () { el.remove(); }, 400);
    }
    document.querySelectorAll(".flash-area .alert").forEach(function (el) {
        var wait = el.classList.contains("alert--success") ? 4000 : 8000;
        setTimeout(function () { leave(el); }, wait);
        el.addEventListener("click", function () { leave(el); });
    });
    // A success confirmed inside a page (a saved form) fades the same way.
    document.querySelectorAll(".alert--success").forEach(function (el) {
        if (el.closest(".flash-area")) return;
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
            // Inline SVG, not a Material Symbols ligature: the icon font is
            // subset to the names found in the templates, so this JS-injected
            // one was silently dropped and the button rendered empty. Drawing
            // the X here keeps the only way out of a modal independent of the
            // font loading, being subset correctly, or being cached.
            '<button class="modal__close" type="button" aria-label="Close">' +
            '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" ' +
            'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
            '<path d="M6 6l12 12M18 6L6 18"/></svg></button></div>' +
            '<div class="modal__body"><div class="modal__loading">…</div></div></div>';
        overlay.querySelector(".modal__header h2").textContent = title || "";
        document.body.appendChild(overlay);
        requestAnimationFrame(function () { overlay.classList.add("open"); });
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

// ── Styled confirm dialog (replaces the native confirm) ──
// Any <form data-confirm="message"> shows a styled dialog before submitting.
// Optional: data-confirm-title, data-confirm-ok (button label),
// data-confirm-danger (red confirm button). Exposed as window.confirmDialog.
(function () {
    function dialog(opts) {
        return new Promise(function (resolve) {
            var ov = document.createElement("div");
            ov.className = "modal-overlay confirm-overlay";
            ov.innerHTML =
                '<div class="modal modal--confirm" role="alertdialog" aria-modal="true">' +
                  '<div class="confirm__body">' +
                    '<h2 class="confirm__title"></h2>' +
                    '<p class="confirm__msg"></p>' +
                  '</div>' +
                  '<div class="confirm__actions">' +
                    '<button type="button" class="btn btn--outlined" data-act="cancel"></button>' +
                    '<button type="button" class="btn" data-act="ok"></button>' +
                  '</div>' +
                '</div>';
            var titleEl = ov.querySelector(".confirm__title");
            if (opts.title) { titleEl.textContent = opts.title; } else { titleEl.remove(); }
            ov.querySelector(".confirm__msg").textContent = opts.message || "";
            var ok = ov.querySelector("[data-act=ok]");
            ok.textContent = opts.ok || "OK";
            ok.classList.add(opts.danger ? "btn--danger" : "btn--primary");
            var cancel = ov.querySelector("[data-act=cancel]");
            cancel.textContent = opts.cancel || (window.ZW && window.ZW.cancel) || "Cancel";

            document.body.appendChild(ov);
            requestAnimationFrame(function () { ov.classList.add("open"); });

            function done(v) {
                document.removeEventListener("keydown", onKey);
                ov.classList.remove("open");
                setTimeout(function () { ov.remove(); }, 200);
                resolve(v);
            }
            function onKey(e) {
                if (e.key === "Escape") done(false);
                else if (e.key === "Enter") { e.preventDefault(); done(true); }
            }
            ov.addEventListener("click", function (e) { if (e.target === ov) done(false); });
            cancel.addEventListener("click", function () { done(false); });
            ok.addEventListener("click", function () { done(true); });
            document.addEventListener("keydown", onKey);
            setTimeout(function () { ok.focus(); }, 40);
        });
    }
    window.confirmDialog = dialog;

    function optsFrom(el) {
        return {
            title: el.getAttribute("data-confirm-title") || "",
            message: el.getAttribute("data-confirm"),
            ok: el.getAttribute("data-confirm-ok") || "OK",
            danger: el.hasAttribute("data-confirm-danger")
        };
    }

    // Whole-form guard: <form data-confirm>. form.submit() bypasses this.
    document.addEventListener("submit", function (e) {
        var form = e.target;
        if (!form || !form.hasAttribute || !form.hasAttribute("data-confirm")) return;
        e.preventDefault();
        e.stopPropagation();
        dialog(optsFrom(form)).then(function (ok) { if (ok) form.submit(); });
    }, true);

    // Per-control guard: a specific <button data-confirm> (e.g. one of several
    // submit buttons) or an <a data-confirm> link. On confirm we replay the
    // original action, keeping the button's name/value as the form submitter.
    document.addEventListener("click", function (e) {
        var el = e.target.closest("button[data-confirm], a[data-confirm]");
        if (!el || el.dataset.confirmed === "1") return;
        e.preventDefault();
        e.stopPropagation();
        dialog(optsFrom(el)).then(function (ok) {
            if (!ok) return;
            el.dataset.confirmed = "1";
            if (el.tagName === "A") window.location = el.getAttribute("href");
            else el.click();
        });
    }, true);
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

// ── Photo picker (shared by any form using templates/_photo_field.html) ──
// Instant local preview, client-side compression before upload (kind to slow
// field connections), and a remove button. Everything is scoped to the
// enclosing .photo-input so several pickers can coexist and survive modal
// injection. See templates/_photo_field.html.
function _photoBox(el) { return el.closest(".photo-input"); }

window.onPhotoPick = function (input) {
    var box = _photoBox(input);
    if (!box) return;
    var removeFlag = box.querySelector('input[name="photo_remove"]');
    if (removeFlag) removeFlag.value = "";   // a fresh pick cancels a pending removal
    var file = input.files && input.files[0];
    if (!file) return;
    var preview = box.querySelector(".photo-preview");
    if (preview) {
        preview.innerHTML =
            '<div class="photo-preview__inner">' +
                '<img src="' + URL.createObjectURL(file) + '" alt="">' +
                '<button type="button" class="photo-preview__remove" data-photo-remove>&times;</button>' +
            '</div>';
    }
    // Compress in the background; swap the input's file if the result is smaller.
    if (window.compressImage) {
        window.compressImage(file, { maxDim: 1600, quality: 0.82 }).then(function (out) {
            if (!out || out === file || out.size >= file.size || typeof DataTransfer === "undefined") return;
            try {
                var dt = new DataTransfer();
                dt.items.add(out instanceof File ? out :
                    new File([out], "photo.jpg", { type: "image/jpeg" }));
                input.files = dt.files;
            } catch (e) { /* keep the original file */ }
        }).catch(function () {});
    }
};

// Remove button (existing or freshly picked photo), delegated for modals.
document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-photo-remove]");
    if (!btn) return;
    e.preventDefault();
    var box = _photoBox(btn);
    if (!box) return;
    var removeFlag = box.querySelector('input[name="photo_remove"]');
    var fileInput = box.querySelector('input[type="file"]');
    var preview = box.querySelector(".photo-preview");
    if (fileInput) fileInput.value = "";
    if (removeFlag) removeFlag.value = "1";   // tell the server to drop the stored photo
    if (preview) preview.innerHTML = "";
});

// ── Filter menus ──
// The bar is sent by its Filtrer button, not by each tick: a filter is usually
// several choices, and firing on every one asked the server for pages nobody
// had finished asking for. What is left here is the behaviour a <details> does
// not give on its own -- closing when you click away from it, and opening the
// date picker from the whole pill rather than the few pixels of its icon.
(function () {
    document.querySelectorAll("form.filter-bar--live label.filter-date").forEach(
        function (pill) {
            pill.addEventListener("click", function (e) {
                var input = pill.querySelector('input[type="date"]');
                // A click on the numbers is left alone: that is how a date is
                // typed at the keyboard.
                if (!input || e.target === input) return;
                // showPicker() is recent, and can refuse. When it does, the
                // browser's own icon stays the way in.
                if (typeof input.showPicker !== "function") return;
                e.preventDefault();
                try { input.showPicker(); } catch (err) { input.focus(); }
            });
        });

    // A menu left open would sit over the page it is filtering.
    document.addEventListener("click", function (e) {
        document.querySelectorAll(".filter-bar--live details.filter-dd[open]").forEach(
            function (dd) { if (!dd.contains(e.target)) dd.open = false; });
    });
    document.addEventListener("keydown", function (e) {
        if (e.key !== "Escape") return;
        document.querySelectorAll(".filter-bar--live details.filter-dd[open]").forEach(
            function (dd) { dd.open = false; });
    });
})();

// ── Night mode button ──
// The server knows the theme only once someone has chosen one. Until then the
// head script sets it from the machine, and this brings the button into line --
// otherwise a reader whose computer is already dark would be offered "switch to
// dark", and clicking it would appear to do nothing.
(function () {
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;
    var dark = document.documentElement.getAttribute("data-theme") === "dark";
    var icon = btn.querySelector(".material-symbols-outlined");
    var label = btn.getAttribute(dark ? "data-label-light" : "data-label-dark");
    btn.href = btn.getAttribute(dark ? "data-to-light" : "data-to-dark");
    if (icon) icon.textContent = dark ? "light_mode" : "dark_mode";
    if (label) {
        btn.title = label;
        btn.setAttribute("aria-label", label);
    }
})();

// ── Money fields ──
// Amounts here run to seven and eight digits. Typed bare, 5000000 and 50000000
// are the same shape to the eye, and one gets keyed for the other. So a money
// field groups itself in threes while it is being filled in, exactly as the
// figures elsewhere on the page are grouped.
//
// The separator is a no-break space, and every reader of a money field on the
// server takes it back out again (parse_amount), so what is typed and what is
// saved never disagree. A field with no JavaScript running still works: bare
// digits are read the same way.
(function () {
    var NBSP = "\u00a0";

    function group(digits) {
        // From the right, because that is where the grouping starts.
        var out = "", n = 0;
        for (var i = digits.length - 1; i >= 0; i--) {
            out = digits[i] + out;
            if (++n % 3 === 0 && i > 0) out = NBSP + out;
        }
        return out;
    }

    function format(el) {
        var before = el.value;
        // Where the caret sits, counted in digits rather than characters: the
        // separators move about, the digits do not.
        var caret = el.selectionStart === null ? before.length : el.selectionStart;
        var digitsBefore = before.slice(0, caret).replace(/\D/g, "").length;

        var digits = before.replace(/\D/g, "").replace(/^0+(?=\d)/, "");
        var after = group(digits);
        if (after === before) return;
        el.value = after;

        // Put the caret back where the same digit is now.
        if (el.selectionStart === null) return;
        var seen = 0, pos = after.length;
        for (var i = 0; i < after.length; i++) {
            if (seen === digitsBefore) { pos = i; break; }
            if (/\d/.test(after[i])) seen++;
        }
        if (seen === digitsBefore && pos === after.length) pos = after.length;
        try { el.setSelectionRange(pos, pos); } catch (err) { /* not a text field */ }
    }

    function bind(root) {
        (root || document).querySelectorAll("input[data-money]").forEach(function (el) {
            if (el.dataset.moneyBound) return;
            el.dataset.moneyBound = "1";
            el.addEventListener("input", function () { format(el); });
            el.addEventListener("blur", function () { format(el); });
            format(el);   // whatever the server put there, grouped too
        });
    }

    bind(document);
    // A form can arrive after the page has: the modal fetches one and drops it
    // in. Watching for added nodes catches those without the modal code having
    // to know this exists.
    new MutationObserver(function (records) {
        for (var i = 0; i < records.length; i++) {
            if (records[i].addedNodes.length) { bind(document); return; }
        }
    }).observe(document.body, { childList: true, subtree: true });
})();

console.log("Zone Web booted");
