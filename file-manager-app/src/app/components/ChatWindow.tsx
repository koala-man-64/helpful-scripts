import { useState, useRef, useEffect } from 'react';
import { X, Send, MessageCircle, Minimize2 } from 'lucide-react';

interface Message {
  id: string;
  text: string;
  sender: 'user' | 'assistant';
  timestamp: Date;
}

interface ChatWindowProps {
  isOpen: boolean;
  onClose: () => void;
  isMinimized: boolean;
  onMinimize: () => void;
  selectedFiles?: Array<{ id: string; name: string; path: string }>;
  currentFileIndex?: number;
}

interface PresetPrompt {
  id: string;
  label: string;
  prompt: string;
}

const presetPrompts: PresetPrompt[] = [
  {
    id: 'help',
    label: 'Help',
    prompt: 'How can I use this application?',
  },
  {
    id: 'analyze',
    label: 'Analyze',
    prompt: 'Can you analyze the selected documents and provide insights?',
  },
  {
    id: 'compare',
    label: 'Compare',
    prompt: 'Compare the selected documents and highlight key differences.',
  },
  {
    id: 'summarize',
    label: 'Summarize',
    prompt: 'Provide a comprehensive summary of the selected documents.',
  },
  {
    id: 'export',
    label: 'Export',
    prompt: 'How can I export the selected files?',
  },
];

