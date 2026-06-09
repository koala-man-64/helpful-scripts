using FileManagerBlazor.Models;

namespace FileManagerBlazor.Data;

public static class MockFileData
{
    public static readonly string[] AllowedExtensions = [".md", ".txt", ".docx", ".pdf"];

    public static IReadOnlyList<FileNode> FileStructure { get; } =
    [
        new(
            "1",
            "Documents",
            FileNodeType.Folder,
            [
                new(
                    "1-1",
                    "Work",
                    FileNodeType.Folder,
                    [
                        new("1-1-1", "report.pdf", FileNodeType.File),
                        new("1-1-2", "quarterly-review.docx", FileNodeType.File),
                        new("1-1-3", "meeting-notes.txt", FileNodeType.File)
                    ]),
                new(
                    "1-2",
                    "Personal",
                    FileNodeType.Folder,
                    [
                        new("1-2-1", "resume.pdf", FileNodeType.File),
                        new("1-2-2", "notes.txt", FileNodeType.File),
                        new("1-2-3", "journal.docx", FileNodeType.File)
                    ])
            ]),
        new(
            "2",
            "Research",
            FileNodeType.Folder,
            [
                new("2-1", "findings.md", FileNodeType.File),
                new("2-2", "bibliography.txt", FileNodeType.File),
                new(
                    "2-3",
                    "Papers",
                    FileNodeType.Folder,
                    [
                        new("2-3-1", "whitepaper.pdf", FileNodeType.File),
                        new("2-3-2", "draft.docx", FileNodeType.File)
                    ])
            ]),
        new(
            "3",
            "Projects",
            FileNodeType.Folder,
            [
                new("3-1", "README.md", FileNodeType.File),
                new("3-2", "CHANGELOG.md", FileNodeType.File),
                new("3-3", "project-plan.pdf", FileNodeType.File)
            ])
    ];

    public static IReadOnlyList<PresetPrompt> PresetPrompts { get; } =
    [
        new("help", "Help", "How can I use this application?"),
        new("analyze", "Analyze", "Can you analyze the selected documents and provide insights?"),
        new("compare", "Compare", "Compare the selected documents and highlight key differences."),
        new("summarize", "Summarize", "Provide a comprehensive summary of the selected documents."),
        new("export", "Export", "How can I export the selected files?")
    ];

