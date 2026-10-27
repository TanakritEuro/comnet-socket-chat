# Slide Notes

Material for the "Implementation Explanation" slides. Update this in the same PR that implements each requirement.
For each item, give: **how it works** (1–2 sentences), **code snapshot** (file → function, line range), and **AI prompt used** (quoted, or "none").

## R1: System architecture
- Diagram: _TODO (server, ≥2 client machines, LAN or cloud, Gemini API)_
- Protocols (what / why / how):
  - TCP: _TODO_
  - IP (IPv4, LAN / Internet): _TODO_
  - Custom application protocol (length-prefixed JSON, see PROTOCOL.md): _TODO_
  - HTTPS (TLS over TCP) to the Gemini API: _TODO_

## R2: Socket programming only
- How: _TODO_
- Code snapshot: _TODO_
- AI prompt: _TODO_

## R3: Unique name
## R4: Online user list (incl. self)
## R5: Separate chat room per chat
## R6: Chat box + chat window
## R7: Private message
## R8: Create group (creator is only member)
## R9: List all groups with members
## R10: Join group by yourself
## R11: Group message (members only)

## Special 1: AI chat summarization
- AI model: _TODO (model name + link to docs)_
## Special 2: File / image sending
## Special 3: Chat history + unread badges
## Special 4: Typing indicator + online status
## (Optional) Cloud deployment
