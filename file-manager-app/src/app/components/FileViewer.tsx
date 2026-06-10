import { ChevronLeft, ChevronRight, FileText, FileCode, ListChecks, Smile, Tag } from 'lucide-react';
import { useState, useEffect, useRef } from 'react';

interface FileViewerProps {
  selectedFiles: Array<{ id: string; name: string; path: string }>;
  onFileIndexChange?: (index: number) => void;
}

type AnalysisType = 'original' | 'summary' | 'keyPoints' | 'sentiment' | 'entities';

const mockFileContent: Record<string, Record<AnalysisType, string>> = {
  'report.pdf': {
    original: `Annual Report 2025

Executive Summary
This report provides a comprehensive overview of our company's performance throughout the fiscal year 2025.

Key Highlights:
- Revenue increased by 23% year-over-year
- Expanded operations to 5 new markets
- Customer satisfaction rating improved to 4.8/5.0
- Successfully launched 3 new product lines

Financial Performance
Total revenue reached $45.2M, representing strong growth across all business segments...`,
    summary: `Annual Report 2025 Summary:
The company demonstrated strong performance in FY2025 with 23% revenue growth, reaching $45.2M total revenue. The organization successfully expanded into 5 new markets and launched 3 new product lines. Customer satisfaction improved to 4.8/5.0, indicating positive market reception.`,
    keyPoints: `• Revenue grew 23% YoY to $45.2M
• Expanded to 5 new markets
• Customer satisfaction: 4.8/5.0
• Launched 3 new product lines
• Strong growth across all business segments`,
    sentiment: `Overall Sentiment: POSITIVE (Score: 0.85/1.0)

The document expresses highly positive sentiment throughout, highlighting achievements and growth. Key positive indicators include revenue growth, market expansion, and improved customer satisfaction.

Confidence: High`,
    entities: `Organizations: [Company Name]
Time Periods: FY 2025
Metrics:
  - Revenue: $45.2M
  - Growth Rate: 23% YoY
  - Customer Satisfaction: 4.8/5.0
  - New Markets: 5
  - New Products: 3`
  },
  'presentation.pptx': {
    original: `Quarterly Business Review - Q2 2025

Slide 1: Agenda
- Market Overview
- Q2 Performance
- Strategic Initiatives
- Next Quarter Goals

Slide 2: Market Overview
The market has shown strong recovery with 15% growth in our sector...

Slide 3: Q2 Performance
- Sales: $12.3M (Target: $11.5M)
- New Customers: 847
- Retention Rate: 94%`,
    summary: `Q2 2025 Business Review:
The presentation covers Q2 performance which exceeded targets. Sales reached $12.3M against a target of $11.5M. The company acquired 847 new customers and maintained a 94% retention rate. Market conditions showed positive recovery with 15% sector growth.`,
    keyPoints: `• Q2 Sales: $12.3M (exceeded target of $11.5M)
• New customers acquired: 847
• Retention rate: 94%
• Market growth: 15% in sector
• Presentation covers: Market overview, performance, strategy, and goals`,
    sentiment: `Overall Sentiment: POSITIVE (Score: 0.78/1.0)

The presentation conveys optimism about Q2 results, with sales exceeding targets and strong customer metrics. Market recovery narrative is encouraging.

Confidence: High`,
    entities: `Time Period: Q2 2025
Financial Metrics:
  - Sales: $12.3M
  - Target: $11.5M
  - New Customers: 847
  - Retention: 94%
  - Market Growth: 15%`
  },
  'notes.txt': {
    original: `Meeting Notes - June 1, 2026

Attendees: Sarah, Mike, Jennifer, Tom

Discussion Topics:
1. New feature roadmap for Q3
   - Priority: Mobile app refresh
   - Timeline: 8-10 weeks
   - Resources needed: 2 additional frontend developers

2. Infrastructure updates
   - Migration to Kubernetes scheduled for July
   - Database optimization project starting next week

Action Items:
- Sarah: Draft technical specification by June 8
- Mike: Review vendor proposals by June 5
- Jennifer: Schedule follow-up meeting for June 15`,
    summary: `Meeting focused on Q3 planning and infrastructure improvements. Key decision: prioritize mobile app refresh (8-10 week timeline, requires 2 frontend developers). Infrastructure work includes July Kubernetes migration and immediate database optimization. Three action items assigned with specific deadlines.`,
    keyPoints: `• Q3 Priority: Mobile app refresh (8-10 weeks)
• Need 2 additional frontend developers
• Kubernetes migration: July
• Database optimization: starting next week
• Action items assigned to Sarah, Mike, and Jennifer with deadlines`,
    sentiment: `Overall Sentiment: NEUTRAL-POSITIVE (Score: 0.55/1.0)

The meeting notes are primarily informational with a slight positive lean toward planned improvements. No negative indicators present. Tone is professional and action-oriented.

Confidence: Medium`,
    entities: `People: Sarah, Mike, Jennifer, Tom
Date: June 1, 2026
Deadlines:
  - June 5: Vendor proposals (Mike)
  - June 8: Technical spec (Sarah)
  - June 15: Follow-up meeting (Jennifer)
Technologies: Kubernetes, Mobile App
Timeline: Q3, 8-10 weeks, July`
  },
  'budget.xlsx': {
    original: `FY 2025 Budget Breakdown

Department | Q1 | Q2 | Q3 | Q4 | Total
Marketing | $125,000 | $135,000 | $140,000 | $150,000 | $550,000
Engineering | $450,000 | $460,000 | $475,000 | $480,000 | $1,865,000
Sales | $280,000 | $290,000 | $295,000 | $310,000 | $1,175,000
Operations | $175,000 | $180,000 | $185,000 | $190,000 | $730,000

Total Budget: $4,320,000`,
    summary: `FY 2025 Budget totals $4.32M across four departments. Engineering receives the largest allocation at $1.865M (43%), followed by Sales at $1.175M (27%), Marketing at $550K (13%), and Operations at $730K (17%). Budgets show gradual increases quarter-over-quarter.`,
    keyPoints: `• Total Budget: $4,320,000
• Engineering: $1,865,000 (43% of total)
• Sales: $1,175,000 (27% of total)
• Operations: $730,000 (17% of total)
• Marketing: $550,000 (13% of total)
• All departments show Q-over-Q growth`,
    sentiment: `Overall Sentiment: NEUTRAL (Score: 0.50/1.0)

Budget data is presented factually without sentiment indicators. The gradual increases suggest planned growth but no explicit positive or negative framing.

Confidence: Low (purely numerical data)`,
    entities: `Time Period: FY 2025
Departments: Marketing, Engineering, Sales, Operations
Total Budget: $4,320,000
Quarterly Distribution: Q1-Q4
Largest Department: Engineering ($1,865,000)
Smallest Department: Marketing ($550,000)`
  },
  'quarterly-review.docx': {
    original: `Quarterly Review - Q2 2026

Executive Summary
This quarter has shown remarkable progress across all key performance indicators.

Achievements:
- Revenue targets exceeded by 15%
- Customer retention improved to 96%
- Three major product releases completed on schedule
- Team expanded by 12 new hires

Next Quarter Focus:
- Launch new enterprise tier
- Expand into APAC markets
- Implement customer feedback system`,
    summary: `Q2 2026 showed strong performance with revenue exceeding targets by 15% and customer retention at 96%. Three major products launched successfully, and team grew by 12 people. Focus for next quarter includes enterprise tier launch and APAC expansion.`,
    keyPoints: `• Revenue exceeded targets by 15%
• Customer retention: 96%
• 3 major product releases completed
• 12 new team members hired
• Next: Enterprise tier launch
• Next: APAC market expansion
• Next: Customer feedback system`,
    sentiment: `Overall Sentiment: HIGHLY POSITIVE (Score: 0.88/1.0)

Exceptionally positive tone throughout, emphasizing achievements and growth. Forward-looking statements express confidence and ambition.

Confidence: High`,
    entities: `Time Period: Q2 2026
Metrics:
  - Revenue growth: +15%
  - Customer retention: 96%
  - Product releases: 3
  - New hires: 12
Initiatives: Enterprise tier, APAC expansion, Customer feedback system`
  },

  'resume.pdf': {
    original: `John Doe
Senior Software Engineer

Contact: john.doe@email.com | (555) 123-4567

Professional Summary
Results-driven software engineer with 8+ years of experience in full-stack development,
specializing in React, Node.js, and cloud architecture.

Experience

Senior Software Engineer | Tech Corp | 2020 - Present
- Led development of microservices architecture serving 2M+ users
- Reduced application load time by 40% through optimization
- Mentored team of 5 junior developers

Software Engineer | StartUp Inc | 2017 - 2020
- Built responsive web applications using React and TypeScript
- Implemented CI/CD pipelines reducing deployment time by 60%`,
    summary: `John Doe is a Senior Software Engineer with 8+ years of full-stack development experience. Currently at Tech Corp (2020-present), he leads microservices development for 2M+ users. Previously at StartUp Inc (2017-2020), he built React/TypeScript applications and CI/CD infrastructure. Specializes in React, Node.js, and cloud architecture.`,
    keyPoints: `• 8+ years full-stack development experience
• Specialization: React, Node.js, cloud architecture
• Current: Senior Software Engineer at Tech Corp (2020-present)
• Led microservices serving 2M+ users
• 40% application performance improvement
• Mentored 5 junior developers
• Previous: StartUp Inc (2017-2020)`,
    sentiment: `Overall Sentiment: POSITIVE (Score: 0.72/1.0)

Resume demonstrates strong career progression and achievements. Language emphasizes results and leadership ("led", "reduced", "mentored"). Quantifiable achievements create positive impression.

Confidence: High`,
    entities: `Person: John Doe
Role: Senior Software Engineer
Companies: Tech Corp (2020-present), StartUp Inc (2017-2020)
Skills: React, Node.js, TypeScript, Cloud Architecture, Microservices, CI/CD
Experience: 8+ years
Contact: john.doe@email.com, (555) 123-4567
Achievements:
  - 2M+ users served
  - 40% performance improvement
  - 60% deployment time reduction
  - 5 developers mentored`
  },
  'README.md': {
    original: `# File Manager Application

A modern web-based file management system with advanced document analysis capabilities.

## Features

- Hierarchical file tree navigation
- Document viewer with multiple analysis types
- Integrated chat support with AI assistance
- Resizable and collapsible panels
- Search and filter functionality

## Technology Stack

- React 18.3
- TypeScript
- Tailwind CSS
- Vite

## Getting Started

\`\`\`bash
pnpm install
pnpm run dev
\`\`\`

## Usage

Select files from the tree view to preview and analyze documents.`,
    summary: `Documentation for a modern web-based file management system featuring hierarchical navigation, document analysis, and AI chat support. Built with React 18.3, TypeScript, Tailwind CSS, and Vite.`,
    keyPoints: `• Hierarchical file tree navigation
• Document viewer with analysis types
• Integrated AI chat support
• Resizable/collapsible panels
• Search and filter functionality
• Tech: React 18.3, TypeScript, Tailwind, Vite`,
    sentiment: `Overall Sentiment: NEUTRAL-POSITIVE (Score: 0.60/1.0)

Technical documentation with factual tone. Presents features and setup instructions objectively.

Confidence: Medium`,
    entities: `Project: File Manager Application
Technologies: React 18.3, TypeScript, Tailwind CSS, Vite
Features: File tree, Document viewer, Chat support, Analysis modes
File types: .md, .txt, .pdf, .docx
Package Manager: pnpm`
  }
};

