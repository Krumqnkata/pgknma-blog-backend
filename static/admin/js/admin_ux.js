/*
 * Small helpers for every admin page.
 *
 * Lists as cards on a phone: each cell gets the name of its column in
 * data-label, and admin_ux.css shows that name next to the value when the
 * screen is narrow, so nothing is hidden off to the side.
 */
(function () {
    "use strict";

    function labelListCells() {
        var table = document.getElementById("result_list");
        if (!table || !table.tHead) {
            return;
        }
        var headers = Array.prototype.map.call(table.tHead.rows[0].cells, function (th) {
            var text = th.querySelector(".text");
            return (text ? text.textContent : "").replace(/\s+/g, " ").trim();
        });
        Array.prototype.forEach.call(table.tBodies, function (body) {
            Array.prototype.forEach.call(body.rows, function (row) {
                Array.prototype.forEach.call(row.cells, function (cell, index) {
                    var label = headers[index] || "";
                    if (label && !cell.hasAttribute("data-label")) {
                        cell.setAttribute("data-label", label);
                    }
                });
            });
        });
        table.classList.add("ux-labelled");
    }

    /*
     * Forms with tabs: an inline marked with the classes „ux-into-tab“ and
     * „ux-tab-<tab name>“ is shown only while that tab is open, right under
     * it. If it has errors, that tab opens and is marked red. Without
     * JavaScript the inline simply stays visible under the tabs.
     *
     * The inline is not moved into the tab: moving it confuses Unfold's
     * Alpine.js and the tabs stop switching.
     */
    function bindInlinesToTabs() {
        Array.prototype.forEach.call(document.querySelectorAll("fieldset.ux-into-tab"), function (fieldset) {
            var tabClass = Array.prototype.find.call(fieldset.classList, function (name) {
                return name.indexOf("ux-tab-") === 0;
            });
            var group = fieldset.parentElement;
            if (!tabClass || !group) {
                return;
            }
            var slug = tabClass.slice("ux-tab-".length);
            var wrappers = Array.prototype.slice.call(document.querySelectorAll(".tab-wrapper"));
            var index = wrappers.findIndex(function (wrapper) {
                return wrapper.classList.contains("fieldset-" + slug);
            });
            if (index === -1) {
                return;
            }
            var links = wrappers[index].parentElement.querySelectorAll("nav > a");
            var link = links[index];
            if (!link) {
                return;
            }

            function sync() {
                // Unfold marks the open tab link with the class „active“.
                group.hidden = !link.classList.contains("active");
            }

            group.classList.add("ux-inline-in-tab");
            new MutationObserver(sync).observe(link, { attributes: true, attributeFilter: ["class"] });
            sync();

            if (group.querySelector(".errorlist, .errors")) {
                link.classList.add("ux-tab-has-errors");
                window.setTimeout(function () {
                    link.click();
                }, 0);
            }
        });
    }

    /*
     * Plain file fields show the browser's own „Choose Files / No file
     * chosen“ in the browser's language. They get a Bulgarian button and
     * text; the real field stays in its place (hidden), so forms, the
     * gallery drop zone and keyboard use keep working. Unfold's own file
     * widgets (inside .opacity-0) are left alone.
     */
    function fileStatusText(input) {
        var files = input.files || [];
        if (!files.length) {
            return input.multiple ? "Няма избрани файлове" : "Няма избран файл";
        }
        if (files.length === 1) {
            return files[0].name;
        }
        return "Избрани файлове: " + files.length;
    }

    function bulgarianFilePickers(root) {
        Array.prototype.forEach.call((root || document).querySelectorAll("input[type=file]"), function (input) {
            if (input.dataset.uxFile || input.closest(".opacity-0, [hidden]") || input.name.indexOf("__prefix__") !== -1) {
                return;
            }
            if (window.getComputedStyle(input).display === "none") {
                return;
            }
            input.dataset.uxFile = "true";

            var picker = document.createElement("span");
            picker.className = "ux-file";
            var button = document.createElement("button");
            button.type = "button";
            button.className = "ux-file-button";
            button.textContent = input.multiple ? "Изберете файлове" : "Изберете файл";
            var status = document.createElement("span");
            status.className = "ux-file-status";
            status.setAttribute("aria-live", "polite");
            status.textContent = fileStatusText(input);
            picker.appendChild(button);
            picker.appendChild(status);

            input.parentNode.insertBefore(picker, input);
            input.classList.add("ux-file-native");
            input.tabIndex = -1;
            if (input.id) {
                button.setAttribute("aria-describedby", input.id);
            }
            button.addEventListener("click", function () {
                input.click();
            });
            input.addEventListener("change", function () {
                status.textContent = fileStatusText(input);
            });
        });
    }

    function init() {
        labelListCells();
        bindInlinesToTabs();
        bulgarianFilePickers();
    }

    // A row added with „Нова …“ under a form brings new file fields.
    document.addEventListener("formset:added", function (event) {
        bulgarianFilePickers(event.target);
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
}());
