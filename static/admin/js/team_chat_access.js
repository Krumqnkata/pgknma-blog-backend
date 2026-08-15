(() => {
    "use strict";

    document.querySelectorAll("[data-team-chat-access-form]").forEach((form) => {
        const modes = [...form.querySelectorAll('input[name="visibility"]')];
        const panels = [...form.querySelectorAll("[data-team-chat-access-panel]")];
        const modeCards = [...form.querySelectorAll(".team-chat-access-mode")];
        const filter = form.querySelector("[data-team-chat-user-filter]");
        const userRows = [...form.querySelectorAll("[data-team-chat-user-row]")];
        const empty = form.querySelector("[data-team-chat-user-empty]");

        const updateMode = () => {
            const selected = modes.find((input) => input.checked)?.value || "team";
            panels.forEach((panel) => {
                panel.hidden = panel.dataset.teamChatAccessPanel !== selected;
            });
            modeCards.forEach((card) => {
                card.classList.toggle(
                    "is-selected",
                    card.querySelector('input[name="visibility"]')?.checked === true,
                );
            });
        };

        const updateUserFilter = () => {
            if (!filter) return;
            const query = filter.value.trim().toLocaleLowerCase("bg-BG");
            let visible = 0;
            userRows.forEach((row) => {
                const matches = !query || (row.dataset.search || "")
                    .toLocaleLowerCase("bg-BG")
                    .includes(query);
                row.hidden = !matches;
                if (matches) visible += 1;
            });
            if (empty) empty.hidden = userRows.length === 0 || visible !== 0;
        };

        modes.forEach((input) => input.addEventListener("change", updateMode));
        filter?.addEventListener("input", updateUserFilter);
        updateMode();
        updateUserFilter();
    });
})();
