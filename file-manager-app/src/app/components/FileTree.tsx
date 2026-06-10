import { useState } from 'react';
import { ChevronRight, ChevronDown, Folder, File, PanelLeftClose } from 'lucide-react';

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

function TreeItem({ node, level, selectedIds, onToggle }: TreeItemProps) {
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

  const handleCheckboxChange = () => {
    onToggle(node.id, isFolder, node.children);
  };

  const handleExpandToggle = () => {
    if (isFolder) {
      setIsExpanded(!isExpanded);
    }
  };

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
            {isExpanded ? (
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

        <span className="text-sm select-none">
          {node.name}
        </span>
      </div>

      {isFolder && isExpanded && node.children && (
        <div>
          {node.children.map((child) => (
            <TreeItem
              key={child.id}
              node={child}
              level={level + 1}
              selectedIds={selectedIds}
              onToggle={onToggle}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function FileTree({ data, onSelectionChange, onCollapse }: FileTreeProps) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const getAllChildIds = (node: TreeNode): string[] => {
    const ids = [node.id];
    if (node.children) {
      node.children.forEach((child) => {
        ids.push(...getAllChildIds(child));
      });
    }
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

  return (
    <div className="flex h-full min-h-0 flex-col rounded-lg border border-gray-200 bg-white p-4">
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

      <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
        {data.map((node) => (
          <TreeItem
            key={node.id}
            node={node}
            level={0}
            selectedIds={selectedIds}
            onToggle={handleToggle}
          />
        ))}
      </div>
    </div>
  );
}
