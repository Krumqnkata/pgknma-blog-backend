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

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", labelListCells);
    } else {
        labelListCells();
    }
}());