    private static readonly Dictionary<string, AnalysisContent> FullAnalysis = new(StringComparer.OrdinalIgnoreCase)
    {
        ["report.pdf"] = new(
            Original: """
                Annual Report 2025

                Executive Summary
                This report provides a comprehensive overview of our company's performance throughout the fiscal year 2025.

                Key Highlights:
                - Revenue increased by 23% year-over-year
                - Expanded operations to 5 new markets
                - Customer satisfaction rating improved to 4.8/5.0
                - Successfully launched 3 new product lines

                Financial Performance
                Total revenue reached $45.2M, representing strong growth across all business segments...
                """,
            Summary: """
                Annual Report 2025 Summary:
                The company demonstrated strong performance in FY2025 with 23% revenue growth, reaching $45.2M total revenue. The organization successfully expanded into 5 new markets and launched 3 new product lines. Customer satisfaction improved to 4.8/5.0, indicating positive market reception.
                """,
            KeyPoints: """
                - Revenue grew 23% YoY to $45.2M
                - Expanded to 5 new markets
                - Customer satisfaction: 4.8/5.0
                - Launched 3 new product lines
                - Strong growth across all business segments
                """,
            Sentiment: """
                Overall Sentiment: POSITIVE (Score: 0.85/1.0)

                The document expresses highly positive sentiment throughout, highlighting achievements and growth. Key positive indicators include revenue growth, market expansion, and improved customer satisfaction.

                Confidence: High
                """,
            Entities: """
                Organizations: [Company Name]
                Time Periods: FY 2025
                Metrics:
                  - Revenue: $45.2M
                  - Growth Rate: 23% YoY
                  - Customer Satisfaction: 4.8/5.0
                  - New Markets: 5
                  - New Products: 3
                """),
        ["quarterly-review.docx"] = new(
            Original: """
                Quarterly Review - Q2 2026

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
                - Implement customer feedback system
                """,
            Summary: """
                Q2 2026 showed strong performance with revenue exceeding targets by 15% and customer retention at 96%. Three major products launched successfully, and team grew by 12 people. Focus for next quarter includes enterprise tier launch and APAC expansion.
                """,
            KeyPoints: """
                - Revenue exceeded targets by 15%
                - Customer retention: 96%
                - 3 major product releases completed
                - 12 new team members hired
                - Next: Enterprise tier launch
                - Next: APAC market expansion
                - Next: Customer feedback system
                """,
            Sentiment: """
                Overall Sentiment: HIGHLY POSITIVE (Score: 0.88/1.0)

                Exceptionally positive tone throughout, emphasizing achievements and growth. Forward-looking statements express confidence and ambition.

                Confidence: High
                """,
            Entities: """
                Time Period: Q2 2026
                Metrics:
                  - Revenue growth: +15%
                  - Customer retention: 96%
                  - Product releases: 3
                  - New hires: 12
                Initiatives: Enterprise tier, APAC expansion, Customer feedback system
                """),
        ["notes.txt"] = new(
            Original: """
                Meeting Notes - June 1, 2026

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
                - Jennifer: Schedule follow-up meeting for June 15
                """,
            Summary: """
                Meeting focused on Q3 planning and infrastructure improvements. Key decision: prioritize mobile app refresh (8-10 week timeline, requires 2 frontend developers). Infrastructure work includes July Kubernetes migration and immediate database optimization. Three action items assigned with specific deadlines.
                """,
            KeyPoints: """
                - Q3 Priority: Mobile app refresh (8-10 weeks)
                - Need 2 additional frontend developers
                - Kubernetes migration: July
                - Database optimization: starting next week
                - Action items assigned to Sarah, Mike, and Jennifer with deadlines
                """,
            Sentiment: """
                Overall Sentiment: NEUTRAL-POSITIVE (Score: 0.55/1.0)

                The meeting notes are primarily informational with a slight positive lean toward planned improvements. No negative indicators present. Tone is professional and action-oriented.

                Confidence: Medium
                """,
            Entities: """
                People: Sarah, Mike, Jennifer, Tom
                Date: June 1, 2026
                Deadlines:
                  - June 5: Vendor proposals (Mike)
                  - June 8: Technical spec (Sarah)
                  - June 15: Follow-up meeting (Jennifer)
                Technologies: Kubernetes, Mobile App
                Timeline: Q3, 8-10 weeks, July
                """),
        ["resume.pdf"] = new(
            Original: """
                John Doe
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
                - Implemented CI/CD pipelines reducing deployment time by 60%
                """,
            Summary: """
                John Doe is a Senior Software Engineer with 8+ years of full-stack development experience. Currently at Tech Corp (2020-present), he leads microservices development for 2M+ users. Previously at StartUp Inc (2017-2020), he built React/TypeScript applications and CI/CD infrastructure. Specializes in React, Node.js, and cloud architecture.
                """,
            KeyPoints: """
                - 8+ years full-stack development experience
                - Specialization: React, Node.js, cloud architecture
                - Current: Senior Software Engineer at Tech Corp (2020-present)
                - Led microservices serving 2M+ users
                - 40% application performance improvement
                - Mentored 5 junior developers
                - Previous: StartUp Inc (2017-2020)
                """,
            Sentiment: """
                Overall Sentiment: POSITIVE (Score: 0.72/1.0)

                Resume demonstrates strong career progression and achievements. Language emphasizes results and leadership ("led", "reduced", "mentored"). Quantifiable achievements create positive impression.

                Confidence: High
                """,
            Entities: """
                Person: John Doe
                Role: Senior Software Engineer
                Companies: Tech Corp (2020-present), StartUp Inc (2017-2020)
                Skills: React, Node.js, TypeScript, Cloud Architecture, Microservices, CI/CD
                Experience: 8+ years
                Contact: john.doe@email.com, (555) 123-4567
                Achievements:
                  - 2M+ users served
                  - 40% performance improvement
                  - 60% deployment time reduction
                  - 5 developers mentored
                """),
        ["README.md"] = new(
            Original: """
                # File Manager Application

                A modern web-based file management system with advanced document analysis capabilities.

                ## Features

                - Hierarchical file tree navigation
                - Document viewer with multiple analysis types
                - Integrated chat support with AI assistance
                - Resizable and collapsible panels
                - Search and filter functionality

                ## Technology Stack

                - Blazor WebAssembly
                - .NET 10
                - C#
                - App-local CSS

                ## Usage

                Select files from the tree view to preview and analyze documents.
                """,
            Summary: """
                Documentation for a modern web-based file management system featuring hierarchical navigation, document analysis, and AI chat support. This copy is implemented with Blazor WebAssembly and .NET 10.
                """,
            KeyPoints: """
                - Hierarchical file tree navigation
                - Document viewer with analysis types
                - Integrated AI chat support
                - Resizable/collapsible panels
                - Search and filter functionality
                - Tech: Blazor WebAssembly, .NET 10, C#
                """,
            Sentiment: """
                Overall Sentiment: NEUTRAL-POSITIVE (Score: 0.60/1.0)

                Technical documentation with factual tone. Presents features and setup instructions objectively.

                Confidence: Medium
                """,
            Entities: """
                Project: File Manager Application
                Technologies: Blazor WebAssembly, .NET 10, C#
                Features: File tree, Document viewer, Chat support, Analysis modes
                File types: .md, .txt, .pdf, .docx
                """)
    };

