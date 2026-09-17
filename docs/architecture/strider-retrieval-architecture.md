# Strider retrieval architecture

`strider-retrieval-architecture.html` is a single self-contained design
document (open it in any browser; light and dark themes) for the chatbot's
knowledge pipeline:

- **Current state** — `Training` (AKS) reads `snowindex_refactored.json`, the
  run manifest of agents, sources and user tiers, from a local clone of the
  DevControlPlane repo. It pulls articles from GitHub, SharePoint, ServiceNow,
  Azure Blob Storage and local files, delta-merges them with the prior run's
  blob state (`kb_metadata.json`, `delta_links.json`), and writes per-agent
  `[agent_name]_index.json` title-to-content dictionaries to Azure Blob
  Storage, committing them back to git only when a file is under about 90 MB.
  `Chatbot` (App Service) filters `agents_metadata.json` by user permissions
  and calls `ContextFinder` (`username, question, agent, top_n`), which scores
  the agent's index with hand-written lexical logic and returns the `top_n`
  articles for the context window.
- **Proposal (section 08)** — replace the index JSON files and the manual
  scorer with an Azure AI Search index fed by the training job, keeping the
  ContextFinder contract unchanged: index schema, example query, migration
  plan, costs, and the open embedding-model decision.

The only external dependency is Google Fonts (IBM Plex); offline it falls back
to system fonts with the same layout.
