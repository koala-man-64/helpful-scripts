window.bulkAnalysisPreview = (() => {
    const objectUrls = new WeakMap();

    function clear(container) {
        const previousUrl = objectUrls.get(container);
        if (previousUrl) {
            URL.revokeObjectURL(previousUrl);
            objectUrls.delete(container);
        }

        container.replaceChildren();
    }

    async function render(container, fileName, contentType, content) {
        clear(container);

        const bytes = content instanceof Uint8Array ? content : new Uint8Array(content);
        const blob = new Blob([bytes], { type: contentType || "application/octet-stream" });
        const url = URL.createObjectURL(blob);
        objectUrls.set(container, url);

        const normalizedName = (fileName || "").toLowerCase();
        if ((contentType || "").includes("html") || normalizedName.endsWith(".html") || normalizedName.endsWith(".htm")) {
            renderHtml(container, url, fileName);
            return;
        }

        if ((contentType || "").includes("pdf") || normalizedName.endsWith(".pdf")) {
            renderPdf(container, url, fileName);
            return;
        }

        if (normalizedName.endsWith(".docx")) {
            await renderDocx(container, blob, url, fileName);
            return;
        }

        if (isPlainTextPreview(contentType, normalizedName)) {
            renderPlainText(container, bytes);
            return;
        }

        renderFallback(container, url, fileName, "Preview is not available for this file type.");
    }

    function renderHtml(container, url, fileName) {
        const frame = document.createElement("iframe");
        frame.className = "raw-html-preview";
        frame.title = fileName || "HTML preview";
        frame.sandbox = "";
        frame.referrerPolicy = "no-referrer";
        frame.src = url;
        container.appendChild(frame);
    }

    function renderPdf(container, url, fileName) {
        const frame = document.createElement("iframe");
        frame.className = "raw-pdf-preview";
        frame.title = fileName || "PDF preview";
        frame.src = url;
        container.appendChild(frame);
    }

    async function renderDocx(container, blob, url, fileName) {
        if (!window.docx?.renderAsync) {
            renderFallback(container, url, fileName, "Word preview library is unavailable.");
            return;
        }

        const host = document.createElement("div");
        host.className = "raw-docx-preview";
        container.appendChild(host);

        try {
            await window.docx.renderAsync(blob, host, null, {
                className: "docx",
                inWrapper: true,
                ignoreWidth: false,
                ignoreHeight: false,
                renderHeaders: true,
                renderFooters: true
            });
        } catch {
            clear(container);
            renderFallback(container, url, fileName, "Word preview could not render this document.");
        }
    }

    function isPlainTextPreview(contentType, normalizedName) {
        const normalizedType = (contentType || "").toLowerCase();
        return normalizedType.startsWith("text/")
            || normalizedType.includes("json")
            || normalizedName.endsWith(".txt")
            || normalizedName.endsWith(".md")
            || normalizedName.endsWith(".csv")
            || normalizedName.endsWith(".json");
    }

    function renderPlainText(container, bytes) {
        const pre = document.createElement("pre");
        pre.textContent = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
        container.appendChild(pre);
    }

    function renderFallback(container, url, fileName, message) {
        const panel = document.createElement("div");
        panel.className = "raw-preview-fallback";

        const title = document.createElement("strong");
        title.textContent = fileName || "Document";

        const copy = document.createElement("span");
        copy.textContent = message;

        const link = document.createElement("a");
        link.href = url;
        link.download = fileName || "document";
        link.textContent = "Download file";

        panel.append(title, copy, link);
        container.appendChild(panel);
    }

    return { clear, render };
})();