// Simplified content for files without full analysis
const simpleContent: Record<string, string> = {
  'quarterly-review.docx': `Quarterly Review - Q2 2026

Executive Summary
This quarter has shown remarkable progress across all key performance indicators.

Achievements:
- Revenue targets exceeded by 15%
- Customer retention improved to 96%
- Three major product releases completed on schedule
- Team expanded by 12 new hires

Next Quarter Focus:
- Launch new enterprise tier
- Expand into APAC markets
- Implement customer feedback system`,

  'meeting-notes.txt': `Team Standup - June 1, 2026

Attendees: Alex, Jordan, Sam, Taylor

Updates:
- Alex: Completed API integration, starting frontend work
- Jordan: Code review in progress, addressing feedback
- Sam: Working on database optimization
- Taylor: User testing scheduled for next week

Blockers: None

Next Meeting: June 8, 2026`,

  'journal.docx': `Personal Journal - May 2026

Reflections on the past month:

The project launch went better than expected. The team worked cohesively and delivered ahead of schedule. Key learnings included better time management and more effective communication patterns.

Goals for next month:
- Complete certification program
- Mentor two junior team members
- Improve work-life balance`,

  'findings.md': `# Research Findings

## Overview
This document summarizes our research into user behavior patterns.

## Key Observations
1. Users prefer simplified interfaces
2. Mobile usage has increased 40% year-over-year
3. Average session duration: 8.5 minutes

## Recommendations
- Optimize mobile experience
- Streamline navigation
- Implement progressive disclosure

## Next Steps
Further analysis needed on conversion patterns.`,

  'bibliography.txt': `Bibliography

1. Smith, J. (2025). Modern Web Development. Tech Press.
2. Johnson, A. (2024). User Experience Design Patterns. UX Publishers.
3. Chen, L. (2026). React Performance Optimization. Dev Books.
4. Rodriguez, M. (2025). TypeScript Best Practices. Code Academy Press.`,

  'whitepaper.pdf': `Technical Whitepaper
Advanced Document Processing Systems

Abstract
This paper presents a novel approach to document analysis using machine learning techniques.

Introduction
Traditional document processing faces challenges in scalability and accuracy.

Methodology
Our system employs a multi-stage pipeline:
1. Document ingestion
2. Text extraction
3. Natural language processing
4. Entity recognition
5. Sentiment analysis

Results
Accuracy improved by 35% compared to baseline methods.
Processing speed increased 10x through parallel processing.

Conclusion
The proposed system demonstrates significant improvements in both accuracy and efficiency.`,

  'draft.docx': `Research Paper Draft

Title: Impact of AI on Document Management

Section 1: Introduction
Document management has evolved significantly with AI integration.

Section 2: Literature Review
Previous studies have shown varying results...

Section 3: Methodology
We conducted a comparative analysis of 5 systems over 6 months.

[Note: This is a work in progress - more sections to be added]`,

  'CHANGELOG.md': `# Changelog

## [1.0.0] - 2026-06-01

### Added
- Initial release
- File tree with folder/file structure
- Document viewer with analysis types
- Chat window with preset prompts
- Resizable panels
- Search functionality

### Features
- Support for .md, .txt, .pdf, .docx files
- Multiple analysis modes (summary, sentiment, entities, key points)
- Context-aware chat integration`,

  'project-plan.pdf': `Project Implementation Plan

Phase 1: Foundation (Weeks 1-2)
- Set up development environment
- Implement basic file tree structure
- Create document viewer component

Phase 2: Core Features (Weeks 3-4)
- Add analysis capabilities
- Implement search and filter
- Build chat integration

Phase 3: Polish (Week 5)
- UI/UX improvements
- Performance optimization
- Testing and bug fixes

Phase 4: Deployment (Week 6)
- Production build
- Documentation
- Launch

Timeline: 6 weeks total
Team: 4 developers
Budget: $50,000`,
};

