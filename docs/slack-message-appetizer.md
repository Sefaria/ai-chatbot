Added a "Topic Appetizer" to the chatbot — a yellow box that shows a relevant Sefaria topic link while the main agent is still working.

How it works: right after auth, a parallel thread extracts the key topic from the prompt (regex first, Haiku fallback if needed), searches Sefaria's topic API, and pushes the result to the SSE stream. The main agent pipeline runs independently.

On production it shows up in ~5 seconds. PR #136 on `waiting-source`.
