# ServiceNow browser work

Prefer the read-only ServiceNow API client for record queries. When a browser is
needed, let the user sign in and use the newest snapshot's frame-prefixed refs.
Use `agent-browser snapshot` after navigation and `agent-browser text` to verify
the displayed record. Preview record changes and submit only within the user's
explicit authorization. A successful click does not prove the record was saved;
verify the resulting record and any validation message.
