(() => {
    "use strict";

    const chatPath = "/admin/team-chat/";
    const unreadUrl = "/admin/team-chat/unread/";
    const messagesUrl = "/admin/team-chat/messages/";
    const readUrl = "/admin/team-chat/read/";
    const discussionsPath = "/admin/team-chat/discussions/";
    const discussionsApiUrl = "/admin/team-chat/discussions/api/";
    const soundStorageKey = "pgknma-team-chat-sound";
    const pollInterval = 5000;
    const visibleMessageLimit = 20;
    const state = {
        previousCount: null,
        pollTimer: null,
        polling: false,
        widgetOpen: false,
        widgetLoading: false,
        widgetMessages: new Map(),
        widgetPinnedMessages: new Map(),
        widgetServerTime: null,
        activeTab: "chat",
        widgetAttachments: [],
        root: null,
        button: null,
        buttonCount: null,
        panel: null,
        messages: null,
        pinned: null,
        pinnedList: null,
        form: null,
        textarea: null,
        submit: null,
        status: null,
        chatView: null,
        discussionsView: null,
        discussionsList: null,
        footer: null,
        fileInput: null,
        attach: null,
        fileSummary: null,
    };

    const createElement = (tag, className, text) => {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    };

    const chatLinks = () => [...document.querySelectorAll("a[href]")].filter((link) => {
        try {
            return new URL(link.href, window.location.origin).pathname === chatPath;
        } catch {
            return false;
        }
    });

    const badgeText = (count) => (count > 99 ? "99+" : String(count));

    const updateSidebarBadges = (count) => {
        chatLinks().forEach((link) => {
            let badge = link.querySelector("[data-team-chat-unread-badge]");
            if (!badge) {
                badge = link.querySelector(".sidebar-badge");
                if (badge) badge.dataset.teamChatUnreadBadge = "";
            }
            if (count <= 0) {
                if (badge) badge.remove();
                link.removeAttribute("aria-label");
                return;
            }
            if (!badge) {
                badge = document.createElement("span");
                badge.dataset.teamChatUnreadBadge = "";
                badge.className = [
                    "sidebar-badge",
                    "font-semibold",
                    "h-[18px]",
                    "leading-[18px]",
                    "ml-2",
                    "min-w-[18px]",
                    "px-1",
                    "rounded-xs",
                    "text-center",
                    "text-[11px]",
                    "whitespace-nowrap",
                    "bg-red-600",
                    "text-white",
                ].join(" ");
                link.append(badge);
            }
            badge.textContent = badgeText(count);
            badge.setAttribute("aria-label", `${count} непрочетени съобщения`);
            link.setAttribute(
                "aria-label",
                `Екипен чат, ${count} непрочетени съобщения`,
            );
        });
    };

    const updateFloatingCount = (count) => {
        if (!state.buttonCount || !state.button) return;
        state.buttonCount.hidden = count <= 0;
        state.buttonCount.textContent = count > 0 ? badgeText(count) : "";
        state.button.setAttribute(
            "aria-label",
            count > 0
                ? `Отвори екипния чат, ${count} непрочетени съобщения`
                : "Отвори екипния чат",
        );
    };

    const soundIsMuted = () => {
        try {
            return localStorage.getItem(soundStorageKey) === "muted";
        } catch {
            return false;
        }
    };

    const playSound = () => {
        if (soundIsMuted() || document.visibilityState !== "visible") return;
        try {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (!AudioContext) return;
            const context = new AudioContext();
            const oscillator = context.createOscillator();
            const gain = context.createGain();
            oscillator.frequency.value = 620;
            gain.gain.setValueAtTime(0.035, context.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.11);
            oscillator.connect(gain);
            gain.connect(context.destination);
            oscillator.start();
            oscillator.stop(context.currentTime + 0.11);
            oscillator.addEventListener("ended", () => void context.close());
        } catch {
            // Browsers may disallow audio before the first user interaction.
        }
    };

    const applyCount = (rawCount, { allowSound = true } = {}) => {
        const count = Math.max(0, Number.parseInt(rawCount, 10) || 0);
        updateSidebarBadges(count);
        updateFloatingCount(count);
        if (
            allowSound
            && state.previousCount !== null
            && count > state.previousCount
            && window.location.pathname !== chatPath
        ) {
            playSound();
        }
        state.previousCount = count;
    };

    const csrfToken = () => {
        const formToken = document.querySelector("input[name=csrfmiddlewaretoken]");
        if (formToken?.value) return formToken.value;
        const prefix = "csrftoken=";
        const cookie = document.cookie
            .split(";")
            .map((part) => part.trim())
            .find((part) => part.startsWith(prefix));
        return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : "";
    };

    const readJson = async (response) => {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.detail || "Възникна неочаквана грешка.");
        }
        return payload;
    };

    const getJson = async (url) => readJson(await fetch(url, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
    }));

    const postJson = async (url, payload) => readJson(await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken(),
            "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify(payload),
    }));

    const postForm = async (url, formData) => readJson(await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
            "X-CSRFToken": csrfToken(),
            "X-Requested-With": "XMLHttpRequest",
        },
        body: formData,
    }));

    const formatDate = (value) => {
        try {
            return new Intl.DateTimeFormat("bg-BG", {
                hour: "2-digit",
                minute: "2-digit",
            }).format(new Date(value));
        } catch {
            return "";
        }
    };

    const maxWidgetMessageId = () => Math.max(0, ...state.widgetMessages.keys());

    const messageActionUrl = (messageId, action) => (
        `${messagesUrl}${messageId}/${action}/`
    );

    const setWidgetStatus = (message = "", isError = false) => {
        if (!state.status) return;
        state.status.textContent = message;
        state.status.classList.toggle("is-error", isError);
    };

    const buildSharedCard = (shared) => {
        const card = createElement(
            shared.available ? "a" : "div",
            "team-chat-floating-share",
        );
        if (shared.available) {
            card.href = shared.url;
            card.textContent = `${shared.label}: ${shared.title}`;
        } else {
            card.textContent = shared.label;
        }
        return card;
    };

    const buildWidgetMessage = (message) => {
        const row = createElement(
            "article",
            `team-chat-floating-message${message.is_own ? " is-own" : ""}`,
        );
        const meta = createElement("div", "team-chat-floating-meta");
        meta.append(createElement("strong", "", message.author_name));
        meta.append(createElement("time", "", formatDate(message.created_at)));
        if (message.edited_at) meta.append(createElement("span", "", "ред."));
        row.append(meta);

        const bubble = createElement("div", "team-chat-floating-bubble");
        if (message.reply_to) {
            bubble.append(createElement(
                "span",
                "team-chat-floating-reply",
                `${message.reply_to.author_name}: ${message.reply_to.preview}`,
            ));
        }
        if (message.deleted) {
            bubble.append(document.createTextNode("Съобщението е изтрито"));
        } else {
            if (message.text) bubble.append(document.createTextNode(message.text));
            (message.shared_objects || []).forEach((shared) => {
                bubble.append(buildSharedCard(shared));
            });
            if (message.attachments?.length) {
                const images = createElement("div", "team-chat-floating-images");
                message.attachments.forEach((attachment) => {
                    const link = createElement("a", "team-chat-floating-image");
                    link.href = attachment.url;
                    link.target = "_blank";
                    link.rel = "noopener";
                    link.setAttribute("aria-label", `Отвори ${attachment.original_name || "изображението"}`);
                    const image = createElement("img");
                    image.src = attachment.thumbnail_url;
                    image.alt = attachment.alt_text || "Прикачено изображение";
                    image.loading = "lazy";
                    link.append(image);
                    images.append(link);
                });
                bubble.append(images);
            }
        }
        row.append(bubble);
        if (!message.deleted) {
            const reactions = createElement("div", "team-chat-floating-reactions");
            (message.reactions || []).forEach((reaction) => {
                const button = createElement(
                    "button",
                    `team-chat-floating-reaction${reaction.reacted_by_me ? " is-active" : ""}`,
                );
                button.type = "button";
                button.setAttribute(
                    "aria-label",
                    `${reaction.emoji} — ${reaction.count} реакции`,
                );
                button.setAttribute("aria-pressed", String(reaction.reacted_by_me));
                button.append(document.createTextNode(reaction.emoji));
                if (reaction.count > 0) {
                    button.append(createElement("span", "", String(reaction.count)));
                }
                button.addEventListener(
                    "click",
                    () => void toggleWidgetReaction(message, reaction.emoji),
                );
                reactions.append(button);
            });
            row.append(reactions);
        }
        return row;
    };

    const replaceWidgetPinnedMessages = (messages) => {
        state.widgetPinnedMessages.clear();
        (messages || []).forEach((message) => {
            state.widgetPinnedMessages.set(message.id, message);
        });
    };

    const renderWidgetPinnedMessages = () => {
        if (!state.pinned || !state.pinnedList) return;
        const rows = [...state.widgetPinnedMessages.values()].sort((left, right) => (
            new Date(right.pinned_at) - new Date(left.pinned_at)
        ));
        state.pinned.hidden = rows.length === 0;
        const fragment = document.createDocumentFragment();
        rows.slice(0, 3).forEach((message) => {
            const card = createElement("article", "team-chat-floating-pinned-card");
            card.append(createElement("strong", "", message.author_name));
            card.append(createElement(
                "span",
                "",
                message.text || "Споделен административен запис",
            ));
            if (message.can_pin) {
                const unpin = createElement("button", "team-chat-floating-pinned-unpin");
                unpin.type = "button";
                unpin.setAttribute("aria-label", "Откачи съобщението");
                unpin.append(createElement("span", "material-symbols-outlined", "keep_off"));
                unpin.addEventListener(
                    "click",
                    () => void toggleWidgetPin(message, false),
                );
                card.append(unpin);
            }
            fragment.append(card);
        });
        if (rows.length > 3) {
            fragment.append(createElement(
                "a",
                "team-chat-floating-pinned-more",
                `Още ${rows.length - 3} в пълния чат`,
            ));
            fragment.lastChild.href = chatPath;
        }
        state.pinnedList.replaceChildren(fragment);
    };

    const renderWidgetMessages = ({ forceBottom = false } = {}) => {
        if (!state.messages) return;
        const nearBottom = (
            state.messages.scrollHeight
            - state.messages.scrollTop
            - state.messages.clientHeight
        ) < 60;
        const rows = [...state.widgetMessages.values()]
            .sort((left, right) => left.id - right.id)
            .slice(-visibleMessageLimit);
        const fragment = document.createDocumentFragment();
        if (!rows.length) {
            const empty = createElement("div", "team-chat-floating-state");
            empty.append(createElement("span", "material-symbols-outlined", "chat_bubble"));
            empty.append(createElement("strong", "", "Все още няма съобщения"));
            empty.append(createElement("span", "", "Започнете разговора с екипа."));
            fragment.append(empty);
        } else {
            rows.forEach((message) => fragment.append(buildWidgetMessage(message)));
        }
        state.messages.replaceChildren(fragment);
        if (forceBottom || nearBottom) {
            state.messages.scrollTop = state.messages.scrollHeight;
        }
    };

    const mergeWidgetMessages = (messages) => {
        messages.forEach((message) => state.widgetMessages.set(message.id, message));
    };

    const renderDiscussions = (discussions) => {
        const fragment = document.createDocumentFragment();
        if (!discussions.length) {
            const empty = createElement("div", "team-chat-floating-state");
            empty.append(createElement("span", "material-symbols-outlined", "forum"));
            empty.append(createElement("strong", "", "Все още няма дискусии"));
            empty.append(createElement("span", "", "Създайте отделна тема от пълния изглед."));
            fragment.append(empty);
        }
        discussions.forEach((discussion) => {
            const card = createElement("a", "team-chat-floating-discussion");
            card.href = discussion.url;
            const heading = createElement("div");
            heading.append(createElement("strong", "", discussion.title));
            heading.append(createElement(
                "span",
                `is-${discussion.status}`,
                discussion.status_label,
            ));
            card.append(heading);
            card.append(createElement(
                "small",
                "",
                `${discussion.message_count} съобщения · ${discussion.participants.length} участници`,
            ));
            if (discussion.source) {
                card.append(createElement(
                    "small",
                    "",
                    `${discussion.source.label}: ${discussion.source.title}`,
                ));
            }
            if (discussion.unread_count > 0) {
                card.append(createElement(
                    "span",
                    "team-chat-floating-discussion-unread",
                    badgeText(discussion.unread_count),
                ));
            }
            fragment.append(card);
        });
        state.discussionsList.replaceChildren(fragment);
    };

    const loadDiscussions = async () => {
        state.discussionsList.replaceChildren(createElement(
            "div",
            "team-chat-floating-state",
            "Зареждане на дискусиите…",
        ));
        try {
            const payload = await getJson(discussionsApiUrl);
            renderDiscussions(payload.discussions || []);
        } catch (error) {
            state.discussionsList.replaceChildren(createElement(
                "div",
                "team-chat-floating-state",
                error.message,
            ));
        }
    };

    const switchTab = (tab) => {
        state.activeTab = tab;
        state.chatView.hidden = tab !== "chat";
        state.footer.hidden = tab !== "chat";
        state.discussionsView.hidden = tab !== "discussions";
        state.panel.querySelectorAll(".team-chat-floating-tab").forEach((button) => {
            const active = button.dataset.tab === tab;
            button.classList.toggle("is-active", active);
            button.setAttribute("aria-selected", String(active));
        });
        if (tab === "discussions") void loadDiscussions();
        else void loadWidgetInitial().then(() => state.textarea.focus());
    };

    const toggleWidgetReaction = async (message, emoji) => {
        try {
            const payload = await postJson(
                messageActionUrl(message.id, "reaction"),
                { emoji },
            );
            state.widgetMessages.set(payload.message.id, payload.message);
            if (payload.message.is_pinned) {
                state.widgetPinnedMessages.set(payload.message.id, payload.message);
            }
            renderWidgetMessages();
            renderWidgetPinnedMessages();
        } catch (error) {
            setWidgetStatus(error.message, true);
        }
    };

    const toggleWidgetPin = async (message, pinned) => {
        try {
            const payload = await postJson(
                messageActionUrl(message.id, "pin"),
                { pinned },
            );
            state.widgetMessages.set(payload.message.id, payload.message);
            replaceWidgetPinnedMessages(payload.pinned_messages);
            renderWidgetMessages();
            renderWidgetPinnedMessages();
        } catch (error) {
            setWidgetStatus(error.message, true);
        }
    };

    const markWidgetRead = async () => {
        const lastMessageId = maxWidgetMessageId();
        if (!state.widgetOpen || lastMessageId <= 0) return;
        try {
            await postJson(readUrl, {
                last_message_id: lastMessageId,
            });
            const counts = await getJson(unreadUrl);
            applyCount(counts.total_unread_count ?? counts.unread_count, { allowSound: false });
        } catch {
            // The next refresh retries without blocking the compact chat.
        }
    };

    const loadWidgetInitial = async () => {
        if (state.widgetLoading) return;
        state.widgetLoading = true;
        setWidgetStatus("Зареждане…");
        try {
            const payload = await getJson(messagesUrl);
            state.widgetMessages.clear();
            mergeWidgetMessages(payload.messages);
            replaceWidgetPinnedMessages(payload.pinned_messages);
            state.widgetServerTime = payload.server_time;
            renderWidgetMessages({ forceBottom: true });
            renderWidgetPinnedMessages();
            setWidgetStatus("");
            await markWidgetRead();
        } catch (error) {
            setWidgetStatus(error.message, true);
            if (!state.widgetMessages.size && state.messages) {
                const failed = createElement(
                    "div",
                    "team-chat-floating-state",
                    "Чатът временно не може да се зареди.",
                );
                state.messages.replaceChildren(failed);
            }
        } finally {
            state.widgetLoading = false;
        }
    };

    const refreshWidget = async () => {
        if (!state.widgetOpen || state.widgetLoading) return;
        if (!state.widgetServerTime) {
            await loadWidgetInitial();
            return;
        }
        state.widgetLoading = true;
        try {
            const query = new URLSearchParams({
                after_id: String(maxWidgetMessageId()),
                updated_after: state.widgetServerTime,
            });
            const payload = await getJson(`${messagesUrl}?${query}`);
            state.widgetServerTime = payload.server_time;
            replaceWidgetPinnedMessages(payload.pinned_messages);
            renderWidgetPinnedMessages();
            if (payload.messages.length) {
                mergeWidgetMessages(payload.messages);
                renderWidgetMessages({ forceBottom: true });
            }
            await markWidgetRead();
        } catch {
            // Preserve visible messages and retry with the global poll.
        } finally {
            state.widgetLoading = false;
        }
    };

    const sendWidgetMessage = async () => {
        const text = state.textarea.value.trim();
        if (!text && !state.widgetAttachments.length) {
            setWidgetStatus("Напишете съобщение или добавете снимка.", true);
            return;
        }
        state.submit.disabled = true;
        setWidgetStatus("Изпращане…");
        try {
            const formData = new FormData();
            formData.append("text", text);
            state.widgetAttachments.forEach((file) => {
                formData.append("images", file, file.name);
            });
            const payload = await postForm(messagesUrl, formData);
            mergeWidgetMessages([payload.message]);
            state.textarea.value = "";
            state.widgetAttachments = [];
            state.fileInput.value = "";
            state.fileSummary.textContent = "";
            renderWidgetMessages({ forceBottom: true });
            setWidgetStatus("");
            await markWidgetRead();
        } catch (error) {
            setWidgetStatus(error.message, true);
        } finally {
            state.submit.disabled = false;
        }
    };

    const addWidgetAttachments = (incoming) => {
        const allowed = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
        for (const file of [...incoming]) {
            if (!allowed.has(file.type)) {
                setWidgetStatus("Разрешени са само JPEG, PNG, WebP и GIF.", true);
                continue;
            }
            if (file.size > 5 * 1024 * 1024) {
                setWidgetStatus(`„${file.name}“ е по-голямо от 5 MB.`, true);
                continue;
            }
            if (state.widgetAttachments.length >= 3) {
                setWidgetStatus("Може да добавите най-много 3 изображения.", true);
                break;
            }
            state.widgetAttachments.push(file);
        }
        state.fileSummary.textContent = state.widgetAttachments.length
            ? `${state.widgetAttachments.length} избрани снимки`
            : "";
    };

    const openWidget = () => {
        state.widgetOpen = true;
        state.panel.hidden = false;
        state.button.setAttribute("aria-expanded", "true");
        if (state.activeTab === "chat") {
            void loadWidgetInitial().then(() => state.textarea.focus());
        } else {
            void loadDiscussions();
        }
    };

    const closeWidget = () => {
        state.widgetOpen = false;
        state.panel.hidden = true;
        state.button.setAttribute("aria-expanded", "false");
        state.button.focus();
    };

    const buildWidget = () => {
        if (window.location.pathname.startsWith(chatPath)) return;

        state.root = createElement("div", "team-chat-floating-root");
        state.button = createElement("button", "team-chat-floating-button");
        state.button.type = "button";
        state.button.title = "Екипен чат";
        state.button.setAttribute("aria-controls", "team-chat-floating-panel");
        state.button.setAttribute("aria-expanded", "false");
        state.button.setAttribute("aria-label", "Отвори екипния чат");
        state.button.append(createElement("span", "material-symbols-outlined", "forum"));
        state.buttonCount = createElement("span", "team-chat-floating-count");
        state.buttonCount.hidden = true;
        state.button.append(state.buttonCount);

        state.panel = createElement("section", "team-chat-floating-panel");
        state.panel.id = "team-chat-floating-panel";
        state.panel.hidden = true;
        state.panel.setAttribute("role", "dialog");
        state.panel.setAttribute("aria-modal", "false");
        state.panel.setAttribute("aria-label", "Компактен екипен чат");

        const header = createElement("header", "team-chat-floating-header");
        const title = createElement("div", "team-chat-floating-title");
        title.append(createElement("span", "material-symbols-outlined", "forum"));
        const titleText = createElement("div");
        titleText.append(createElement("strong", "", "Екипен чат"));
        titleText.append(createElement("small", "", "Само за потребители с admin достъп"));
        title.append(titleText);
        const close = createElement("button", "team-chat-floating-close");
        close.type = "button";
        close.setAttribute("aria-label", "Затвори чата");
        close.append(createElement("span", "material-symbols-outlined", "close"));
        close.addEventListener("click", closeWidget);
        header.append(title, close);
        state.panel.append(header);

        const tabs = createElement("div", "team-chat-floating-tabs");
        tabs.setAttribute("role", "tablist");
        [
            ["chat", "chat", "Чат"],
            ["discussions", "forum", "Дискусии"],
        ].forEach(([tab, icon, label]) => {
            const button = createElement(
                "button",
                `team-chat-floating-tab${tab === state.activeTab ? " is-active" : ""}`,
            );
            button.type = "button";
            button.dataset.tab = tab;
            button.setAttribute("role", "tab");
            button.setAttribute("aria-selected", String(tab === state.activeTab));
            button.append(createElement("span", "material-symbols-outlined", icon));
            button.append(document.createTextNode(label));
            button.addEventListener("click", () => switchTab(tab));
            tabs.append(button);
        });
        state.panel.append(tabs);

        state.chatView = createElement("div", "team-chat-floating-chat-view");

        state.pinned = createElement("section", "team-chat-floating-pinned");
        state.pinned.hidden = true;
        const pinnedTitle = createElement("div", "team-chat-floating-pinned-title");
        pinnedTitle.append(createElement("span", "material-symbols-outlined", "keep"));
        pinnedTitle.append(createElement("strong", "", "Закачени"));
        state.pinnedList = createElement("div", "team-chat-floating-pinned-list");
        state.pinned.append(pinnedTitle, state.pinnedList);
        state.chatView.append(state.pinned);

        state.messages = createElement("div", "team-chat-floating-messages");
        state.messages.setAttribute("aria-live", "polite");
        state.chatView.append(state.messages);
        state.panel.append(state.chatView);

        state.discussionsView = createElement("section", "team-chat-floating-discussions");
        state.discussionsView.hidden = true;
        state.discussionsList = createElement("div", "team-chat-floating-discussion-list");
        state.discussionsView.append(state.discussionsList);
        const allDiscussions = createElement(
            "a",
            "team-chat-floating-full-link team-chat-floating-all-discussions",
            "Всички дискусии",
        );
        allDiscussions.href = discussionsPath;
        state.discussionsView.append(allDiscussions);
        state.panel.append(state.discussionsView);

        const footer = createElement("footer", "team-chat-floating-footer");
        state.footer = footer;
        state.form = createElement("form", "team-chat-floating-form");
        state.textarea = createElement("textarea", "team-chat-floating-textarea");
        state.textarea.rows = 2;
        state.textarea.maxLength = 4000;
        state.textarea.placeholder = "Напишете съобщение…";
        state.textarea.setAttribute("aria-label", "Съобщение в екипния чат");
        state.submit = createElement("button", "team-chat-floating-send");
        state.submit.type = "submit";
        state.submit.setAttribute("aria-label", "Изпрати съобщението");
        state.submit.append(createElement("span", "material-symbols-outlined", "send"));
        state.attach = createElement("button", "team-chat-floating-attach");
        state.attach.type = "button";
        state.attach.setAttribute("aria-label", "Добави изображения");
        state.attach.append(createElement("span", "material-symbols-outlined", "attach_file"));
        state.fileInput = createElement("input");
        state.fileInput.type = "file";
        state.fileInput.accept = "image/jpeg,image/png,image/webp,image/gif";
        state.fileInput.multiple = true;
        state.fileInput.hidden = true;
        state.form.append(state.textarea, state.attach, state.fileInput, state.submit);
        state.attach.addEventListener("click", () => state.fileInput.click());
        state.fileInput.addEventListener("change", () => {
            addWidgetAttachments(state.fileInput.files);
            state.fileInput.value = "";
        });
        state.form.addEventListener("submit", (event) => {
            event.preventDefault();
            void sendWidgetMessage();
        });
        state.textarea.addEventListener("keydown", (event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
                event.preventDefault();
                void sendWidgetMessage();
            }
        });
        state.textarea.addEventListener("paste", (event) => {
            const images = [...(event.clipboardData?.files || [])].filter((file) => (
                file.type.startsWith("image/")
            ));
            if (images.length) addWidgetAttachments(images);
        });
        footer.append(state.form);

        state.fileSummary = createElement("div", "team-chat-floating-file-summary");
        footer.append(state.fileSummary);

        const bottomRow = createElement("div", "team-chat-floating-bottom-row");
        state.status = createElement("span", "team-chat-floating-status");
        state.status.setAttribute("role", "status");
        state.status.setAttribute("aria-live", "polite");
        const fullLink = createElement(
            "a",
            "team-chat-floating-full-link",
            "Отвори целия чат",
        );
        fullLink.href = chatPath;
        bottomRow.append(state.status, fullLink);
        footer.append(bottomRow);
        state.panel.append(footer);

        state.button.addEventListener("click", () => {
            if (state.widgetOpen) closeWidget();
            else openWidget();
        });
        state.root.append(state.panel, state.button);
        document.body.append(state.root);
    };

    const poll = async () => {
        if (state.polling) return;
        state.polling = true;
        window.clearTimeout(state.pollTimer);
        try {
            const payload = await getJson(unreadUrl);
            applyCount(payload.total_unread_count ?? payload.unread_count);
            if (state.widgetOpen && state.activeTab === "chat") await refreshWidget();
            if (state.widgetOpen && state.activeTab === "discussions") await loadDiscussions();
        } catch {
            // Keep the last visible count and retry without disturbing admin work.
        } finally {
            state.polling = false;
            state.pollTimer = window.setTimeout(() => void poll(), pollInterval);
        }
    };

    window.addEventListener("team-chat:unread", (event) => {
        if (event.detail?.count > 0) {
            applyCount(event.detail.count, { allowSound: false });
        } else {
            void poll();
        }
    });
    window.addEventListener("team-chat:refresh-unread", () => void poll());
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") void poll();
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && state.widgetOpen) closeWidget();
    });

    const start = () => {
        if (!chatLinks().length) return;
        buildWidget();
        void poll();
    };
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start, { once: true });
    } else {
        start();
    }
})();