const analysisOptions: Array<{ value: AnalysisType; label: string; icon: typeof FileText }> = [
  { value: 'original', label: 'Original', icon: FileText },
  { value: 'summary', label: 'Summary', icon: FileCode },
  { value: 'keyPoints', label: 'Key Points', icon: ListChecks },
  { value: 'sentiment', label: 'Sentiment', icon: Smile },
  { value: 'entities', label: 'Entities', icon: Tag },
];

export default function FileViewer({ selectedFiles, onFileIndexChange }: FileViewerProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [analysisType, setAnalysisType] = useState<AnalysisType>('original');

  useEffect(() => {
    // Reset to first file if current index is out of bounds
    if (currentIndex >= selectedFiles.length && selectedFiles.length > 0) {
      setCurrentIndex(0);
    }
  }, [selectedFiles.length, currentIndex]);

  // Notify parent when index changes
  useEffect(() => {
    if (onFileIndexChange && selectedFiles.length > 0) {
      onFileIndexChange(currentIndex);
    }
  }, [currentIndex, selectedFiles.length]);

  if (selectedFiles.length === 0) {
    return (
      <div className="border border-gray-200 rounded-lg p-8 bg-white flex flex-col items-center justify-center h-full min-h-[400px]">
        <FileText size={48} className="text-gray-300 mb-4" />
        <p className="text-gray-500">No files selected</p>
        <p className="text-sm text-gray-400 mt-2">Select files from the tree to view their content</p>
      </div>
    );
  }

  const currentFile = selectedFiles[currentIndex];

  // Determine file type
  const getFileType = (fileName: string): 'pdf' | 'docx' | 'txt' | 'md' | 'image' | 'unknown' => {
    if (fileName.endsWith('.pdf')) return 'pdf';
    if (fileName.endsWith('.docx')) return 'docx';
    if (fileName.endsWith('.txt')) return 'txt';
    if (fileName.endsWith('.md')) return 'md';
    if (fileName.match(/\.(jpg|jpeg|png|gif|svg)$/i)) return 'image';
    return 'unknown';
  };

  const fileType = getFileType(currentFile.name);
  const isOriginalView = analysisType === 'original';

  // Get content based on analysis type
  let content: string;
  let isHtmlContent = false;

  if (mockFileContent[currentFile.name]) {
    content = mockFileContent[currentFile.name][analysisType];
    isHtmlContent = !isOriginalView;
  } else if (simpleContent[currentFile.name]) {
    content = analysisType === 'original' ? simpleContent[currentFile.name] : `Analysis not available for this file type.\n\nThis file does not support ${analysisOptions.find(opt => opt.value === analysisType)?.label.toLowerCase()} analysis.`;
    isHtmlContent = !isOriginalView;
  } else {
    content = 'No preview available for this file.';
    isHtmlContent = false;
  }

  // Convert analysis content to HTML
  const formatAsHtml = (text: string): string => {
    // Convert plain text to HTML with proper formatting
    return text
      .split('\n\n')
      .map(paragraph => {
        // Check if it's a header
        if (paragraph.startsWith('Overall Sentiment:') || paragraph.startsWith('Time Period:') || paragraph.startsWith('Organizations:')) {
          return `<h3 class="font-semibold text-gray-900 mt-4 mb-2">${paragraph}</h3>`;
        }
        // Check if it's a bullet list
        if (paragraph.startsWith('•')) {
          const items = paragraph.split('\n').map(line =>
            line.trim().startsWith('•') ? `<li class="ml-4">${line.substring(1).trim()}</li>` : ''
          ).join('');
          return `<ul class="list-disc list-inside space-y-1 text-gray-700">${items}</ul>`;
        }
        // Check if it's a key-value pair with indentation
        if (paragraph.includes(':') && paragraph.includes('  -')) {
          const lines = paragraph.split('\n').map(line => {
            if (line.includes(':') && !line.startsWith(' ')) {
              return `<p class="font-semibold text-gray-900 mt-2">${line}</p>`;
            } else if (line.trim().startsWith('-')) {
              return `<p class="ml-4 text-gray-700">${line.trim()}</p>`;
            }
            return `<p class="text-gray-700">${line}</p>`;
          }).join('');
          return lines;
        }
        // Regular paragraph
        return `<p class="text-gray-700 mb-3">${paragraph.replace(/\n/g, '<br>')}</p>`;
      })
      .join('');
  };

  const handlePrevious = () => {
    setCurrentIndex((prev) => prev > 0 ? prev - 1 : selectedFiles.length - 1);
  };

  const handleNext = () => {
    setCurrentIndex((prev) => prev < selectedFiles.length - 1 ? prev + 1 : 0);
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <div className="flex min-h-0 flex-1 flex-col rounded-lg border border-gray-200 bg-white">
        <div className="border-b border-gray-200 p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex-1">
              <h3 className="font-semibold">{currentFile.name}</h3>
              <p className="text-xs text-gray-500 mt-1">{currentFile.path}</p>
            </div>
          </div>

          {/* Analysis Type Selector */}
          <div className="flex gap-2">
            {analysisOptions.map((option) => {
              return (
                <button
                  key={option.value}
                  onClick={() => setAnalysisType(option.value)}
                  className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
                    analysisType === option.value
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                  }`}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex-1 p-6 overflow-auto">
          {isOriginalView && fileType === 'pdf' ? (
            <div className="h-full flex flex-col items-center justify-center bg-gray-100 rounded-lg border-2 border-dashed border-gray-300">
              <div className="text-center p-8">
                <FileText size={64} className="text-gray-400 mb-4 mx-auto" />
                <p className="text-gray-600 font-semibold mb-2">{currentFile.name}</p>
                <p className="text-sm text-gray-500">PDF Document Preview</p>
                <div className="mt-4 p-4 bg-white rounded border border-gray-200 max-w-2xl">
                  <div className="text-left text-sm text-gray-700">
                    {mockFileContent[currentFile.name]?.original || simpleContent[currentFile.name] || 'PDF content would be displayed here'}
                  </div>
                </div>
              </div>
            </div>
          ) : isOriginalView && fileType === 'docx' ? (
            <div className="h-full flex flex-col items-center justify-center bg-gray-100 rounded-lg border-2 border-dashed border-gray-300">
              <div className="text-center p-8">
                <FileText size={64} className="text-blue-500 mb-4 mx-auto" />
                <p className="text-gray-600 font-semibold mb-2">{currentFile.name}</p>
                <p className="text-sm text-gray-500">Word Document Preview</p>
                <div className="mt-4 p-6 bg-white rounded border border-gray-200 max-w-2xl shadow-sm">
                  <div className="text-left text-sm text-gray-700 whitespace-pre-wrap">
                    {mockFileContent[currentFile.name]?.original || simpleContent[currentFile.name] || 'Word document content would be displayed here'}
                  </div>
                </div>
              </div>
            </div>
          ) : isOriginalView && (fileType === 'txt' || fileType === 'md') ? (
            <pre className="whitespace-pre-wrap font-mono text-sm text-gray-800 bg-white p-4 rounded border border-gray-200">
              {content}
            </pre>
          ) : isHtmlContent ? (
            <div
              className="prose prose-sm max-w-none"
              dangerouslySetInnerHTML={{ __html: formatAsHtml(content) }}
            />
          ) : (
            <pre className="whitespace-pre-wrap font-mono text-sm text-gray-800">
              {content}
            </pre>
          )}
        </div>
      </div>

      {selectedFiles.length > 1 && (
        <nav className="flex shrink-0 items-center justify-between gap-4 px-1" aria-label="File navigation">
          <button
            onClick={handlePrevious}
            className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm font-semibold text-gray-800 shadow-sm transition-colors hover:bg-gray-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)]"
            aria-label="Previous file"
          >
            <ChevronLeft size={16} />
            Previous
          </button>
          <span className="text-sm font-medium text-gray-600">
            {currentIndex + 1} of {selectedFiles.length}
          </span>
          <button
            onClick={handleNext}
            className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-[var(--shell-accent-strong)] px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-[var(--shell-accent-mid)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--shell-accent-strong)] focus-visible:ring-offset-2"
            aria-label="Next file"
          >
            Next
            <ChevronRight size={16} />
          </button>
        </nav>
      )}
    </div>
  );
}
