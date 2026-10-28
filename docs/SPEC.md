# Term Project: Socket Programming — Simple Chat Application

> Converted from `../Computer_Network_Term_Project___Socket_Programming-*.pdf` (kept outside the repo, one folder up). The PDF is authoritative.
> Submission / presentation: **Mon 9 Nov 2026** (week 13 in the syllabus).

## Background

Socket programming is an alternative way of developing a communication channel between two hosts
over the network. A socket is bound to a port number to identify the application process. Students
may use any language (Java, C++, Python, C, …) on any platform. **The client and server processes
must be implemented in separate files.** The chat application must communicate between two or more
computers like a regular chat application.

## Objectives

- Understand socket programming
- Apply socket programming in a simple chat application

## Grading (full score = 12.5)

Students present an **"Implementation Explanation" slide deck** with a live demonstration, and
explain the concepts and code related to socket programming and to the requirements, as questioned by the instructor.

### Part 1 — System Architecture Design (1.5 pts). Must be complete, or Part 2 is not graded

- **R1 (1.0)** Present the system architecture. **At least two clients, each on a different physical
  computer.** The server may run on one of them or on a third machine.
  - Explain the network protocols used: *what* protocols, *why* each was selected, *how* each is used.
  - Cloud deployment is optional and counts as one special point.
- **R2 (0.5)** Chat messages between the server and each client must be implemented using **socket programming only**.

### Part 2 — Fundamental implementation (7 pts). Must be complete, or Part 3 is not graded

- **R3 (0.5)** Each client has a unique name.
- **R4 (0.5)** Each client can see the list of names of all currently connected clients, **including its own name**.
- **R5 (0.5)** Each chat between clients (private and group) has **its own chat room**.
- **R6 (0.5)** Each chat room has a **chat box** for sending text and a **chat window** showing sent messages and new incoming messages.
- Private message
  - **R7 (1.0)** A client can send a direct text message to any client in the list. **Only sender and receiver** can see it.
- Group message
  - **R8 (1.0)** A client can create chat group(s), which **initially include only the creator**.
  - **R9 (1.0)** A client can see the list of **all existing groups, with their member lists**, created by any client.
  - **R10 (1.0)** Clients join a group **only by themselves**; the creator never adds them automatically.
  - **R11 (1.0)** A client can send a text message to a group it has joined. **Only group members** can see it.

### Part 3 — Special points (4 pts, 1.0 per feature)

At least one feature **must be an AI feature** (speech-to-text, summarization, sentiment analysis, chatbot, …).

Our planned features (all four are AI features, one per team member; UI and owners in [`AI_FEATURES.md`](AI_FEATURES.md)):

1. **Translation (mee):** a *Translate* link under each message; the server asks Gemini to translate that one message into the language chosen in the room's *Translate to* picker. No auto-translate.
2. **Sentiment analysis (magga):** an *Analyze mood* button on each message; the server classifies that one message as positive, neutral or negative.
3. **Text summarization (euro):** `Summarize` button in any room you can see → server asks Gemini to summarize that room's recent history (offline fallback if no API key).
4. **Chatbot (timsum):** an *AI Assistant* tab with its own private conversation; the server answers questions with Gemini (offline fallback message if no API key).
5. *(Optional)* **Cloud deployment** of the server on a free VM.

Present the 4 AI features; cloud deployment is a spare fifth if time allows.

## Slide template structure (`…Implementation_Explanation….pdf`)

1. Title: Group number, student IDs and names
2. R1: System Architecture Diagram + explanation
3. R1: Protocols — what, why, how
4. R2: Code snapshot + generative AI prompt (if used)
5. R3 … R11: one slide each, containing **Code Snapshot (Mandatory)**, Code Snapshot, and **Generative AI Prompt (if use)** ×2
6. Special Points 1–4: description (+ AI model and reference for the AI feature), code snapshots, and AI prompts
