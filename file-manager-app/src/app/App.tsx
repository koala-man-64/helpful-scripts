import { useState } from 'react';
import {
  Bot,
  Clock3,
  FolderOpen,
  MessageCircle,
  Mic,
  Paperclip,
  PanelLeftOpen,
  Plus,
  Settings,
  Shield,
  Sparkles,
} from 'lucide-react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import FileTree from './components/FileTree';
import FileViewer from './components/FileViewer';
import ChatWindow from './components/ChatWindow';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from './components/ui/sheet';

interface TreeNode {
  id: string;
  name: string;
  type: 'folder' | 'file';
  children?: TreeNode[];
}

const sampleFileStructure: TreeNode[] = [
  {
    id: '1',
    name: 'Documents',
    type: 'folder',
    children: [
      {
        id: '1-1',
        name: 'Work',
        type: 'folder',
        children: [
          { id: '1-1-1', name: 'report.pdf', type: 'file' },
          { id: '1-1-2', name: 'quarterly-review.docx', type: 'file' },
          { id: '1-1-3', name: 'meeting-notes.txt', type: 'file' },
        ],
      },
      {
        id: '1-2',
        name: 'Personal',
        type: 'folder',
        children: [
          { id: '1-2-1', name: 'resume.pdf', type: 'file' },
          { id: '1-2-2', name: 'notes.txt', type: 'file' },
          { id: '1-2-3', name: 'journal.docx', type: 'file' },
        ],
      },
    ],
  },
  {
    id: '2',
    name: 'Research',
    type: 'folder',
    children: [
      { id: '2-1', name: 'findings.md', type: 'file' },
      { id: '2-2', name: 'bibliography.txt', type: 'file' },
      {
        id: '2-3',
        name: 'Papers',
        type: 'folder',
        children: [
          { id: '2-3-1', name: 'whitepaper.pdf', type: 'file' },
          { id: '2-3-2', name: 'draft.docx', type: 'file' },
        ],
      },
    ],
  },
  {
    id: '3',
    name: 'Projects',
    type: 'folder',
    children: [
      { id: '3-1', name: 'README.md', type: 'file' },
      { id: '3-2', name: 'CHANGELOG.md', type: 'file' },
      { id: '3-3', name: 'project-plan.pdf', type: 'file' },
    ],
  },
];

interface HostSidebarProps {
  onWorkspaceOpen: () => void;
}

