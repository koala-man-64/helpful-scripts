namespace FileManagerBlazor.Models;

public enum ChatSender
{
    User,
    Assistant
}

public sealed record ChatMessage(
    string Id,
    string Text,
    ChatSender Sender,
    DateTime Timestamp);
