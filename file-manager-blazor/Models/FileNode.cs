namespace FileManagerBlazor.Models;

public enum FileNodeType
{
    Folder,
    File
}

public sealed record FileNode(
    string Id,
    string Name,
    FileNodeType Type,
    IReadOnlyList<FileNode>? Children = null);
