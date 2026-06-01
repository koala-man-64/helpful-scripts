import { useState } from 'react';
import { ChevronRight, ChevronDown, Folder, File, Search, PanelLeftClose } from 'lucide-react';

interface TreeNode {
  id: string;
  name: string;
  type: 'folder' | 'file';
  children?: TreeNode[];
}

interface FileTreeProps {
  data: TreeNode[];
  onSelectionChange?: (selectedFiles: Array<{ id: string; name: string; path: string }>) => void;
  onCollapse?: () => void;
}

interface TreeItemProps {
  node: TreeNode;
  level: number;
  selectedIds: Set<string>;
  onToggle: (id: string, isFolder: boolean, children?: TreeNode[]) => void;
  searchTerm: string;
  expandedBySearch: boolean;
}

const ALLOWED_EXTENSIONS = ['.md', '.txt', '.docx', '.pdf'];

const isAllowedFile = (fileName: string): boolean => {
  return ALLOWED_EXTENSIONS.some(ext => fileName.toLowerCase().endsWith(ext));
};

const hasAllowedFiles = (node: TreeNode): boolean => {
  if (node.type === 'file') {
    return isAllowedFile(node.name);
  }
  if (node.children) {
    return node.children.some(hasAllowedFiles);
  }
  return false;
};

function TreeItem({ node, level, selectedIds, onToggle, searchTerm, expandedBySearch }: TreeItemProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const isFolder = node.type === 'folder';
  const isSelected = selectedIds.has(node.id);

  // Filter: Only show allowed file types
  if (node.type === 'file' && !isAllowedFile(node.name)) {
    return null;
  }

  // Filter: Only show folders that contain allowed files
  if (isFolder && !hasAllowedFiles(node)) {
    return null;
  }

  const matchesSearch = searchTerm === '' || node.name.toLowerCase().includes(searchTerm.toLowerCase());

  const hasMatchingChild = (n: TreeNode): boolean => {
    if (n.name.toLowerCase().includes(searchTerm.toLowerCase())) {
      return true;
    }
    if (n.children) {
      return n.children.some(hasMatchingChild);
    }
    return false;
  };

  const shouldShow = searchTerm === '' || matchesSearch || (isFolder && node.children && node.children.some(hasMatchingChild));
  const shouldExpand = expandedBySearch || isExpanded;

  const handleCheckboxChange = () => {
    onToggle(node.id, isFolder, node.children);
  };

  const handleExpandToggle = () => {
    if (isFolder) {
      setIsExpanded(!isExpanded);
    }
  };

  if (!shouldShow) {
    return null;
  }

  return (
    <div>
      <div
        className="flex items-center gap-2 py-1 px-2 hover:bg-gray-100 rounded"
        style={{ paddingLeft: `${level * 20 + 8}px` }}
      >
        {isFolder && (
          <button
            onClick={handleExpandToggle}
            className="p-0.5 hover:bg-gray-200 rounded"
          >
            {shouldExpand ? (
              <ChevronDown size={16} className="text-gray-600" />
            ) : (
              <ChevronRight size={16} className="text-gray-600" />
            )}
          </button>
        )}
        {!isFolder && <div className="w-5" />}

        <input
          type="checkbox"
          checked={isSelected}
          onChange={handleCheckboxChange}
          className="cursor-pointer"
        />

        {isFolder ? (
          <Folder size={16} className="text-blue-500" />
        ) : (
          <File size={16} className="text-gray-500" />
        )}

        <span className={`text-sm select-none ${matchesSearch && searchTerm !== '' ? 'bg-yellow-200' : ''}`}>
          {node.name}
        </span>
      </div>

      {isFolder && shouldExpand && node.children && (
        <div>
          {node.children.map((child) => (
            <TreeItem
              key={child.id}
              node={child}
              level={level + 1}
              selectedIds={selectedIds}
              onToggle={onToggle}
              searchTerm={searchTerm}
              expandedBySearch={expandedBySearch}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function FileTree({ data, onSelectionChange, onCollapse }: FileTreeProps) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [searchTerm, setSearchTerm] = useState('');

  const getAllChildIds = (node: TreeNode): string[] => {
    const ids = [node.id];
    if (node.children) {
      node.children.forEach((child) => {
        ids.push(...getAllChildIds(child));
      });
    }
    return ids;
  };

  const getAllIds = (nodes: TreeNode[]): string[] => {
    const ids: string[] = [];
    nodes.forEach((node) => {
      ids.push(...getAllChildIds(node));
    });
    return ids;
  };

  const getSelectedFiles = (nodes: TreeNode[], selectedIds: Set<string>, parentPath = ''): Array<{ id: string; name: string; path: string }> => {
    const files: Array<{ id: string; name: string; path: string }> = [];

    nodes.forEach((node) => {
      const currentPath = parentPath ? `${parentPath}/${node.name}` : node.name;

      if (selectedIds.has(node.id) && node.type === 'file') {
        files.push({
          id: node.id,
          name: node.name,
          path: currentPath,
        });
      }

      if (node.children) {
        files.push(...getSelectedFiles(node.children, selectedIds, currentPath));
      }
    });

    return files;
  };

  const updateSelection = (newSelected: Set<string>) => {
    setSelectedIds(newSelected);
    if (onSelectionChange) {
      const selectedFiles = getSelectedFiles(data, newSelected);
      onSelectionChange(selectedFiles);
    }
  };

  const handleToggle = (id: string, isFolder: boolean, children?: TreeNode[]) => {
    const newSelected = new Set(selectedIds);

    if (newSelected.has(id)) {
      // Unselect this item and all its children if it's a folder
      newSelected.delete(id);
      if (isFolder && children) {
        children.forEach((child) => {
          getAllChildIds(child).forEach((childId) => newSelected.delete(childId));
        });
      }
    } else {
      // Select this item and all its children if it's a folder
      newSelected.add(id);
      if (isFolder && children) {
        children.forEach((child) => {
          getAllChildIds(child).forEach((childId) => newSelected.add(childId));
        });
      }
    }

    updateSelection(newSelected);
  };

  const handleSelectAll = () => {
    const allIds = getAllIds(data);
    updateSelection(new Set(allIds));
  };

  const handleSelectNone = () => {
    updateSelection(new Set());
  };

  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="font-semibold">Select Files</h3>
        <div className="flex items-center gap-2">
          <span className="text-sm text-gray-500">
            {selectedIds.size} selected
          </span>
          {onCollapse && (
            <button
              onClick={onCollapse}
              className="p-1.5 hover:bg-gray-100 rounded"
              aria-label="Collapse panel"
            >
              <PanelLeftClose size={18} className="text-gray-600" />
            </button>
          )}
        </div>
      </div>

      <div className="mb-4 flex gap-2">
        <div className="flex-1 relative">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            placeholder="Search files and folders..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <button
          onClick={handleSelectAll}
          className="px-3 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50"
        >
          Select All
        </button>
        <button
          onClick={handleSelectNone}
          className="px-3 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50"
        >
          Select None
        </button>
      </div>

      <div className="space-y-0.5 max-h-96 overflow-y-auto">
        {data.map((node) => (
          <TreeItem
            key={node.id}
            node={node}
            level={0}
            selectedIds={selectedIds}
            onToggle={handleToggle}
            searchTerm={searchTerm}
            expandedBySearch={searchTerm !== ''}
          />
        ))}
      </div>
    </div>
  );
}
