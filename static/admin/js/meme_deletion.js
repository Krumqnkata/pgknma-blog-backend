(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-confirm-message]").forEach(function (button) {
            button.addEventListener("click", function (event) {
                var section = button.closest(".related-deletion-section");
                var hasSelection = section && section.querySelector(
                    'input[type="checkbox"]:checked'
                );

                if (!hasSelection) {
                    event.preventDefault();
                    window.alert("Първо изберете поне един запис.");
                    return;
                }

                if (!window.confirm(button.dataset.confirmMessage)) {
                    event.preventDefault();
                }
            });
        });
    });
}());
