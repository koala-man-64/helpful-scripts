# File Manager Application

A modern web-based file management system with advanced document analysis capabilities.

## Features

- **Hierarchical File Tree**: Browse and navigate through folders and files with expand/collapse functionality
- **Document Viewer**: View documents with multiple analysis types:
  - Original content
  - AI-generated summary
  - Key points extraction
  - Sentiment analysis
  - Entity recognition
- **Integrated Chat**: AI-powered chat assistant with:
  - Preset prompts for common tasks
  - Context-aware responses (current document or all selected documents)
  - Resizable chat window
- **Advanced UI**:
  - Resizable and collapsible panels
  - Search and filter functionality
  - File type filtering (.md, .txt, .pdf, .docx)
  - Select all/none functionality

## Technology Stack

- React 18.3
- TypeScript
- Tailwind CSS v4
- Vite
- Lucide Icons
- React Resizable Panels

## Getting Started

### Installation

```bash
pnpm install
```

### Development

```bash
pnpm run dev
```

### Build

```bash
pnpm run build
```

## Usage

1. Select files from the tree view on the left
2. Use the search box to filter files by name
3. View document content in the center panel
4. Switch between analysis types using the buttons (Original, Summary, Key Points, Sentiment, Entities)
5. Open the chat bubble to interact with the AI assistant
6. Toggle between using current document or all selected documents as context

## Project Structure

```
file-manager-app/
├── src/
│   ├── app/
│   │   ├── components/
│   │   │   ├── ChatWindow.tsx      # Resizable chat interface
│   │   │   ├── FileTree.tsx        # Hierarchical file browser
│   │   │   ├── FileViewer.tsx      # Document viewer with analysis
│   │   │   └── ui/                 # Shadcn UI components
│   │   └── App.tsx                 # Main application
│   └── styles/                     # CSS and theme files
├── package.json
└── vite.config.ts
```

## Features in Detail

### File Tree
- Hierarchical folder/file structure
- Checkbox selection for folders and files
- Search/filter by filename
- Select all/none buttons
- Collapsible panel

### Document Analysis
- **Original**: Raw document content
- **Summary**: AI-generated concise summary
- **Key Points**: Bulleted list of main points
- **Sentiment**: Emotional tone analysis with scoring
- **Entities**: Extracted people, dates, metrics, and organizations

### Chat Assistant
- Preset prompts: Help, Analyze, Compare, Summarize, Export
- Context modes: Current document or all selected documents
- Resizable window with minimize functionality
- Real-time message timestamps

## License

MIT
