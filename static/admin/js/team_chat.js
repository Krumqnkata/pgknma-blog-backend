(() => {
    "use strict";

    const root = document.getElementById("team-chat-app");
    if (!root) return;

    const elements = {
        messages: document.getElementById("team-chat-messages"),
        loading: document.getElementById("team-chat-loading"),
        empty: document.getElementById("team-chat-empty"),
        loadOlder: document.getElementById("team-chat-load-older"),
        newMessages: document.getElementById("team-chat-new-messages"),
        form: document.getElementById("team-chat-form"),
        text: document.getElementById("team-chat-text"),
        submit: document.getElementById("team-chat-submit"),
        submitLabel: document.getElementById("team-chat-submit-label"),
        status: document.getElementById("team-chat-status"),
        characterCount: document.getElementById("team-chat-character-count"),
        mode: document.getElementById("team-chat-mode"),
        modeTitle: document.getElementById("team-chat-mode-title"),
        modePreview: document.getElementById("team-chat-mode-preview"),
        modeCancel: document.getElementById("team-chat-mode-cancel"),
        mentions: document.getElementById("team-chat-mentions"),
        soundToggle: document.getElementById("team-chat-sound-toggle"),
        pinned: document.getElementById("team-chat-pinned"),
        pinnedList: document.getElementById("team-chat-pinned-list"),
        pinnedCount: document.getElementById("team-chat-pinned-count"),
        fileInput: document.getElementById("team-chat-files"),
        attach: document.getElementById("team-chat-attach"),
        attachmentPreview: document.getElementById("team-chat-attachment-preview"),
        dropzone: document.getElementById("team-chat-dropzone"),
        lightbox: document.getElementById("team-chat-lightbox"),
        lightboxImage: document.getElementById("team-chat-lightbox-image"),
        lightboxCaption: document.getElementById("team-chat-lightbox-caption"),
        lightboxClose: document.getElementById("team-chat-lightbox-close"),
        resolve: document.getElementById("team-chat-resolve"),
        headerStatus: document.getElementById("team-chat-header-status"),
    };

    const endpoints = {
        messages: root.dataset.messagesUrl,
        read: root.dataset.readUrl,
        staff: root.dataset.staffUrl,
        editTemplate: root.dataset.editUrlTemplate,
        deleteTemplate: root.dataset.deleteUrlTemplate,
        pinTemplate: root.dataset.pinUrlTemplate,
        reactionTemplate: root.dataset.reactionUrlTemplate,
        discussions: root.dataset.discussionsUrl,
        discussionsApi: root.dataset.discussionsApiUrl,
        resolve: root.dataset.resolveUrl,
    };
    const scope = root.dataset.scope || "general";
    const csrfToken = elements.form.querySelector("input[name=csrfmiddlewaretoken]").value;
    const soundStorageKey = "pgknma-team-chat-sound";
    const state = {
        messages: new Map(),
        pinnedMessages: new Map(),
        initialized: false,
        hasMore: false,
        serverTime: null,
        replyTo: null,
        editing: null,
        pollTimer: null,
        readTimer: null,
        mentionTimer: null,
        mentionRange: null,
        mentionResults: [],
        mentionIndex: 0,
        attachments: [],
        previewUrls: [],
    };

    const createElement = (tag, className, text) => {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    };

    const endpointForMessage = (template, id) => template.replace("/0/", `/${id}/`);

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
            "X-CSRFToken": csrfToken,
            "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify(payload),
    }));

    const postForm = async (url, formData) => readJson(await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: {
            "X-CSRFToken": csrfToken,
            "X-Requested-With": "XMLHttpRequest",
        },
        body: formData,
    }));

    const setStatus = (message = "", isError = false) => {
        elements.status.textContent = message;
        elements.status.classList.toggle("is-error", isError);
        elements.headerStatus.textContent = isError ? message : "";
        elements.headerStatus.classList.toggle("is-error", isError);
    };

    const formatDate = (value) => {
        try {
            return new Intl.DateTimeFormat("bg-BG", {
                day: "numeric",
                month: "short",
                hour: "2-digit",
                minute: "2-digit",
            }).format(new Date(value));
        } catch {
            return "";
        }
    };

    const initials = (name) => name
        .split(/\s+/u)
        .filter(Boolean)
        .slice(0, 2)
        .map((part) => part[0])
        .join("") || "?";

    const appendHighlightedText = (container, text, mentions) => {
        const mentioned = new Set(mentions.map((username) => username.toLocaleLowerCase("bg-BG")));
        const pattern = /@[\p{L}\p{N}_.+\-]+/gu;
        let cursor = 0;
        for (const match of text.matchAll(pattern)) {
            if (match.index > cursor) {
                container.append(document.createTextNode(text.slice(cursor, match.index)));
            }
            const rawToken = match[0];
            const coreToken = rawToken.replace(/[.,!?;:]+$/u, "");
            const punctuation = rawToken.slice(coreToken.length);
            const username = coreToken.slice(1).toLocaleLowerCase("bg-BG");
            if (mentioned.has(username)) {
                container.append(createElement("span", "team-chat-mention", coreToken));
            } else {
                container.append(document.createTextNode(coreToken));
            }
            if (punctuation) container.append(document.createTextNode(punctuation));
            cursor = match.index + rawToken.length;
        }
        if (cursor < text.length) {
            container.append(document.createTextNode(text.slice(cursor)));
        }
    };

    const buildReplyQuote = (reply) => {
        const quote = createElement("div", "team-chat-reply-quote");
        quote.append(createElement("strong", "", reply.author_name));
        quote.append(createElement("span", "", reply.preview));
        return quote;
    };

    const buildSharedObject = (shared) => {
        const card = createElement(
            shared.available ? "a" : "div",
            `team-chat-share-card${shared.available ? "" : " is-restricted"}`,
        );
        if (shared.available) {
            card.href = shared.url;
            card.append(createElement("small", "", shared.label));
            card.append(createElement("strong", "", shared.title));
        } else {
            card.append(createElement("strong", "", shared.label));
        }
        return card;
    };

    const actionButton = (icon, label, handler) => {
        const button = createElement("button", "team-chat-action-button");
        button.type = "button";
        button.setAttribute("aria-label", label);
        button.append(createElement("span", "material-symbols-outlined", icon));
        button.append(document.createTextNode(label));
        button.addEventListener("click", handler);
        return button;
    };

    const setMode = ({ reply = null, edit = null } = {}) => {
        state.replyTo = reply;
        state.editing = edit;
        if (!reply && !edit) {
            elements.mode.hidden = true;
            elements.modeTitle.textContent = "";
            elements.modePreview.textContent = "";
            elements.submitLabel.textContent = "Изпрати";
            return;
        }
        elements.mode.hidden = false;
        if (edit) {
            clearAttachmentPreviews();
            elements.modeTitle.textContent = "Редактиране на съобщението";
            elements.modePreview.textContent = "Промените ще останат в административната история.";
            elements.text.value = edit.text;
            elements.submitLabel.textContent = "Запази";
            updateCharacterCount();
        } else {
            elements.modeTitle.textContent = `Отговор до ${reply.author_name}`;
            elements.modePreview.textContent = reply.text || "Споделен административен запис";
            elements.submitLabel.textContent = "Изпрати";
        }
        elements.text.focus();
    };

    const deleteChatMessage = async (message) => {
        if (!window.confirm("Да изтрием ли това съобщение? Текстът ще бъде скрит, но ще остане одитна следа.")) return;
        try {
            const payload = await postJson(endpointForMessage(endpoints.deleteTemplate, message.id), {});
            state.messages.set(payload.message.id, payload.message);
            state.pinnedMessages.delete(payload.message.id);
            renderPinnedMessages();
            renderMessages();
        } catch (error) {
            setStatus(error.message, true);
        }
    };

    const togglePin = async (message) => {
        try {
            const payload = await postJson(
                endpointForMessage(endpoints.pinTemplate, message.id),
                { pinned: !message.is_pinned },
            );
            state.messages.set(payload.message.id, payload.message);
            replacePinnedMessages(payload.pinned_messages);
            renderPinnedMessages();
            renderMessages();
        } catch (error) {
            setStatus(error.message, true);
        }
    };

    const toggleReaction = async (message, emoji) => {
        try {
            const payload = await postJson(
                endpointForMessage(endpoints.reactionTemplate, message.id),
                { emoji },
            );
            state.messages.set(payload.message.id, payload.message);
            if (payload.message.is_pinned) {
                state.pinnedMessages.set(payload.message.id, payload.message);
            }
            renderPinnedMessages();
            renderMessages();
        } catch (error) {
            setStatus(error.message, true);
        }
    };

    const buildReactions = (message) => {
        const row = createElement("div", "team-chat-reactions");
        (message.reactions || []).forEach((reaction) => {
            const label = `${reaction.emoji} — ${reaction.count} реакции`;
            const button = createElement(
                "button",
                `team-chat-reaction${reaction.reacted_by_me ? " is-active" : ""}`,
            );
            button.type = "button";
            button.setAttribute("aria-label", label);
            button.setAttribute("aria-pressed", String(reaction.reacted_by_me));
            button.append(document.createTextNode(reaction.emoji));
            if (reaction.count > 0) {
                button.append(createElement("span", "", String(reaction.count)));
            }
            button.addEventListener(
                "click",
                () => void toggleReaction(message, reaction.emoji),
            );
            row.append(button);
        });
        return row;
    };

    const openAttachment = (attachment) => {
        elements.lightboxImage.src = attachment.url;
        elements.lightboxImage.alt = attachment.alt_text || "Прикачено изображение";
        elements.lightboxCaption.textContent = attachment.original_name || attachment.alt_text || "";
        if (typeof elements.lightbox.showModal === "function") {
            elements.lightbox.showModal();
        } else {
            window.open(attachment.url, "_blank", "noopener");
        }
    };

    const buildAttachments = (attachments) => {
        const grid = createElement("div", "team-chat-attachments");
        attachments.forEach((attachment) => {
            const button = createElement("button", "team-chat-attachment");
            button.type = "button";
            button.setAttribute("aria-label", `Отвори ${attachment.original_name || "изображението"}`);
            const image = createElement("img");
            image.src = attachment.thumbnail_url;
            image.alt = attachment.alt_text || "Прикачено изображение";
            image.loading = "lazy";
            image.decoding = "async";
            button.append(image);
            if (attachment.is_animated) {
                button.append(createElement("span", "team-chat-animated-label", "GIF"));
            }
            button.addEventListener("click", () => openAttachment(attachment));
            grid.append(button);
        });
        return grid;
    };

    const startDiscussion = (message) => {
        if (message.origin_discussion) {
            window.location.assign(message.origin_discussion.url);
            return;
        }
        const query = new URLSearchParams({ origin_message_id: String(message.id) });
        window.location.assign(`${endpoints.discussions}?${query}`);
    };

    const buildMessage = (message) => {
        const article = createElement("article", `team-chat-message${message.is_own ? " is-own" : ""}`);
        article.dataset.messageId = String(message.id);
        article.append(createElement("div", "team-chat-avatar", initials(message.author_name)));

        const content = createElement("div", "team-chat-message-content");
        const meta = createElement("div", "team-chat-message-meta");
        meta.append(createElement("strong", "", message.author_name));
        meta.append(createElement("time", "", formatDate(message.created_at)));
        if (message.edited_at) meta.append(createElement("span", "", "редактирано"));
        if (message.is_pinned) {
            const pinned = createElement("span", "team-chat-pinned-label", "закачено");
            pinned.prepend(createElement("span", "material-symbols-outlined", "keep"));
            meta.append(pinned);
        }
        content.append(meta);

        const bubble = createElement("div", "team-chat-bubble");
        if (message.reply_to) bubble.append(buildReplyQuote(message.reply_to));
        if (message.deleted) {
            bubble.append(createElement("div", "team-chat-deleted", "Съобщението е изтрито"));
        } else {
            if (message.text) {
                const text = createElement("div", "team-chat-text");
                appendHighlightedText(text, message.text, message.mentions || []);
                bubble.append(text);
            }
            if (message.shared_objects?.length) {
                const shares = createElement("div", "team-chat-shares");
                message.shared_objects.forEach((shared) => shares.append(buildSharedObject(shared)));
                bubble.append(shares);
            }
            if (message.attachments?.length) {
                bubble.append(buildAttachments(message.attachments));
            }
        }
        content.append(bubble);

        if (!message.deleted) {
            const actions = createElement("div", "team-chat-message-actions");
            actions.append(actionButton("reply", "Отговори", () => setMode({ reply: message })));
            if (scope === "general") {
                actions.append(actionButton(
                    message.origin_discussion ? "forum" : "add_comment",
                    message.origin_discussion ? "Отвори дискусията" : "Започни дискусия",
                    () => void startDiscussion(message),
                ));
            }
            if (message.can_edit) {
                actions.append(actionButton("edit", "Редактирай", () => setMode({ edit: message })));
            }
            if (message.can_delete) {
                actions.append(actionButton("delete", "Изтрий", () => void deleteChatMessage(message)));
            }
            if (message.can_pin) {
                actions.append(actionButton(
                    message.is_pinned ? "keep_off" : "keep",
                    message.is_pinned ? "Откачи" : "Закачи",
                    () => void togglePin(message),
                ));
            }
            content.append(actions);
            content.append(buildReactions(message));
        }
        article.append(content);
        return article;
    };

    const isNearBottom = () => (
        elements.messages.scrollHeight - elements.messages.scrollTop - elements.messages.clientHeight < 100
    );

    const scrollToBottom = () => {
        elements.messages.scrollTop = elements.messages.scrollHeight;
        elements.newMessages.hidden = true;
    };

    const renderMessages = ({ forceBottom = false, preserveScroll = false } = {}) => {
        const wasNearBottom = isNearBottom();
        const previousHeight = elements.messages.scrollHeight;
        const previousTop = elements.messages.scrollTop;
        const rows = [...state.messages.values()].sort((a, b) => a.id - b.id);
        const fragment = document.createDocumentFragment();
        rows.forEach((message) => fragment.append(buildMessage(message)));
        elements.messages.replaceChildren(fragment);
        elements.empty.hidden = rows.length > 0;
        elements.messages.setAttribute("aria-busy", "false");
        if (preserveScroll) {
            elements.messages.scrollTop = previousTop + (elements.messages.scrollHeight - previousHeight);
        } else if (forceBottom || wasNearBottom) {
            scrollToBottom();
        }
    };

    const mergeMessages = (messages) => {
        messages.forEach((message) => state.messages.set(message.id, message));
    };

    const replacePinnedMessages = (messages) => {
        state.pinnedMessages.clear();
        (messages || []).forEach((message) => {
            state.pinnedMessages.set(message.id, message);
        });
    };

    const renderPinnedMessages = () => {
        const rows = [...state.pinnedMessages.values()].sort((left, right) => (
            new Date(right.pinned_at) - new Date(left.pinned_at)
        ));
        elements.pinned.hidden = rows.length === 0;
        elements.pinnedCount.textContent = rows.length ? String(rows.length) : "";
        const fragment = document.createDocumentFragment();
        rows.forEach((message) => {
            const card = createElement("article", "team-chat-pinned-card");
            const meta = createElement("div", "team-chat-pinned-card-meta");
            meta.append(createElement("strong", "", message.author_name));
            meta.append(createElement("time", "", formatDate(message.created_at)));
            card.append(meta);
            card.append(createElement(
                "p",
                "",
                message.text || "Споделен административен запис",
            ));
            if (message.can_pin) {
                const unpin = createElement("button", "team-chat-pinned-unpin");
                unpin.type = "button";
                unpin.setAttribute("aria-label", "Откачи съобщението");
                unpin.append(createElement("span", "material-symbols-outlined", "keep_off"));
                unpin.addEventListener("click", () => void togglePin(message));
                card.append(unpin);
            }
            fragment.append(card);
        });
        elements.pinnedList.replaceChildren(fragment);
    };

    const maxMessageId = () => Math.max(0, ...state.messages.keys());
    const minMessageId = () => Math.min(...state.messages.keys());

    const markRead = async () => {
        if (
            document.visibilityState !== "visible"
            || state.messages.size === 0
            || !isNearBottom()
        ) return;
        try {
            await postJson(endpoints.read, { last_message_id: maxMessageId() });
            if (scope === "general") {
                window.dispatchEvent(new CustomEvent("team-chat:unread", { detail: { count: 0 } }));
            }
            window.dispatchEvent(new CustomEvent("team-chat:refresh-unread"));
        } catch {
            // A later poll will retry; reading the chat must never block the UI.
        }
    };

    const soundIsMuted = () => {
        try {
            return localStorage.getItem(soundStorageKey) === "muted";
        } catch {
            return false;
        }
    };

    const updateSoundButton = () => {
        const muted = soundIsMuted();
        elements.soundToggle.setAttribute("aria-pressed", String(muted));
        elements.soundToggle.querySelector(".material-symbols-outlined").textContent = muted ? "volume_off" : "volume_up";
        elements.soundToggle.lastElementChild.textContent = muted ? "Звукът е изключен" : "Звукът е включен";
    };

    const playSound = () => {
        if (soundIsMuted()) return;
        try {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (!AudioContext) return;
            const context = new AudioContext();
            const oscillator = context.createOscillator();
            const gain = context.createGain();
            oscillator.frequency.value = 620;
            gain.gain.setValueAtTime(.035, context.currentTime);
            gain.gain.exponentialRampToValueAtTime(.001, context.currentTime + .11);
            oscillator.connect(gain);
            gain.connect(context.destination);
            oscillator.start();
            oscillator.stop(context.currentTime + .11);
            oscillator.addEventListener("ended", () => void context.close());
        } catch {
            // Browsers may block audio before the first user interaction.
        }
    };

    const loadInitial = async () => {
        elements.loading.hidden = false;
        elements.messages.setAttribute("aria-busy", "true");
        try {
            const payload = await getJson(endpoints.messages);
            mergeMessages(payload.messages);
            replacePinnedMessages(payload.pinned_messages);
            state.hasMore = payload.has_more;
            state.serverTime = payload.server_time;
            elements.loadOlder.hidden = !state.hasMore;
            renderMessages({ forceBottom: true });
            renderPinnedMessages();
            state.initialized = true;
            setStatus("");
            await markRead();
        } catch (error) {
            elements.messages.setAttribute("aria-busy", "false");
            setStatus(error.message, true);
        } finally {
            elements.loading.hidden = true;
        }
    };

    const poll = async () => {
        window.clearTimeout(state.pollTimer);
        if (!state.initialized) {
            await loadInitial();
            state.pollTimer = window.setTimeout(() => void poll(), 5000);
            return;
        }
        try {
            const query = new URLSearchParams({
                after_id: String(maxMessageId()),
                updated_after: state.serverTime,
            });
            const payload = await getJson(`${endpoints.messages}?${query}`);
            const previousMax = maxMessageId();
            const newFromOthers = payload.messages.some(
                (message) => message.id > previousMax && !message.is_own && !message.deleted,
            );
            const shouldStick = isNearBottom();
            mergeMessages(payload.messages);
            replacePinnedMessages(payload.pinned_messages);
            state.serverTime = payload.server_time;
            renderPinnedMessages();
            if (payload.messages.length) {
                renderMessages({ forceBottom: shouldStick });
                if (!shouldStick && newFromOthers) elements.newMessages.hidden = false;
                if (newFromOthers) playSound();
                if (shouldStick) await markRead();
            }
        } catch {
            // Keep the current messages and retry silently after the interval.
        } finally {
            state.pollTimer = window.setTimeout(() => void poll(), 5000);
        }
    };

    const loadOlder = async () => {
        if (!state.hasMore || state.messages.size === 0) return;
        elements.loadOlder.disabled = true;
        try {
            const query = new URLSearchParams({ before_id: String(minMessageId()) });
            const payload = await getJson(`${endpoints.messages}?${query}`);
            mergeMessages(payload.messages);
            state.hasMore = payload.has_more;
            elements.loadOlder.hidden = !state.hasMore;
            renderMessages({ preserveScroll: true });
        } catch (error) {
            setStatus(error.message, true);
        } finally {
            elements.loadOlder.disabled = false;
        }
    };

    const updateCharacterCount = () => {
        elements.characterCount.textContent = `${elements.text.value.length} / 4000`;
    };

    const clearAttachmentPreviews = () => {
        state.previewUrls.forEach((url) => URL.revokeObjectURL(url));
        state.previewUrls = [];
        state.attachments = [];
        elements.fileInput.value = "";
        elements.attachmentPreview.replaceChildren();
        elements.attachmentPreview.hidden = true;
    };

    const renderAttachmentPreviews = () => {
        state.previewUrls.forEach((url) => URL.revokeObjectURL(url));
        state.previewUrls = [];
        const fragment = document.createDocumentFragment();
        state.attachments.forEach((file, index) => {
            const card = createElement("div", "team-chat-preview-card");
            const url = URL.createObjectURL(file);
            state.previewUrls.push(url);
            const image = createElement("img");
            image.src = url;
            image.alt = "Избрано изображение";
            card.append(image);
            const name = createElement("span", "", file.name || `Изображение ${index + 1}`);
            card.append(name);
            const remove = createElement("button");
            remove.type = "button";
            remove.setAttribute("aria-label", `Премахни ${file.name || "изображението"}`);
            remove.append(createElement("span", "material-symbols-outlined", "close"));
            remove.addEventListener("click", () => {
                state.attachments.splice(index, 1);
                renderAttachmentPreviews();
            });
            card.append(remove);
            fragment.append(card);
        });
        elements.attachmentPreview.replaceChildren(fragment);
        elements.attachmentPreview.hidden = state.attachments.length === 0;
    };

    const addAttachments = (incoming) => {
        const allowed = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
        const files = [...incoming];
        for (const file of files) {
            if (!allowed.has(file.type)) {
                setStatus("Разрешени са само JPEG, PNG, WebP и GIF изображения.", true);
                continue;
            }
            if (file.size > 5 * 1024 * 1024) {
                setStatus(`„${file.name}“ е по-голямо от 5 MB.`, true);
                continue;
            }
            if (state.attachments.length >= 3) {
                setStatus("Към едно съобщение може да има най-много 3 изображения.", true);
                break;
            }
            state.attachments.push(file);
        }
        renderAttachmentPreviews();
    };

    const resetComposer = () => {
        elements.text.value = "";
        clearAttachmentPreviews();
        setMode();
        updateCharacterCount();
        hideMentionSuggestions();
    };

    const submitMessage = async () => {
        const text = elements.text.value.trim();
        if (!text && !state.attachments.length && !state.editing) {
            setStatus("Напишете съобщение или добавете изображение.", true);
            return;
        }
        elements.submit.disabled = true;
        setStatus(state.editing ? "Запазване…" : "Изпращане…");
        try {
            let payload;
            if (state.editing) {
                payload = await postJson(
                    endpointForMessage(endpoints.editTemplate, state.editing.id),
                    { text },
                );
            } else {
                const formData = new FormData();
                formData.append("text", text);
                if (state.replyTo?.id) formData.append("reply_to_id", String(state.replyTo.id));
                state.attachments.forEach((file) => formData.append("images", file, file.name));
                payload = await postForm(endpoints.messages, formData);
            }
            state.messages.set(payload.message.id, payload.message);
            resetComposer();
            renderMessages({ forceBottom: true });
            setStatus("");
            await markRead();
        } catch (error) {
            setStatus(error.message, true);
        } finally {
            elements.submit.disabled = false;
        }
    };

    const mentionContext = () => {
        const caret = elements.text.selectionStart;
        const beforeCaret = elements.text.value.slice(0, caret);
        const match = beforeCaret.match(/@([\p{L}\p{N}_.+\-]{0,50})$/u);
        if (!match) return null;
        return {
            query: match[1],
            start: caret - match[0].length,
            end: caret,
        };
    };

    const hideMentionSuggestions = () => {
        elements.mentions.hidden = true;
        elements.mentions.replaceChildren();
        state.mentionRange = null;
        state.mentionResults = [];
        state.mentionIndex = 0;
    };

    const chooseMention = (username) => {
        if (!state.mentionRange) return;
        const { start, end } = state.mentionRange;
        elements.text.setRangeText(`@${username} `, start, end, "end");
        hideMentionSuggestions();
        elements.text.focus();
        updateCharacterCount();
    };

    const renderMentionSuggestions = () => {
        elements.mentions.replaceChildren();
        if (!state.mentionResults.length) {
            elements.mentions.hidden = true;
            return;
        }
        state.mentionResults.forEach((person, index) => {
            const button = createElement(
                "button",
                `team-chat-mention-option${index === state.mentionIndex ? " is-active" : ""}`,
            );
            button.type = "button";
            button.setAttribute("role", "option");
            button.setAttribute("aria-selected", String(index === state.mentionIndex));
            button.append(createElement("strong", "", `@${person.username}`));
            button.append(createElement("span", "", person.display_name));
            button.addEventListener("mousedown", (event) => event.preventDefault());
            button.addEventListener("click", () => chooseMention(person.username));
            elements.mentions.append(button);
        });
        elements.mentions.hidden = false;
    };

    const searchMentions = () => {
        window.clearTimeout(state.mentionTimer);
        const context = mentionContext();
        if (!context) {
            hideMentionSuggestions();
            return;
        }
        state.mentionRange = context;
        state.mentionTimer = window.setTimeout(async () => {
            try {
                const target = new URL(endpoints.staff, window.location.href);
                target.searchParams.set("q", context.query);
                const payload = await getJson(target.toString());
                state.mentionResults = payload.results;
                state.mentionIndex = 0;
                renderMentionSuggestions();
            } catch {
                hideMentionSuggestions();
            }
        }, 150);
    };

    elements.form.addEventListener("submit", (event) => {
        event.preventDefault();
        void submitMessage();
    });
    elements.text.addEventListener("input", () => {
        updateCharacterCount();
        searchMentions();
    });
    elements.text.addEventListener("keydown", (event) => {
        if (!elements.mentions.hidden && state.mentionResults.length) {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const direction = event.key === "ArrowDown" ? 1 : -1;
                state.mentionIndex = (
                    state.mentionIndex + direction + state.mentionResults.length
                ) % state.mentionResults.length;
                renderMentionSuggestions();
                return;
            }
            if (event.key === "Enter") {
                event.preventDefault();
                chooseMention(state.mentionResults[state.mentionIndex].username);
                return;
            }
            if (event.key === "Escape") {
                event.preventDefault();
                hideMentionSuggestions();
                return;
            }
        }
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            void submitMessage();
        }
    });
    elements.modeCancel.addEventListener("click", () => {
        if (state.editing) elements.text.value = "";
        setMode();
        updateCharacterCount();
        elements.text.focus();
    });
    elements.loadOlder.addEventListener("click", () => void loadOlder());
    elements.newMessages.addEventListener("click", () => {
        scrollToBottom();
        void markRead();
    });
    elements.messages.addEventListener("scroll", () => {
        window.clearTimeout(state.readTimer);
        if (!isNearBottom()) return;
        elements.newMessages.hidden = true;
        state.readTimer = window.setTimeout(() => void markRead(), 150);
    });
    elements.soundToggle.addEventListener("click", () => {
        try {
            localStorage.setItem(soundStorageKey, soundIsMuted() ? "enabled" : "muted");
        } catch {
            // The current page still works when storage is unavailable.
        }
        updateSoundButton();
    });
    elements.attach.addEventListener("click", () => elements.fileInput.click());
    elements.fileInput.addEventListener("change", () => {
        addAttachments(elements.fileInput.files);
        elements.fileInput.value = "";
    });
    ["dragenter", "dragover"].forEach((eventName) => {
        elements.dropzone.addEventListener(eventName, (event) => {
            event.preventDefault();
            if (event.dataTransfer?.types?.includes("Files")) {
                elements.dropzone.classList.add("is-dragging");
            }
        });
    });
    ["dragleave", "drop"].forEach((eventName) => {
        elements.dropzone.addEventListener(eventName, (event) => {
            event.preventDefault();
            elements.dropzone.classList.remove("is-dragging");
        });
    });
    elements.dropzone.addEventListener("drop", (event) => {
        if (event.dataTransfer?.files?.length) addAttachments(event.dataTransfer.files);
    });
    elements.text.addEventListener("paste", (event) => {
        const images = [...(event.clipboardData?.files || [])].filter((file) => (
            file.type.startsWith("image/")
        ));
        if (images.length) addAttachments(images);
    });
    elements.lightboxClose.addEventListener("click", () => elements.lightbox.close());
    elements.lightbox.addEventListener("click", (event) => {
        if (event.target === elements.lightbox) elements.lightbox.close();
    });
    elements.lightbox.addEventListener("close", () => {
        elements.lightboxImage.removeAttribute("src");
    });
    if (elements.resolve && endpoints.resolve) {
        elements.resolve.addEventListener("click", async () => {
            const currentlyResolved = elements.resolve.dataset.resolved === "true";
            elements.resolve.disabled = true;
            try {
                await postJson(endpoints.resolve, { resolved: !currentlyResolved });
                window.location.reload();
            } catch (error) {
                setStatus(error.message, true);
                elements.resolve.disabled = false;
            }
        });
    }
    document.addEventListener("click", (event) => {
        if (!elements.mentions.contains(event.target) && event.target !== elements.text) {
            hideMentionSuggestions();
        }
    });
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") void markRead();
    });
    window.addEventListener("beforeunload", () => {
        state.previewUrls.forEach((url) => URL.revokeObjectURL(url));
    });

    updateSoundButton();
    updateCharacterCount();
    void loadInitial().then(() => {
        state.pollTimer = window.setTimeout(() => void poll(), 5000);
    });
})();
