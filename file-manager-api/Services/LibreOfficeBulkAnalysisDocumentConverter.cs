using System.ComponentModel;
using System.Diagnostics;

namespace FileManagerApi.Services;

public sealed class LibreOfficeBulkAnalysisDocumentConverter(
    BulkAnalysisRenderingOptions options,
    ILogger<LibreOfficeBulkAnalysisDocumentConverter> logger) : IBulkAnalysisDocumentConverter
{
    public async Task<byte[]> ConvertDocToDocxAsync(string sourceFileName, byte[] content, CancellationToken cancellationToken = default)
    {
        var executablePath = string.IsNullOrWhiteSpace(options.LibreOfficePath)
            ? "soffice"
            : options.LibreOfficePath;

        var workingDirectory = Path.Combine(Path.GetTempPath(), "bulk-analysis-doc-preview", Guid.NewGuid().ToString("N"));
        var outputDirectory = Path.Combine(workingDirectory, "out");
        Directory.CreateDirectory(outputDirectory);

        var inputPath = Path.Combine(workingDirectory, Path.GetFileName(sourceFileName));
        await File.WriteAllBytesAsync(inputPath, content, cancellationToken);

        try
        {
            using var process = CreateProcess(executablePath, inputPath, outputDirectory);
            try
            {
                process.Start();
            }
            catch (Win32Exception ex)
            {
                throw new InvalidOperationException(
                    "Legacy .doc preview requires LibreOffice. Configure BulkAnalysisRendering:LibreOfficePath or add soffice to PATH.",
                    ex);
            }

            var standardOutputTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
            var standardErrorTask = process.StandardError.ReadToEndAsync(cancellationToken);
            await process.WaitForExitAsync(cancellationToken);

            var standardOutput = await standardOutputTask;
            var standardError = await standardErrorTask;
            if (process.ExitCode != 0)
            {
                var detail = string.IsNullOrWhiteSpace(standardError) ? standardOutput.Trim() : standardError.Trim();
                throw new InvalidOperationException(
                    $"Legacy .doc preview conversion failed with exit code {process.ExitCode}: {detail}");
            }

            var expectedPath = Path.Combine(outputDirectory, $"{Path.GetFileNameWithoutExtension(sourceFileName)}.docx");
            var outputPath = File.Exists(expectedPath)
                ? expectedPath
                : Directory.EnumerateFiles(outputDirectory, "*.docx").SingleOrDefault();

            if (string.IsNullOrWhiteSpace(outputPath) || !File.Exists(outputPath))
            {
                throw new InvalidOperationException("Legacy .doc preview conversion did not produce a .docx file.");
            }

            logger.LogInformation("Converted legacy .doc result {FileName} to DOCX preview.", sourceFileName);
            return await File.ReadAllBytesAsync(outputPath, cancellationToken);
        }
        finally
        {
            try
            {
                if (Directory.Exists(workingDirectory))
                {
                    Directory.Delete(workingDirectory, recursive: true);
                }
            }
            catch (Exception ex)
            {
                logger.LogDebug(ex, "Unable to clean temporary legacy .doc preview directory {Directory}.", workingDirectory);
            }
        }
    }

    private static Process CreateProcess(string executablePath, string inputPath, string outputDirectory)
    {
        var startInfo = new ProcessStartInfo(executablePath)
        {
            RedirectStandardError = true,
            RedirectStandardOutput = true,
            UseShellExecute = false,
            CreateNoWindow = true
        };

        startInfo.ArgumentList.Add("--headless");
        startInfo.ArgumentList.Add("--convert-to");
        startInfo.ArgumentList.Add("docx");
        startInfo.ArgumentList.Add("--outdir");
        startInfo.ArgumentList.Add(outputDirectory);
        startInfo.ArgumentList.Add(inputPath);

        return new Process
        {
            StartInfo = startInfo
        };
    }
}