function HostSidebar({ onWorkspaceOpen }: HostSidebarProps) {
  return (
    <aside className="flex h-screen flex-col border-r border-[var(--shell-rail-border)] bg-[var(--shell-rail-background)]">
      <div className="flex h-[84px] items-center justify-center">
        <button
          type="button"
          className="flex h-12 w-12 items-center justify-center rounded-2xl text-[2.15rem] font-semibold tracking-[-0.08em] text-transparent transition-transform hover:scale-[1.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)] focus-visible:ring-offset-2"
          aria-label="Strider home"
        >
          <span className="bg-gradient-to-br from-emerald-500 via-green-600 to-cyan-600 bg-clip-text">S</span>
        </button>
      </div>

      <div className="flex justify-center px-4 pb-8">
        <button
          type="button"
          onClick={onWorkspaceOpen}
          className="flex h-11 w-11 items-center justify-center rounded-xl border border-[var(--shell-rail-border-strong)] bg-white text-[var(--shell-icon)] shadow-[var(--shell-icon-shadow)] transition-all hover:-translate-y-0.5 hover:border-[var(--shell-accent)] hover:text-[var(--shell-accent-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
          aria-label="Open document workspace"
        >
          <Plus size={18} />
        </button>
      </div>

      <div className="mx-0 border-t border-[var(--shell-rail-border)]" />

      <nav className="flex flex-1 flex-col items-center gap-9 px-3 py-8">
        <button
          type="button"
          className="rounded-2xl p-3 text-[var(--shell-icon)] transition-colors hover:bg-white hover:text-[var(--shell-accent-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
          aria-label="History"
        >
          <Clock3 size={23} strokeWidth={1.9} />
        </button>
        <button
          type="button"
          className="rounded-2xl p-3 text-[var(--shell-icon)] transition-colors hover:bg-white hover:text-[var(--shell-accent-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
          aria-label="Agents"
        >
          <Bot size={23} strokeWidth={1.9} />
        </button>
        <button
          type="button"
          className="rounded-2xl p-3 text-[var(--shell-icon)] transition-colors hover:bg-white hover:text-[var(--shell-accent-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
          aria-label="Trust center"
        >
          <Shield size={23} strokeWidth={1.9} />
        </button>
      </nav>

      <div className="flex justify-center px-3 pb-8">
        <button
          type="button"
          className="rounded-2xl p-3 text-[var(--shell-icon)] transition-colors hover:bg-white hover:text-[var(--shell-accent-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
          aria-label="Settings"
        >
          <Settings size={23} strokeWidth={1.9} />
        </button>
      </div>

      <div className="border-t border-[var(--shell-rail-border)] px-4 py-4">
        <button
          type="button"
          className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-[radial-gradient(circle_at_30%_30%,#c7f0ff_0%,#6ec6c1_36%,#2d737b_100%)] text-sm font-semibold text-white shadow-[var(--shell-avatar-shadow)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
          aria-label="Rudy profile"
        >
          RP
        </button>
      </div>
    </aside>
  );
}

interface AssistantLandingProps {
  composerValue: string;
  onComposerChange: (value: string) => void;
  onWorkspaceOpen: () => void;
  onChatOpen: () => void;
}

function AssistantLanding({
  composerValue,
  onComposerChange,
  onWorkspaceOpen,
  onChatOpen,
}: AssistantLandingProps) {
  return (
    <div className="mx-auto flex min-h-screen w-full max-w-[1040px] flex-col px-6 pb-16 pt-[clamp(4.5rem,13vh,7.75rem)] sm:px-10 lg:px-16">
      <div className="mx-auto flex w-full max-w-[960px] flex-1 flex-col">
        <section className="mx-auto w-full max-w-[820px] text-center">
          <h1 className="text-[clamp(2.85rem,5vw,4.25rem)] font-semibold tracking-[-0.06em] text-[var(--shell-heading)]">
            Hello,{' '}
            <span className="bg-gradient-to-r from-[var(--shell-accent)] via-[var(--shell-accent-mid)] to-[var(--shell-accent-strong)] bg-clip-text text-transparent">
              Rudy
            </span>
          </h1>
          <p className="mt-2 text-[clamp(2.2rem,4vw,3.5rem)] font-medium tracking-[-0.055em] text-[var(--shell-heading)]">
            How can I assist you?
          </p>
          <p className="mx-auto mt-6 max-w-[660px] text-[1.12rem] leading-8 text-[var(--shell-muted)]">
            I can help you with documentation guidance with internal processes, Support
            Inquiries, and more.
          </p>
        </section>

        <section className="mx-auto mt-12 w-full max-w-[960px]">
          <div className="relative overflow-hidden rounded-[var(--shell-composer-radius)] border border-[var(--shell-composer-border)] bg-white shadow-[var(--shell-composer-shadow)] transition-all focus-within:-translate-y-0.5 focus-within:border-[var(--shell-accent)] focus-within:shadow-[var(--shell-composer-shadow-focus)] hover:border-[var(--shell-composer-border-strong)]">
            <textarea
              value={composerValue}
              onChange={(event) => onComposerChange(event.target.value)}
              placeholder="Ask Strider, use @ to select an agent"
              rows={3}
              className="min-h-[92px] w-full resize-none border-0 bg-transparent px-5 pb-14 pt-4 text-[1.06rem] text-[var(--shell-heading)] placeholder:text-[var(--shell-placeholder)] focus:outline-none"
            />

            <button
              type="button"
              onClick={onWorkspaceOpen}
              className="absolute bottom-3 left-3 flex h-9 w-9 items-center justify-center rounded-full text-[var(--shell-placeholder)] transition-colors hover:bg-[var(--shell-surface-subtle)] hover:text-[var(--shell-accent-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
              aria-label="Attach files"
            >
              <Paperclip size={19} />
            </button>

            <button
              type="button"
              onClick={onChatOpen}
              className="absolute bottom-3 right-3 flex h-9 w-9 items-center justify-center rounded-full text-[var(--shell-placeholder)] transition-colors hover:bg-[var(--shell-surface-subtle)] hover:text-[var(--shell-accent-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
              aria-label="Open microphone assistant"
            >
              <Mic size={18} />
            </button>
          </div>

          <p className="px-4 pt-4 text-[0.95rem] text-[var(--shell-muted)]">
            AI can make mistakes. Please verify critical information.{' '}
            <button
              type="button"
              onClick={onChatOpen}
              className="font-semibold text-[var(--shell-heading)] transition-colors hover:text-[var(--shell-accent-strong)]"
            >
              Auto Agent
            </button>
          </p>
        </section>

        <section className="mx-auto mt-7 w-full max-w-[960px]">
          <h2 className="text-[2rem] font-medium tracking-[-0.045em] text-[var(--shell-heading)]">
            Recommended Actions for you
          </h2>
          <div className="mt-4 max-w-[370px] rounded-[30px] border border-transparent px-1 py-1">
            <p className="text-[1rem] leading-8 text-[var(--shell-heading)]">
              No recommended actions available at this time.
            </p>
            <button
              type="button"
              onClick={onWorkspaceOpen}
              className="mt-2 inline-flex items-center gap-2 rounded-full px-3 py-2 text-sm font-medium text-[var(--shell-accent-strong)] transition-colors hover:bg-[var(--shell-surface-subtle)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
            >
              <FolderOpen size={16} />
              Open document workspace
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}

interface FloatingLaunchersProps {
  onWorkspaceOpen: () => void;
  onChatOpen: () => void;
}

function FloatingLaunchers({ onWorkspaceOpen, onChatOpen }: FloatingLaunchersProps) {
  return (
    <div className="fixed bottom-5 right-5 z-40 flex flex-col gap-3">
      <button
        type="button"
        onClick={onChatOpen}
        className="flex h-12 w-12 items-center justify-center rounded-full border border-[var(--shell-float-border)] bg-[radial-gradient(circle_at_30%_30%,#ffffff_0%,#dff8f6_18%,#7bd4c9_65%,#49a9ba_100%)] text-white shadow-[var(--shell-float-shadow)] transition-transform hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
        aria-label="Open assistant chat"
      >
        <MessageCircle size={18} />
      </button>
      <button
        type="button"
        onClick={onWorkspaceOpen}
        className="flex h-12 w-12 items-center justify-center rounded-full border border-[var(--shell-float-border)] bg-white text-[1.4rem] font-semibold tracking-[-0.08em] text-transparent shadow-[var(--shell-float-shadow)] transition-transform hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
        aria-label="Open workspace launcher"
      >
        <span className="bg-gradient-to-br from-emerald-500 via-green-600 to-cyan-600 bg-clip-text">S</span>
      </button>
    </div>
  );
}

interface DocumentWorkspaceProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  onChatOpen: () => void;
  selectedFiles: Array<{ id: string; name: string; path: string }>;
  onSelectionChange: (selectedFiles: Array<{ id: string; name: string; path: string }>) => void;
  onFileIndexChange: (index: number) => void;
}

function DocumentWorkspace({
  isOpen,
  onOpenChange,
  onChatOpen,
  selectedFiles,
  onSelectionChange,
  onFileIndexChange,
}: DocumentWorkspaceProps) {
  const [isTreeCollapsed, setIsTreeCollapsed] = useState(false);

  return (
    <Sheet open={isOpen} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full gap-0 border-l border-[var(--shell-rail-border)] bg-[var(--shell-surface)] p-0 sm:max-w-none lg:w-[min(92vw,1420px)]"
      >
        <SheetHeader className="border-b border-[var(--shell-rail-border)] px-6 py-5 pr-14">
          <div className="flex items-start justify-between gap-4">
            <div>
              <SheetTitle className="text-[1.55rem] tracking-[-0.04em] text-[var(--shell-heading)]">
                Document workspace
              </SheetTitle>
              <SheetDescription className="mt-1 max-w-[680px] text-[0.98rem] leading-7 text-[var(--shell-muted)]">
                Browse files, review previews, and keep the chat available as a secondary tool.
              </SheetDescription>
            </div>
            <button
              type="button"
              onClick={onChatOpen}
              className="inline-flex items-center gap-2 rounded-full border border-[var(--shell-rail-border)] bg-white px-3 py-2 text-sm font-medium text-[var(--shell-heading)] shadow-sm transition-colors hover:border-[var(--shell-accent)] hover:text-[var(--shell-accent-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
            >
              <Sparkles size={16} />
              Open chat
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-3 pt-4 text-sm text-[var(--shell-muted)]">
            <span className="rounded-full bg-[var(--shell-surface-subtle)] px-3 py-1.5 text-[var(--shell-heading)]">
              {selectedFiles.length} file{selectedFiles.length === 1 ? '' : 's'} selected
            </span>
            <span>Use the left tree to choose files and compare outputs in the viewer.</span>
          </div>
        </SheetHeader>

        <div className="flex min-h-0 flex-1 px-5 py-5">
          <PanelGroup direction="horizontal">
            {!isTreeCollapsed ? (
              <>
                <Panel defaultSize={34} minSize={22} maxSize={48}>
                  <FileTree
                    data={sampleFileStructure}
                    onSelectionChange={onSelectionChange}
                    onCollapse={() => setIsTreeCollapsed(true)}
                  />
                </Panel>
                <PanelResizeHandle className="mx-3 w-1 rounded-full bg-[var(--shell-handle)] transition-colors hover:bg-[var(--shell-accent)]/35" />
              </>
            ) : (
              <div className="mr-3 flex items-start">
                <button
                  type="button"
                  onClick={() => setIsTreeCollapsed(false)}
                  className="rounded-2xl border border-[var(--shell-rail-border)] bg-white p-3 text-[var(--shell-icon)] shadow-sm transition-colors hover:border-[var(--shell-accent)] hover:text-[var(--shell-accent-strong)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
                  aria-label="Show file tree"
                >
                  <PanelLeftOpen size={20} />
                </button>
              </div>
            )}
            <Panel minSize={42}>
              <FileViewer
                selectedFiles={selectedFiles}
                onFileIndexChange={onFileIndexChange}
              />
            </Panel>
          </PanelGroup>
        </div>
      </SheetContent>
    </Sheet>
  );
}

export default function App() {
  const [selectedFiles, setSelectedFiles] = useState<Array<{ id: string; name: string; path: string }>>([]);
  const [currentFileIndex, setCurrentFileIndex] = useState(0);
  const [isWorkspaceOpen, setIsWorkspaceOpen] = useState(false);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [isChatMinimized, setIsChatMinimized] = useState(false);
  const [composerValue, setComposerValue] = useState('');

  const openChat = () => {
    setIsChatOpen(true);
    setIsChatMinimized(false);
  };

  return (
    <div className="min-h-screen bg-[var(--shell-background)] text-[var(--shell-foreground)]">
      <div className="grid min-h-screen grid-cols-[72px_minmax(0,1fr)] bg-[radial-gradient(circle_at_top,#ffffff_0%,#fbfcff_38%,#f7f9fc_100%)] sm:grid-cols-[84px_minmax(0,1fr)]">
        <HostSidebar onWorkspaceOpen={() => setIsWorkspaceOpen(true)} />

        <main className="relative overflow-hidden">
          <AssistantLanding
            composerValue={composerValue}
            onComposerChange={setComposerValue}
            onWorkspaceOpen={() => setIsWorkspaceOpen(true)}
            onChatOpen={openChat}
          />
        </main>
      </div>

      <FloatingLaunchers
        onWorkspaceOpen={() => setIsWorkspaceOpen(true)}
        onChatOpen={openChat}
      />

      <DocumentWorkspace
        isOpen={isWorkspaceOpen}
        onOpenChange={setIsWorkspaceOpen}
        onChatOpen={openChat}
        selectedFiles={selectedFiles}
        onSelectionChange={setSelectedFiles}
        onFileIndexChange={setCurrentFileIndex}
      />

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
