(() => {
    "use strict";

    const pad = (value) => String(Math.max(0, value)).padStart(2, "0");

    document.querySelectorAll("[data-countdown]").forEach((countdown) => {
        const target = new Date(countdown.dataset.countdown).getTime();
        const days = countdown.querySelector("[data-days]");
        const hours = countdown.querySelector("[data-hours]");
        const minutes = countdown.querySelector("[data-minutes]");

        const update = () => {
            const distance = Math.max(0, target - Date.now());
            const totalMinutes = Math.floor(distance / 60000);
            days.textContent = pad(Math.floor(totalMinutes / 1440));
            hours.textContent = pad(Math.floor((totalMinutes % 1440) / 60));
            minutes.textContent = pad(totalMinutes % 60);
            if (distance === 0) {
                window.clearInterval(timer);
            }
        };

        update();
        const timer = window.setInterval(update, 30000);
    });

    const composer = document.querySelector(".composer-form");
    if (composer) {
        const classSelect = composer.querySelector("#recipient-class");
        const recipientSelect = composer.querySelector("#recipient");
        const content = composer.querySelector("#id_content");
        const anonymous = composer.querySelector('input[name="is_anonymous"]');
        const preview = document.querySelector("[data-card-preview]");
        const previewRecipient = document.querySelector("[data-preview-recipient]");
        const previewContent = document.querySelector("[data-preview-content]");
        const previewSignature = document.querySelector("[data-preview-signature]");
        const charCount = composer.querySelector("[data-char-count]");
        const studentsUrl = composer.dataset.studentsUrl;

        const updatePreview = () => {
            const recipientOption = recipientSelect.options[recipientSelect.selectedIndex];
            previewRecipient.textContent =
                recipientOption && recipientOption.value
                    ? recipientOption.textContent
                    : "някой специален";
            previewContent.textContent =
                content.value.trim() || "Твоето послание ще изглежда така…";
            previewSignature.textContent = anonymous.checked
                ? "Анонимен приятел"
                : "От теб";
            charCount.textContent = `${content.value.length} / 500`;
        };

        const loadStudents = async () => {
            const className = classSelect.value;
            const selected = recipientSelect.dataset.selected;
            recipientSelect.disabled = true;
            recipientSelect.innerHTML = '<option value="">Зареждане…</option>';

            if (!className) {
                recipientSelect.innerHTML =
                    '<option value="">Първо избери клас</option>';
                updatePreview();
                return;
            }

            try {
                const url = studentsUrl.replace(
                    "__CLASS__",
                    encodeURIComponent(className)
                );
                const response = await fetch(url, {
                    headers: { Accept: "application/json" },
                    credentials: "same-origin",
                });
                if (!response.ok) {
                    throw new Error("Students request failed");
                }
                const data = await response.json();
                recipientSelect.innerHTML =
                    '<option value="">Избери получател</option>';
                data.students.forEach((student) => {
                    const option = new Option(student.name, student.id);
                    option.selected = student.id === selected;
                    recipientSelect.add(option);
                });
                recipientSelect.disabled = false;
                recipientSelect.dataset.selected = "";
                updatePreview();
            } catch (error) {
                recipientSelect.innerHTML =
                    '<option value="">Неуспешно зареждане</option>';
                console.error(error);
            }
        };

        classSelect.addEventListener("change", loadStudents);
        recipientSelect.addEventListener("change", updatePreview);
        content.addEventListener("input", updatePreview);
        anonymous.addEventListener("change", updatePreview);

        composer
            .querySelectorAll('input[name="theme"]')
            .forEach((themeInput) => {
                themeInput.addEventListener("change", () => {
                    preview.className = `valentine-preview theme-${themeInput.value}`;
                });
            });

        const selectedTheme = composer.querySelector(
            'input[name="theme"]:checked'
        );
        if (selectedTheme) {
            preview.className = `valentine-preview theme-${selectedTheme.value}`;
        }
        if (classSelect.value) {
            loadStudents();
        }
        updatePreview();
    }

    document.querySelectorAll(".report-details form").forEach((form) => {
        form.addEventListener("submit", (event) => {
            const confirmed = window.confirm(
                "Съобщението ще бъде скрито и изпратено на модератор. Да продължим?"
            );
            if (!confirmed) {
                event.preventDefault();
            }
        });
    });

    document.querySelectorAll(".toast").forEach((toast, index) => {
        window.setTimeout(() => {
            toast.classList.add("toast-leaving");
            window.setTimeout(() => toast.remove(), 350);
        }, 4500 + index * 300);
    });
})();
