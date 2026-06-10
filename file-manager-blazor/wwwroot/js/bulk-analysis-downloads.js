window.bulkAnalysisDownloads = {
    downloadTextFile(fileName, content, mimeType) {
        downloadBlob(fileName, [content], mimeType || "text/plain;charset=utf-8");
    },

    downloadBinaryFile(fileName, content, mimeType) {
        const bytes = content instanceof Uint8Array
            ? content
            : new Uint8Array(content);

        downloadBlob(fileName, [bytes], mimeType || "application/octet-stream");
    }
};

function downloadBlob(fileName, parts, mimeType) {
    const blob = new Blob(parts, { type: mimeType });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");

    anchor.href = url;
    anchor.download = fileName;
    anchor.style.display = "none";

    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();

    window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
