(() => {
    "use strict";

    const actions = [
        ["post_read", "Прочетена публикация"],
        ["poll_vote", "Гласуване в анкета"],
        ["calendar_visit", "Посещение на училищния календар"],
        ["comment", "Публикуван валиден коментар"],
    ];

    const createElement = (tag, className, text) => {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text) element.textContent = text;
        return element;
    };

    const createLabel = (text, control) => {
        const label = createElement("label", "mission-requirements__field");
        label.append(createElement("span", "mission-requirements__label", text));
        label.append(control);
        return label;
    };

    const uniqueKey = (rows, action) => {
        const existing = new Set(rows.map((row) => String(row.key || "")));
        const base = action || "goal";
        if (!existing.has(base)) return base;
        let number = 2;
        while (existing.has(`${base}_${number}`)) number += 1;
        return `${base}_${number}`;
    };

    const setupEditor = (textarea) => {
        if (textarea.dataset.missionRequirementsReady === "true") return;

        let rows;
        try {
            rows = JSON.parse(textarea.value || "[]");
        } catch (_error) {
            return;
        }
        if (!Array.isArray(rows)) return;

        textarea.dataset.missionRequirementsReady = "true";
        textarea.hidden = true;

        const editor = createElement("section", "mission-requirements");
        editor.setAttribute("aria-label", "Условия на мисията");

        const toolbar = createElement("div", "mission-requirements__toolbar");
        const summary = createElement("p", "mission-requirements__summary");
        const addButton = createElement(
            "button",
            "mission-requirements__add",
            "+ Добави условие"
        );
        addButton.type = "button";
        toolbar.append(summary, addButton);

        const list = createElement("div", "mission-requirements__list");
        const hint = createElement(
            "p",
            "mission-requirements__hint",
            "Всяко условие следи едно действие и колко пъти трябва да бъде извършено."
        );
        editor.append(toolbar, hint, list);
        textarea.insertAdjacentElement("afterend", editor);

        const sync = () => {
            textarea.value = JSON.stringify(rows);
            summary.textContent = rows.length === 1
                ? "1 условие"
                : `${rows.length} условия`;
        };

        const render = () => {
            list.replaceChildren();

            if (!rows.length) {
                list.append(createElement(
                    "p",
                    "mission-requirements__empty",
                    "Все още няма добавени условия."
                ));
            }

            rows.forEach((row, index) => {
                const card = createElement("article", "mission-requirements__card");
                const number = createElement(
                    "span",
                    "mission-requirements__number",
                    `Условие ${index + 1}`
                );

                const labelInput = document.createElement("input");
                labelInput.type = "text";
                labelInput.value = String(row.label || "");
                labelInput.placeholder = "Напр. Прочети 3 различни публикации";
                labelInput.addEventListener("input", () => {
                    row.label = labelInput.value;
                    sync();
                });

                const actionSelect = document.createElement("select");
                const emptyOption = document.createElement("option");
                emptyOption.value = "";
                emptyOption.textContent = "Изберете действие";
                actionSelect.append(emptyOption);
                actions.forEach(([value, text]) => {
                    const option = document.createElement("option");
                    option.value = value;
                    option.textContent = text;
                    option.selected = row.action === value;
                    actionSelect.append(option);
                });
                actionSelect.addEventListener("change", () => {
                    row.action = actionSelect.value;
                    if (!row.key) row.key = uniqueKey(rows, row.action);
                    keyInput.value = row.key;
                    sync();
                });

                const targetInput = document.createElement("input");
                targetInput.type = "number";
                targetInput.min = "1";
                targetInput.max = "10000";
                targetInput.step = "1";
                targetInput.value = String(row.target || 1);
                targetInput.addEventListener("input", () => {
                    row.target = Number(targetInput.value);
                    sync();
                });

                const removeButton = createElement(
                    "button",
                    "mission-requirements__remove",
                    "Премахни"
                );
                removeButton.type = "button";
                removeButton.setAttribute("aria-label", `Премахни условие ${index + 1}`);
                removeButton.addEventListener("click", () => {
                    rows.splice(index, 1);
                    sync();
                    render();
                });

                const details = createElement("details", "mission-requirements__advanced");
                details.append(createElement("summary", "", "Разширени настройки"));
                const keyInput = document.createElement("input");
                keyInput.type = "text";
                keyInput.value = String(row.key || "");
                keyInput.placeholder = "напр. read_posts";
                keyInput.pattern = "[a-z][a-z0-9_]{0,49}";
                keyInput.addEventListener("input", () => {
                    row.key = keyInput.value;
                    sync();
                });
                details.append(createLabel("Системен ключ", keyInput));

                card.append(
                    number,
                    createLabel("Описание за потребителя", labelInput),
                    createLabel("Следено действие", actionSelect),
                    createLabel("Необходим брой", targetInput),
                    removeButton,
                    details
                );
                list.append(card);
            });
            sync();
        };

        addButton.addEventListener("click", () => {
            rows.push({
                key: "",
                label: "",
                action: "",
                target: 1,
            });
            render();
            const cards = list.querySelectorAll(".mission-requirements__card");
            cards[cards.length - 1]?.querySelector('input[type="text"]')?.focus();
        });

        textarea.form?.addEventListener("submit", sync);
        render();
    };

    const setupAll = () => {
        document
            .querySelectorAll('[data-mission-requirements-input="true"]')
            .forEach(setupEditor);
    };

    document.addEventListener("DOMContentLoaded", setupAll);
    setupAll();
})();
