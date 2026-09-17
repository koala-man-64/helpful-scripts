# Strider retrieval architecture

`strider-retrieval-architecture.html` is a single self-contained design
document (open it in any browser; light and dark themes) for the Strider
chatbot's knowledge pipeline:

- **Current state** — `Strider.AI.Training` (AKS) reads
  `snow_index_refactored.json` from the DevControlPlane repo, pulls articles
  from GitHub, SharePoint and ServiceNow, and writes `service_metadata.json`
  and per-agent `[agent_name]_index.json` title-to-content dictionaries back
  to the repo. `Strider.AI` (App Service) filters `agents_metadata.json` by
  user permissions and calls `Strider.AI.ContextFinder`
  (`username, question, agent, top_n`), which scores the agent's index with
  hand-written lexical logic and returns the `top_n` articles for the
  context window.
- **Proposal (section 08)** — replace the index JSON files and the manual
  scorer with an Azure AI Search index fed by the training job, keeping the
  ContextFinder contract unchanged: index schema, example query, migration
  plan, costs, and the open embedding-model decision.

The only external dependency is Google Fonts (IBM Plex); offline it falls back
to system fonts with the same layout.
