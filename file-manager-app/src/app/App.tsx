import { useState } from 'react';
import { Home, Settings, Users, FileText, MessageCircle, PanelLeftOpen } from 'lucide-react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import FileTree from './components/FileTree';
import FileViewer from './components/FileViewer';
import ChatWindow from './components/ChatWindow';

const sampleFileStructure = [
  {
    id: '1',
    name: 'Documents',
    type: 'folder' as const,
    children: [
      {
        id: '1-1',
        name: 'Work',
        type: 'folder' as const,
        children: [
          { id: '1-1-1', name: 'report.pdf', type: 'file' as const },
          { id: '1-1-2', name: 'quarterly-review.docx', type: 'file' as const },
          { id: '1-1-3', name: 'meeting-notes.txt', type: 'file' as const },
        ],
      },
      {
        id: '1-2',
        name: 'Personal',
        type: 'folder' as const,
        children: [
          { id: '1-2-1', name: 'resume.pdf', type: 'file' as const },
          { id: '1-2-2', name: 'notes.txt', type: 'file' as const },
          { id: '1-2-3', name: 'journal.docx', type: 'file' as const },
        ],
      },
    ],
  },
  {
    id: '2',
    name: 'Research',
    type: 'folder' as const,
    children: [
      { id: '2-1', name: 'findings.md', type: 'file' as const },
      { id: '2-2', name: 'bibliography.txt', type: 'file' as const },
      {
        id: '2-3',
        name: 'Papers',
        type: 'folder' as const,
        children: [
          { id: '2-3-1', name: 'whitepaper.pdf', type: 'file' as const },
          { id: '2-3-2', name: 'draft.docx', type: 'file' as const },
        ],
      },
    ],
  },
  {
    id: '3',
    name: 'Projects',
    type: 'folder' as const,
    children: [
      { id: '3-1', name: 'README.md', type: 'file' as const },
      { id: '3-2', name: 'CHANGELOG.md', type: 'file' as const },
      { id: '3-3', name: 'project-plan.pdf', type: 'file' as const },
    ],
  },
];

export default function App() {
  const [selectedFiles, setSelectedFiles] = useState<Array<{ id: string; name: string; path: string }>>([]);
  const [currentFileIndex, setCurrentFileIndex] = useState(0);
  const [isTreeCollapsed, setIsTreeCollapsed] = useState(false);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [isChatMinimized, setIsChatMinimized] = useState(false);

  const handleChatToggle = () => {
    if (isChatOpen && isChatMinimized) {
      setIsChatMinimized(false);
    } else {
      setIsChatOpen(!isChatOpen);
      setIsChatMinimized(false);
    }
  };

  return (
    <div className="size-full flex">
      {/* Left Navigation Panel */}
      <nav className="w-64 border-r border-gray-200 bg-gray-50 p-4">
        <div className="mb-8">
          <h1 className="font-semibold text-gray-900">My App</h1>
        </div>

        <ul className="space-y-2">
          <li>
            <a href="#" className="flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-900 text-white">
              <Home size={20} />
              <span>Home</span>
            </a>
          </li>
          <li>
            <a href="#" className="flex items-center gap-3 px-3 py-2 rounded-lg text-gray-700 hover:bg-gray-200">
              <Users size={20} />
              <span>Users</span>
            </a>
          </li>
          <li>
            <a href="#" className="flex items-center gap-3 px-3 py-2 rounded-lg text-gray-700 hover:bg-gray-200">
              <FileText size={20} />
              <span>Documents</span>
            </a>
          </li>
          <li>
            <a href="#" className="flex items-center gap-3 px-3 py-2 rounded-lg text-gray-700 hover:bg-gray-200">
              <Settings size={20} />
              <span>Settings</span>
            </a>
          </li>
        </ul>
      </nav>

      {/* Right Content Panel */}
      <main className="flex-1 overflow-hidden bg-gray-50">
        <div className="p-8 pb-4">
          <h2 className="mb-6">File Manager</h2>
        </div>

        <div className="px-8 h-[calc(100%-7rem)]">
          <PanelGroup direction="horizontal">
            {!isTreeCollapsed ? (
              <>
                <Panel defaultSize={40} minSize={20} maxSize={60}>
                  <FileTree
                    data={sampleFileStructure}
                    onSelectionChange={setSelectedFiles}
                    onCollapse={() => setIsTreeCollapsed(true)}
                  />
                </Panel>
                <PanelResizeHandle className="w-2 hover:bg-blue-500 hover:bg-opacity-20 transition-colors mx-3" />
              </>
            ) : (
              <div className="mr-3">
                <button
                  onClick={() => setIsTreeCollapsed(false)}
                  className="p-2 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 shadow-sm"
                  aria-label="Show file tree"
                >
                  <PanelLeftOpen size={20} className="text-gray-600" />
                </button>
              </div>
            )}
            <Panel minSize={40}>
              <FileViewer
                selectedFiles={selectedFiles}
                onFileIndexChange={setCurrentFileIndex}
              />
            </Panel>
          </PanelGroup>
        </div>
      </main>

      {/* Floating Chat Bubble */}
      {!isChatOpen && (
        <button
          onClick={handleChatToggle}
          className="fixed bottom-6 right-6 w-14 h-14 bg-blue-600 text-white rounded-full shadow-lg hover:bg-blue-700 flex items-center justify-center transition-transform hover:scale-110"
          aria-label="Open chat"
        >
          <MessageCircle size={24} />
        </button>
      )}

      {/* Chat Window */}
      <ChatWindow
        isOpen={isChatOpen}
        onClose={() => setIsChatOpen(false)}
        isMinimized={isChatMinimized}
        onMinimize={() => setIsChatMinimized(!isChatMinimized)}
        selectedFiles={selectedFiles}
        currentFileIndex={currentFileIndex}
      />
    </div>
  );
}