export default function ChatWindow({ isOpen, onClose, isMinimized, onMinimize, selectedFiles = [], currentFileIndex = 0 }: ChatWindowProps) {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: '1',
      text: 'Hello! How can I help you today?',
      sender: 'assistant',
      timestamp: new Date(),
    },
  ]);
  const [inputValue, setInputValue] = useState('');
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null);
  const [contextMode, setContextMode] = useState<'current' | 'all'>('current');
  const [chatSize, setChatSize] = useState({ width: 384, height: 500 });
  const [isResizing, setIsResizing] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const chatWindowRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    if (!isResizing) return;

    const handleMouseMove = (e: MouseEvent) => {
      if (!chatWindowRef.current) return;

      const rect = chatWindowRef.current.getBoundingClientRect();
      const newWidth = rect.right - e.clientX;
      const newHeight = rect.bottom - e.clientY;

      setChatSize({
        width: Math.max(300, Math.min(800, newWidth)),
        height: Math.max(400, Math.min(800, newHeight)),
      });
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizing]);

  const handleSend = (messageText?: string) => {
    const textToSend = messageText || inputValue;
    if (textToSend.trim() === '') return;

    const newMessage: Message = {
      id: Date.now().toString(),
      text: textToSend,
      sender: 'user',
      timestamp: new Date(),
    };

    setMessages([...messages, newMessage]);
    setInputValue('');

    // Simulate assistant response
    setTimeout(() => {
      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        text: "I'm a demo assistant. I've received your message!",
        sender: 'assistant',
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, assistantMessage]);
    }, 1000);
  };

  const handlePresetClick = (preset: PresetPrompt) => {
    setSelectedPreset(preset.id);
    setInputValue(preset.prompt);
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (!isOpen) return null;

  if (isMinimized) {
    return (
      <div className="fixed bottom-24 right-6 bg-white border border-gray-300 rounded-lg shadow-lg p-3 w-64">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <MessageCircle size={18} className="text-blue-600" />
            <span className="font-semibold text-sm">Chat</span>
          </div>
          <div className="flex gap-1">
            <button
              onClick={onMinimize}
              className="p-1 hover:bg-gray-100 rounded"
              aria-label="Restore chat"
            >
              <MessageCircle size={16} />
            </button>
            <button
              onClick={onClose}
              className="p-1 hover:bg-gray-100 rounded"
              aria-label="Close chat"
            >
              <X size={16} />
            </button>
          </div>
        </div>
      </div>
    );
  }

  const currentFile = selectedFiles[currentFileIndex];
  const hasSelectedFiles = selectedFiles.length > 0;
  const contextFiles = contextMode === 'current' && currentFile ? [currentFile] : selectedFiles;

  return (
    <div
      ref={chatWindowRef}
      className="fixed bottom-24 right-6 bg-white border border-gray-300 rounded-lg shadow-2xl flex flex-col"
      style={{ width: `${chatSize.width}px`, height: `${chatSize.height}px` }}
    >
      {/* Resize Handle - Top Left Corner */}
      <div
        className="absolute top-0 left-0 w-4 h-4 cursor-nwse-resize hover:bg-blue-500 hover:bg-opacity-30 rounded-tl-lg"
        onMouseDown={(e) => {
          e.preventDefault();
          setIsResizing(true);
        }}
      />
      {/* Resize Handle - Top Edge */}
      <div
        className="absolute top-0 left-4 right-4 h-1 cursor-ns-resize hover:bg-blue-500 hover:bg-opacity-30"
        onMouseDown={(e) => {
          e.preventDefault();
          setIsResizing(true);
        }}
      />
      {/* Resize Handle - Left Edge */}
      <div
        className="absolute left-0 top-4 bottom-4 w-1 cursor-ew-resize hover:bg-blue-500 hover:bg-opacity-30"
        onMouseDown={(e) => {
          e.preventDefault();
          setIsResizing(true);
        }}
      />
      {/* Header */}
      <div className="bg-blue-600 text-white p-4 rounded-t-lg flex items-center justify-between">
        <div className="flex items-center gap-2">
          <MessageCircle size={20} />
          <h3 className="font-semibold">Chat Support</h3>
        </div>
        <div className="flex gap-2">
          <button
            onClick={onMinimize}
            className="p-1 hover:bg-blue-700 rounded"
            aria-label="Minimize chat"
          >
            <Minimize2 size={18} />
          </button>
          <button
            onClick={onClose}
            className="p-1 hover:bg-blue-700 rounded"
            aria-label="Close chat"
          >
            <X size={18} />
          </button>
        </div>
      </div>

      {/* Context Selector */}
      {hasSelectedFiles && (
        <div className="p-3 border-b border-gray-200 bg-gray-50">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-semibold text-gray-700">Document Context</span>
            <div className="flex gap-1 bg-white rounded-lg p-0.5 border border-gray-300">
              <button
                onClick={() => setContextMode('current')}
                className={`px-2 py-1 text-xs rounded transition-colors ${
                  contextMode === 'current'
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-600 hover:bg-gray-100'
                }`}
              >
                Current
              </button>
              <button
                onClick={() => setContextMode('all')}
                className={`px-2 py-1 text-xs rounded transition-colors ${
                  contextMode === 'all'
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-600 hover:bg-gray-100'
                }`}
              >
                All ({selectedFiles.length})
              </button>
            </div>
          </div>
          <div className="text-xs text-gray-600">
            {contextMode === 'current' && currentFile ? (
              <span className="truncate block">📄 {currentFile.name}</span>
            ) : (
              <span>{selectedFiles.length} document{selectedFiles.length !== 1 ? 's' : ''} selected</span>
            )}
          </div>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex ${message.sender === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[75%] rounded-lg p-3 ${
                message.sender === 'user'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-900'
              }`}
            >
              <p className="text-sm">{message.text}</p>
              <p
                className={`text-xs mt-1 ${
                  message.sender === 'user' ? 'text-blue-100' : 'text-gray-500'
                }`}
              >
                {message.timestamp.toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </p>
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t border-gray-200">
        {/* Preset Prompts */}
        <div className="p-3 border-b border-gray-200 bg-gray-50">
          <div className="flex gap-2 flex-wrap">
            {presetPrompts.map((preset) => (
              <button
                key={preset.id}
                onClick={() => handlePresetClick(preset)}
                className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${
                  selectedPreset === preset.id
                    ? 'bg-blue-600 text-white'
                    : 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-100'
                }`}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>

        {/* Input Field */}
        <div className="p-4">
          <div className="flex gap-2">
            <input
              type="text"
              value={inputValue}
              onChange={(e) => {
                setInputValue(e.target.value);
                setSelectedPreset(null);
              }}
              onKeyPress={handleKeyPress}
              placeholder="Type your message..."
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              onClick={() => handleSend()}
              className="p-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              disabled={inputValue.trim() === ''}
              aria-label="Send message"
            >
              <Send size={18} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
