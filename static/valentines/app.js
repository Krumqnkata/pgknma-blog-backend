(() => {
    "use strict";

    const pad = (value) => String(Math.max(0, value)).padStart(2, "0");

    document.querySelectorAll("[data-countdown]").forEach((countdown) => {
        const target = new Date(countdown.dataset.countdown).getTime();
        if (!Number.isFinite(target)) {
            return;
        }
        const days = countdown.querySelector("[data-days]");
        const hours = countdown.querySelector("[data-hours]");
        const minutes = countdown.querySelector("[data-minutes]");
        const startedBeforeTarget = target > Date.now();
        let timer = null;

        const update = () => {
            const distance = Math.max(0, target - Date.now());
            const totalMinutes = Math.floor(distance / 60000);
            days.textContent = pad(Math.floor(totalMinutes / 1440));
            hours.textContent = pad(Math.floor((totalMinutes % 1440) / 60));
            minutes.textContent = pad(totalMinutes % 60);
            if (distance === 0) {
                if (timer !== null) {
                    window.clearInterval(timer);
                }
                if (startedBeforeTarget) {
                    window.setTimeout(() => window.location.reload(), 500);
                }
            }
        };

        update();
        if (target > Date.now()) {
            timer = window.setInterval(update, 30000);
        }
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
        const targetInputs = Array.from(
            composer.querySelectorAll('input[name="target_type"]')
        );
        const classField = composer.querySelector("[data-class-field]");
        const recipientField = composer.querySelector(
            "[data-recipient-field]"
        );
        const audienceSummary = composer.querySelector(
            "[data-audience-summary]"
        );
        const teacherCount = Number(composer.dataset.teacherCount || 0);
        let teacherClassOption = null;
        const fallbackRecipientOptions = recipientSelect.innerHTML;
        let studentsRequest = 0;

        const getTargetType = () => {
            const selected = targetInputs.find((input) => input.checked);
            return selected ? selected.value : "individual";
        };

        const updatePreview = () => {
            const targetType = getTargetType();
            const recipientOption = recipientSelect.options[recipientSelect.selectedIndex];
            const classOption = classSelect.options[classSelect.selectedIndex];
            if (targetType === "teachers") {
                previewRecipient.textContent = "всички учители";
            } else if (targetType === "class") {
                previewRecipient.textContent =
                    classOption && classOption.value
                        ? `целия ${classOption.textContent.toLowerCase()}`
                        : "цял клас";
            } else {
                previewRecipient.textContent =
                    recipientOption && recipientOption.value
                        ? recipientOption.textContent
                        : "някой специален";
            }
            previewContent.textContent =
                content.value.trim() || "Твоето послание ще изглежда така…";
            previewSignature.textContent = anonymous.checked
                ? "Анонимен приятел"
                : "От теб";
            charCount.textContent = `${content.value.length} / 500`;
        };

        const loadStudents = async () => {
            const requestId = ++studentsRequest;
            const targetType = getTargetType();
            const canSelectIndividualTeacher = targetType === "individual";
            if (canSelectIndividualTeacher && teacherCount > 0) {
                if (!teacherClassOption) {
                    teacherClassOption = new Option("Учители", "U");
                    classSelect.add(teacherClassOption);
                }
                if (classSelect.dataset.selected === "U") {
                    classSelect.value = "U";
                    classSelect.dataset.selected = "";
                }
            } else if (teacherClassOption) {
                if (classSelect.value === "U") {
                    classSelect.value = "";
                }
                teacherClassOption.remove();
                teacherClassOption = null;
            }
            const className =
                targetType === "teachers" ? "U" : classSelect.value;
            const selected = recipientSelect.dataset.selected;

            classField.hidden = targetType === "teachers";
            recipientField.hidden = targetType !== "individual";
            classSelect.disabled = targetType === "teachers";
            classSelect.required = targetType !== "teachers";
            recipientSelect.required = targetType === "individual";

            if (targetType === "teachers") {
                recipientSelect.disabled = true;
                audienceSummary.textContent =
                    teacherCount === 1
                        ? "Посланието ще бъде доставено на 1 учител."
                        : `Посланието ще бъде доставено на ${teacherCount} учители.`;
                updatePreview();
                return;
            }

            if (!className) {
                recipientSelect.disabled = targetType === "individual";
                if (targetType === "individual") {
                    recipientSelect.innerHTML =
                        '<option value="">Първо избери клас</option>';
                }
                audienceSummary.textContent =
                    targetType === "class"
                        ? "Избери клас за груповата пратка."
                        : "Избери клас и след това конкретен човек.";
                updatePreview();
                return;
            }

            recipientSelect.disabled = true;
            if (targetType === "individual") {
                recipientSelect.innerHTML =
                    '<option value="">Зареждане…</option>';
            }
            audienceSummary.textContent = "Проверяваме получателите…";

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
                if (requestId !== studentsRequest) {
                    return;
                }
                if (targetType === "individual") {
                    recipientSelect.innerHTML =
                        '<option value="">Избери получател</option>';
                    data.students.forEach((student) => {
                        const option = new Option(student.name, student.id);
                        option.selected = student.id === selected;
                        recipientSelect.add(option);
                    });
                    recipientSelect.disabled = false;
                    recipientSelect.dataset.selected = "";
                    if (className === "U") {
                        audienceSummary.textContent =
                            data.students.length === 1
                                ? "Има 1 наличен учител."
                                : `Има ${data.students.length} налични учители.`;
                    } else {
                        audienceSummary.textContent =
                            data.students.length === 1
                                ? "В класа има 1 наличен получател."
                                : `В класа има ${data.students.length} налични получатели.`;
                    }
                } else {
                    recipientSelect.disabled = true;
                    audienceSummary.textContent =
                        data.students.length === 1
                            ? "Посланието ще бъде доставено на 1 човек."
                            : `Посланието ще бъде доставено на ${data.students.length} души.`;
                }
                updatePreview();
            } catch (error) {
                if (requestId !== studentsRequest) {
                    return;
                }
                if (targetType === "individual") {
                    recipientSelect.innerHTML = fallbackRecipientOptions;
                    recipientSelect.disabled = false;
                }
                audienceSummary.textContent =
                    "Броят не можа да се зареди. Формулярът все още може да бъде изпратен.";
                console.error(error);
                updatePreview();
            }
        };

        classSelect.addEventListener("change", loadStudents);
        targetInputs.forEach((input) => {
            input.addEventListener("change", loadStudents);
        });
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
        loadStudents();
        updatePreview();
    }

    const editContent = document.querySelector(
        ".edit-panel textarea[name='content']"
    );
    const editCharCount = document.querySelector(
        ".edit-panel [data-char-count]"
    );
    if (editContent && editCharCount) {
        const updateEditCount = () => {
            editCharCount.textContent = `${editContent.value.length} / 500`;
        };
        editContent.addEventListener("input", updateEditCount);
        updateEditCount();
    }

    const copyComputedStyles = (source, clone) => {
        const computed = window.getComputedStyle(source);
        for (const property of computed) {
            clone.style.setProperty(
                property,
                computed.getPropertyValue(property),
                computed.getPropertyPriority(property)
            );
        }
        clone.style.setProperty("animation", "none", "important");
        clone.style.setProperty("transition", "none", "important");

        const sourceChildren = Array.from(source.children);
        const cloneChildren = Array.from(clone.children);
        sourceChildren.forEach((sourceChild, index) => {
            if (cloneChildren[index]) {
                copyComputedStyles(sourceChild, cloneChildren[index]);
            }
        });
    };

    const pseudoContent = (value) => {
        if (!value || value === "none" || value === "normal") {
            return "";
        }
        const quote = value[0];
        if (
            (quote === '"' || quote === "'") &&
            value[value.length - 1] === quote
        ) {
            return value
                .slice(1, -1)
                .replace(/\\(["'\\])/g, "$1");
        }
        return value;
    };

    const addPseudoElement = (source, clone, pseudo, prepend) => {
        const computed = window.getComputedStyle(source, pseudo);
        const content = pseudoContent(computed.content);
        if (!content || computed.display === "none") {
            return;
        }
        const generated = document.createElement("span");
        generated.setAttribute("aria-hidden", "true");
        generated.textContent = content;
        for (const property of computed) {
            generated.style.setProperty(
                property,
                computed.getPropertyValue(property),
                computed.getPropertyPriority(property)
            );
        }
        generated.style.setProperty("content", "normal", "important");
        generated.style.setProperty("animation", "none", "important");
        generated.style.setProperty("transition", "none", "important");
        if (prepend) {
            clone.insertBefore(generated, clone.firstChild);
        } else {
            clone.appendChild(generated);
        }
    };

    const addGeneratedContent = (source, clone) => {
        const sourceChildren = Array.from(source.children);
        const cloneChildren = Array.from(clone.children);
        sourceChildren.forEach((sourceChild, index) => {
            if (cloneChildren[index]) {
                addGeneratedContent(sourceChild, cloneChildren[index]);
            }
        });
        addPseudoElement(source, clone, "::before", true);
        addPseudoElement(source, clone, "::after", false);
    };

    const imageFromUrl = (url) =>
        new Promise((resolve, reject) => {
            const image = new Image();
            image.onload = () => resolve(image);
            image.onerror = () => reject(
                new Error("Card image could not be rendered")
            );
            image.src = url;
        });

    const canvasBlob = (canvas) =>
        new Promise((resolve, reject) => {
            canvas.toBlob((blob) => {
                if (blob) {
                    resolve(blob);
                } else {
                    reject(new Error("PNG could not be created"));
                }
            }, "image/png");
        });

    const createCardImage = async (card) => {
        const sourceRect = card.getBoundingClientRect();
        const width = Math.max(320, Math.ceil(sourceRect.width));
        const clone = card.cloneNode(true);

        copyComputedStyles(card, clone);
        addGeneratedContent(card, clone);
        clone
            .querySelectorAll("[data-download-exclude]")
            .forEach((element) => element.remove());
        clone.style.setProperty("width", `${width}px`, "important");
        clone.style.setProperty("height", "auto", "important");
        clone.style.setProperty("margin", "0", "important");
        clone.style.setProperty("transform", "none", "important");

        const measurementHost = document.createElement("div");
        measurementHost.style.cssText = [
            "position:fixed",
            "left:-100000px",
            "top:0",
            "visibility:hidden",
            "pointer-events:none",
            `width:${width}px`,
        ].join(";");
        measurementHost.appendChild(clone);
        document.body.appendChild(measurementHost);
        const height = Math.max(
            370,
            Math.ceil(clone.getBoundingClientRect().height)
        );

        const padding = 32;
        const exportWidth = width + padding * 2;
        const exportHeight = height + padding * 2;
        const exportRoot = document.createElement("div");
        exportRoot.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
        exportRoot.style.cssText = [
            `width:${width}px`,
            `height:${height}px`,
            `padding:${padding}px`,
            "box-sizing:content-box",
            "background:transparent",
        ].join(";");
        exportRoot.appendChild(clone);
        measurementHost.remove();

        const serialized = new XMLSerializer().serializeToString(exportRoot);
        const svg = [
            `<svg xmlns="http://www.w3.org/2000/svg" width="${exportWidth}" height="${exportHeight}" viewBox="0 0 ${exportWidth} ${exportHeight}">`,
            `<foreignObject width="100%" height="100%">${serialized}</foreignObject>`,
            "</svg>",
        ].join("");
        const svgUrl = URL.createObjectURL(
            new Blob([svg], { type: "image/svg+xml;charset=utf-8" })
        );

        try {
            const image = await imageFromUrl(svgUrl);
            const scale = 3;
            const canvas = document.createElement("canvas");
            canvas.width = exportWidth * scale;
            canvas.height = exportHeight * scale;
            const context = canvas.getContext("2d");
            if (!context) {
                throw new Error("Canvas is unavailable");
            }
            context.scale(scale, scale);
            context.drawImage(image, 0, 0, exportWidth, exportHeight);
            return await canvasBlob(canvas);
        } finally {
            URL.revokeObjectURL(svgUrl);
        }
    };

    const safeFilename = (value) => {
        const cleaned = (value || "valentine")
            .normalize("NFKD")
            .replace(/[^\w-]+/g, "-")
            .replace(/^-+|-+$/g, "");
        return `${cleaned || "valentine"}.png`;
    };

    document.querySelectorAll("[data-download-image]").forEach((button) => {
        button.addEventListener("click", async () => {
            const card = button.closest("[data-download-card]");
            if (!card) {
                return;
            }
            const originalText = button.textContent;
            button.disabled = true;
            button.setAttribute("aria-busy", "true");
            button.textContent = "Подготвям снимката…";
            try {
                const blob = await createCardImage(card);
                const downloadUrl = URL.createObjectURL(blob);
                const link = document.createElement("a");
                link.href = downloadUrl;
                link.download = safeFilename(button.dataset.filename);
                document.body.appendChild(link);
                link.click();
                link.remove();
                window.setTimeout(
                    () => URL.revokeObjectURL(downloadUrl),
                    1000
                );
            } catch (error) {
                console.error(error);
                window.alert(
                    "Снимката не можа да бъде създадена. Опитай отново."
                );
            } finally {
                button.disabled = false;
                button.removeAttribute("aria-busy");
                button.textContent = originalText;
            }
        });
    });

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
