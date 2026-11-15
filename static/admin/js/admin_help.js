/*
 * The admin guide page:
 *  - search: hides sections without the words, opens and highlights the rest;
 *  - #guide-… in the address (from the „?“ button) opens that section.
 */
(function () {
    "use strict";

    function sections() {
        return Array.prototype.slice.call(document.querySelectorAll("details[id^='guide-']"));
    }

    function clearMarks(root) {
        Array.prototype.forEach.call(root.querySelectorAll("mark.ux-hit"), function (mark) {
            var parent = mark.parentNode;
            parent.replaceChild(document.createTextNode(mark.textContent), mark);
            parent.normalize();
        });
    }

    function markWords(root, words) {
        var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
            acceptNode: function (node) {
                var tag = node.parentNode && node.parentNode.nodeName;
                if (tag === "SCRIPT" || tag === "STYLE" || tag === "MARK") {
                    return NodeFilter.FILTER_REJECT;
                }
                return node.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
            },
        });
        var nodes = [];
        while (walker.nextNode()) {
            nodes.push(walker.currentNode);
        }
        var escaped = words.map(function (word) {
            return word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        });
        var pattern = new RegExp("(" + escaped.join("|") + ")", "gi");
        nodes.forEach(function (node) {
            var text = node.nodeValue;
            pattern.lastIndex = 0;
            if (!pattern.test(text)) {
                return;
            }
            var fragment = document.createDocumentFragment();
            text.split(pattern).forEach(function (part, index) {
                if (index % 2 === 1) {
                    var mark = document.createElement("mark");
                    mark.className = "ux-hit";
                    mark.textContent = part;
                    fragment.appendChild(mark);
                } else if (part) {
                    fragment.appendChild(document.createTextNode(part));
                }
            });
            node.parentNode.replaceChild(fragment, node);
        });
    }

    function search(input, status) {
        var words = input.value.toLowerCase().split(/\s+/).filter(function (word) {
            return word.length > 1;
        });
        var found = 0;
        sections().forEach(function (section) {
            clearMarks(section);
            if (!words.length) {
                section.hidden = false;
                return;
            }
            var text = section.textContent.toLowerCase();
            var matches = words.every(function (word) {
                return text.indexOf(word) !== -1;
            });
            section.hidden = !matches;
            if (matches) {
                found += 1;
                section.open = true;
                markWords(section, words);
            }
        });
        if (!words.length) {
            status.textContent = "Пишете думи, например „анкета“, „CSV“ или „парола“.";
        } else if (found) {
            status.textContent = "Намерени раздели: " + found + ".";
        } else {
            status.textContent = "Няма съвпадения. Опитайте с друга дума.";
        }
    }

    function openFromHash() {
        var id = window.location.hash.slice(1);
        if (!id || id.indexOf("guide-") !== 0) {
            return;
        }
        var section = document.getElementById(id);
        if (section && section.tagName === "DETAILS") {
            section.hidden = false;
            section.open = true;
            section.scrollIntoView({ block: "start" });
        }
    }

    function init() {
        var input = document.getElementById("ux-help-search-input");
        var status = document.getElementById("ux-help-search-status");
        if (input && status) {
            var timer = null;
            input.addEventListener("input", function () {
                window.clearTimeout(timer);
                timer = window.setTimeout(function () {
                    search(input, status);
                }, 150);
            });
            input.addEventListener("keydown", function (event) {
                if (event.key === "Escape") {
                    input.value = "";
                    search(input, status);
                }
            });
        }
        openFromHash();
        window.addEventListener("hashchange", openFromHash);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
}());