    private static readonly Dictionary<string, string> SimpleContent = new(StringComparer.OrdinalIgnoreCase)
    {
        ["meeting-notes.txt"] = """
            Team Standup - June 1, 2026

            Attendees: Alex, Jordan, Sam, Taylor

            Updates:
            - Alex: Completed API integration, starting frontend work
            - Jordan: Code review in progress, addressing feedback
            - Sam: Working on database optimization
            - Taylor: User testing scheduled for next week

            Blockers: None

            Next Meeting: June 8, 2026
            """,
        ["journal.docx"] = """
            Personal Journal - May 2026

            Reflections on the past month:

            The project launch went better than expected. The team worked cohesively and delivered ahead of schedule. Key learnings included better time management and more effective communication patterns.

            Goals for next month:
            - Complete certification program
            - Mentor two junior team members
            - Improve work-life balance
            """,
        ["findings.md"] = """
            # Research Findings

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
            Further analysis needed on conversion patterns.
            """,
        ["bibliography.txt"] = """
            Bibliography

            1. Smith, J. (2025). Modern Web Development. Tech Press.
            2. Johnson, A. (2024). User Experience Design Patterns. UX Publishers.
            3. Chen, L. (2026). React Performance Optimization. Dev Books.
            4. Rodriguez, M. (2025). TypeScript Best Practices. Code Academy Press.
            """,
        ["whitepaper.pdf"] = """
            Technical Whitepaper
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
            The proposed system demonstrates significant improvements in both accuracy and efficiency.
            """,
        ["draft.docx"] = """
            Research Paper Draft

            Title: Impact of AI on Document Management

            Section 1: Introduction
            Document management has evolved significantly with AI integration.

            Section 2: Literature Review
            Previous studies have shown varying results...

            Section 3: Methodology
            We conducted a comparative analysis of 5 systems over 6 months.

            [Note: This is a work in progress - more sections to be added]
            """,
        ["CHANGELOG.md"] = """
            # Changelog

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
            - Context-aware chat integration
            """,
        ["project-plan.pdf"] = """
            Project Implementation Plan

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
            Budget: $50,000
            """,
        ["claims-intake-playbook.pdf"] = """
            Claims Intake Playbook

            Purpose
            This playbook defines how front-door claims staff capture, validate, and route new claim submissions.

            Intake Requirements
            - Confirm member identity before opening a case.
            - Record claim type, date of service, provider identifier, and urgency indicator.
            - Attach supporting documents before routing the claim downstream.

            Escalation Rules
            Urgent clinical, payment, or member harm concerns require same-day supervisor review. Duplicate or incomplete submissions must be flagged before case creation.

            Handoff Notes
            Intake owns first-contact quality. Adjudication owns benefit interpretation after the intake record is complete.
            """,
        ["appeals-resolution-guide.docx"] = """
            Appeals Resolution Guide

            Overview
            This guide describes the standard appeals path from member dispute intake through final determination.

            Required Evidence
            - Original denial reason
            - Member appeal statement
            - Applicable plan language
            - Clinical or payment records used in review

            Review Sequence
            Appeals coordinators validate timeliness, assign the case to the correct reviewer, and document every outbound member notice.

            Closure Criteria
            A case may close only after determination language, evidence references, and notification dates are complete.
            """,
        ["notice-generation-style-guide-long-title.docx"] = """
            Notice Generation Style Guide

            Purpose
            Member notices must be clear, consistent, timely, and traceable to the source decision.

            Writing Standards
            - Use direct member-facing language.
            - Include the decision, reason, effective date, and next action.
            - Avoid unsupported abbreviations.
            - Reference appeal rights when required.

            Quality Review
            A second reviewer checks regulatory timing, template selection, and source-record alignment before release.
            """,
        ["clinical-review-policy.md"] = """
            # Clinical Review Policy Brief

            ## Scope
            This brief covers prior authorization clinical review for services that require medical necessity assessment.

            ## Review Criteria
            Clinical reviewers compare the request, diagnosis, treatment history, and supporting notes against active policy criteria.

            ## Decision Documentation
            Each decision must identify the criterion used, the evidence reviewed, and the rationale for approval, denial, or escalation.

            ## Operational Note
            Ambiguous requests should be routed for peer review rather than denied for incomplete evidence without outreach.
            """,
        ["authorization-data-quality-checklist.pdf"] = """
            Authorization Data Quality Checklist

            Objective
            Ensure prior authorization records are complete enough for downstream review, reporting, and audit.

            Checklist
            - Member and provider identifiers are present.
            - Service codes match the requested treatment.
            - Request date and receipt channel are recorded.
            - Required clinical attachments are linked.
            - Decision reason codes align with reviewer notes.

            Exception Handling
            Records that fail validation remain in pending status until corrected or explicitly waived by an authorized lead.
            """,
        ["complete-claims-corpus.zip"] = """
            Complete Claims Document Corpus

            Archive Manifest
            This source package contains claims intake procedures, appeals guidance, notice templates, data quality checklists, and historical control notes.

            Review Guidance
            Treat the archive as a corpus-level input. Verify included folder names, document categories, and date coverage before running cross-document analysis.

            Known Limits
            The archive preview lists package contents only. Individual files should be opened separately for document-level analysis.
            """
    };

    public static bool IsAllowedFile(string fileName) =>
        AllowedExtensions.Any(extension => fileName.EndsWith(extension, StringComparison.OrdinalIgnoreCase));

    public static bool HasAllowedFiles(FileNode node)
    {
        if (node.Type == FileNodeType.File)
        {
            return IsAllowedFile(node.Name);
        }

        return node.Children?.Any(HasAllowedFiles) == true;
    }

    public static string GetContent(string fileName, AnalysisType analysisType)
    {
        if (FullAnalysis.TryGetValue(fileName, out var analysisContent))
        {
            return analysisContent.GetText(analysisType).Trim();
        }

        if (SimpleContent.TryGetValue(fileName, out var original))
        {
            return analysisType == AnalysisType.Original
                ? original.Trim()
                : $"Analysis not available for this file type.\n\nThis file does not support {analysisType.Label().ToLowerInvariant()} analysis.";
        }

        return "No preview available for this file.";
    }
}
