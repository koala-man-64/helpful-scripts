window.bulkAnalysisDownloads = {
    downloadTextFile(fileName, content, mimeType) {
        const blob = new Blob([content], { type: mimeType || "text/plain;charset=utf-8" });
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
};
