# Setup Instructions

This file manager application was built using Figma Make. It now defaults to an assistant-shell landing page with the original file manager preserved inside a right-side document workspace.

## Option 1: View in Figma Make

This project is best viewed and edited in Figma Make, where it was originally created.

## Option 2: Local Setup

### Prerequisites
- Node.js 18+ 
- pnpm package manager

### Installation

1. Install dependencies:
```bash
pnpm install
```

2. Run the development server:
```bash
pnpm run dev
```

3. Open your browser to the provided local URL

### Project Structure

```
file-manager-app/
├── src/
│   ├── app/
│   │   ├── App.tsx                    # Main application component
│   │   └── components/
│   │       ├── FileTree.tsx           # Hierarchical file browser with search
│   │       ├── FileViewer.tsx         # Document viewer with analysis modes
│   │       ├── ChatWindow.tsx         # Resizable chat interface
│   │       └── ui/                    # Shadcn UI component library
│   └── styles/
│       ├── globals.css
│       ├── tailwind.css
│       └── theme.css
├── package.json
├── vite.config.ts
└── postcss.config.mjs
```

## Key Components

### FileTree Component
- Displays hierarchical folder/file structure
- Checkbox selection for files and folders
- Search and filter functionality
- Filters to show only .md, .txt, .pdf, .docx files
- Select all/none buttons
- Collapsible panel

### FileViewer Component  
- Displays selected document content
- Five analysis modes:
  - **Original**: Raw document text
  - **Summary**: AI-generated summary
  - **Key Points**: Bulleted highlights
  - **Sentiment**: Emotional tone analysis
  - **Entities**: Extracted people, dates, metrics
- Navigation between multiple selected files
- Icon-based mode selector

### ChatWindow Component
- Floating chat interface
- Preset prompts for common tasks
- Context selector (current document vs all selected)
- Resizable window (drag from edges/corners)
- Minimize/restore functionality
- Real-time message timestamps

### Assistant Shell
- Left navigation rail and branded landing view
- Centered greeting/composer layout matching the embedded-host mock
- Recommended actions block with workspace entry point
- Floating launchers for chat and workspace
- URL params for documentation capture:
  - `?workspace=open`
  - `?chat=open`
  - `?chat=minimized`
  - `?prompt=...`

## Features

- **Responsive Panels**: Resize file tree and viewer panels inside the workspace
- **File Filtering**: Only document types (.md, .txt, .pdf, .docx)
- **Search**: Filter files by name with highlight
- **Selection Management**: Checkbox selection with parent/child relationship
- **Document Analysis**: Multiple AI-powered analysis types
- **Chat Integration**: Context-aware AI assistant
- **Host Shell Mock**: Assistant landing page shell for embedded-app demos

## Technologies

- React 18.3
- TypeScript
- Tailwind CSS v4
- Vite
- Lucide React (icons)
- React Resizable Panels
- Radix UI primitives

## Development Notes

- Built with Figma Make for rapid prototyping
- Uses Tailwind CSS v4 (latest)
- Shadcn UI components for consistent design system
- Mock data for demonstration purposes
- Analysis results are pre-generated examples

For the complete source code, see the src/ directory.

Tracked screenshots live in `docs/screenshots/`.
