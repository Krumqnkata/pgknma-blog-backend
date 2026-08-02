(() => {
    "use strict";

    const formatSize = (bytes) => {
        if (bytes < 1024 * 1024) {
            return `${Math.max(1, Math.round(bytes / 1024))} KB`;
        }
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    };

    const setupGalleryUpload = () => {
        const input = document.querySelector(
            'input[type="file"][data-gallery-upload="true"]'
        );
        if (!input || input.dataset.galleryReady === "true") return;
        input.dataset.galleryReady = "true";

        const maxFiles = Number(input.dataset.maxFiles || 10);
        const maxFileSize = Number(input.dataset.maxFileSize || 0);
        let objectUrls = [];

        const panel = document.createElement("section");
        panel.setAttribute("aria-live", "polite");
        panel.style.cssText = [
            "border:1px dashed #94a3b8",
            "border-radius:12px",
            "padding:14px",
            "margin-top:10px",
            "background:rgba(148,163,184,.08)",
        ].join(";");

        const instruction = document.createElement("p");
        instruction.textContent =
            `Пуснете снимките тук или ги изберете от полето ` +
            `(до ${maxFiles} наведнъж` +
            `${maxFileSize ? `, по ${formatSize(maxFileSize)}` : ""}).`;
        instruction.style.cssText = "margin:0 0 10px;font-weight:600";

        const summary = document.createElement("p");
        summary.style.cssText = "margin:0;color:#64748b;font-size:13px";
        summary.textContent = "Все още няма избрани нови снимки.";

        const previews = document.createElement("div");
        previews.style.cssText = [
            "display:grid",
            "grid-template-columns:repeat(auto-fill,minmax(120px,1fr))",
            "gap:10px",
            "margin-top:12px",
        ].join(";");

        const progressBox = document.createElement("div");
        progressBox.hidden = true;
        progressBox.style.cssText = "margin-top:12px";
        const progressText = document.createElement("p");
        progressText.textContent = "Качване и записване на галерията…";
        progressText.style.cssText = "margin:0 0 6px;font-weight:600";
        const progress = document.createElement("progress");
        progress.removeAttribute("value");
        progress.style.cssText = "width:100%;height:12px";
        progressBox.append(progressText, progress);

        panel.append(instruction, summary, previews, progressBox);
        input.insertAdjacentElement("afterend", panel);

        const clearObjectUrls = () => {
            objectUrls.forEach((url) => URL.revokeObjectURL(url));
            objectUrls = [];
        };

        const render = () => {
            clearObjectUrls();
            previews.replaceChildren();
            const files = Array.from(input.files || []);
            const totalSize = files.reduce((sum, file) => sum + file.size, 0);
            const tooMany = files.length > maxFiles;
            const oversizedFile = maxFileSize
                ? files.find((file) => file.size > maxFileSize)
                : null;

            input.setCustomValidity(
                tooMany
                    ? `Може да качите най-много ${maxFiles} изображения наведнъж.`
                    : oversizedFile
                        ? `Снимката „${oversizedFile.name}“ надвишава лимита от ${formatSize(maxFileSize)}.`
                    : ""
            );
            summary.textContent = files.length
                ? `${files.length} избрани изображения · общо ${formatSize(totalSize)}`
                : "Все още няма избрани нови снимки.";
            summary.style.color = tooMany || oversizedFile ? "#dc2626" : "#64748b";

            files.forEach((file) => {
                const figure = document.createElement("figure");
                figure.style.cssText = "margin:0;min-width:0";
                const image = document.createElement("img");
                const url = URL.createObjectURL(file);
                objectUrls.push(url);
                image.src = url;
                image.alt = "";
                image.style.cssText = [
                    "display:block",
                    "width:100%",
                    "aspect-ratio:1",
                    "object-fit:cover",
                    "border-radius:9px",
                    "background:#e2e8f0",
                ].join(";");
                const caption = document.createElement("figcaption");
                caption.textContent = `${file.name} · ${formatSize(file.size)}`;
                caption.title = file.name;
                caption.style.cssText = [
                    "margin-top:5px",
                    "font-size:12px",
                    "white-space:nowrap",
                    "overflow:hidden",
                    "text-overflow:ellipsis",
                ].join(";");
                figure.append(image, caption);
                previews.append(figure);
            });
        };

        ["dragenter", "dragover"].forEach((eventName) => {
            panel.addEventListener(eventName, (event) => {
                event.preventDefault();
                panel.style.borderColor = "#2563eb";
                panel.style.background = "rgba(37,99,235,.08)";
            });
        });
        ["dragleave", "drop"].forEach((eventName) => {
            panel.addEventListener(eventName, (event) => {
                event.preventDefault();
                panel.style.borderColor = "#94a3b8";
                panel.style.background = "rgba(148,163,184,.08)";
            });
        });
        panel.addEventListener("drop", (event) => {
            if (!event.dataTransfer?.files?.length) return;
            try {
                input.files = event.dataTransfer.files;
                input.dispatchEvent(new Event("change", { bubbles: true }));
            } catch (_error) {
                summary.textContent =
                    "Браузърът не позволи директното пускане. Използвайте полето за избор.";
                summary.style.color = "#dc2626";
            }
        });

        input.addEventListener("change", render);
        input.closest("form")?.addEventListener("submit", () => {
            if (input.files?.length && input.checkValidity()) {
                progressBox.hidden = false;
                panel.setAttribute("aria-busy", "true");
            }
        });
        window.addEventListener("beforeunload", clearObjectUrls, { once: true });
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", setupGalleryUpload);
    } else {
        setupGalleryUpload();
    }
})();
