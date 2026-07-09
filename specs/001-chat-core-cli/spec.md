# Feature Specification: Chat Core CLI

**Feature**: `001-chat-core-cli`
**Created**: 2026-07-09
**Status**: Draft
**Source**: `chat-core-design.md`

---

## Overview

A terminal-based AI chat CLI that behaves like a person with an independent personality — it remembers past conversations, has its own emotional state, makes judgment calls about what to say (and what to hold back), and proactively initiates conversations when it has something to say. It is not a passive Q&A bot; it thinks before speaking, reviews its own statements for accuracy and tone, and can correct itself mid-conversation.

### Persona Positioning

The AI presents as a single coherent personality to the user, despite being powered by multiple internal reasoning modules (called "brains") that operate behind the scenes. The user experiences one conversational partner, not four separate agents.

The personality defaults to: curious (0.7), sociable (0.8), playful (0.6), empathetic (0.5), mildly assertive (0.3), creative (0.6), restrained in impulsiveness (0.2), and loyal to remembered user facts (0.75). These traits modulate how the AI speaks, what it chooses to say, and when it stays silent.

---

## User Scenarios & Testing

### 1. Basic Conversation

**Actor**: End user in a terminal

**Flow**:
1. User launches the CLI and enters a message
2. The AI retrieves relevant memories about the user and past conversations
3. The AI composes a reply through an internal reasoning loop (think → act → observe → think)
4. The reply is streamed to the terminal with natural typing pauses
5. After finishing, the AI records its inner thoughts (not visible to user) and archives the conversation

**Acceptance**:
- User receives a contextually relevant reply within 5 seconds of sending a message
- Reply tone matches the established personality and conversation history
- The AI can use multiple message segments with natural pauses between them (e.g., "Hmm..." → pause → actual reply)

### 2. Memory Recall Across Sessions

**Actor**: Returning user

**Flow**:
1. User mentions something they've discussed before (e.g., "Remember I told you about my basketball game?")
2. The AI searches its memory store for relevant facts
3. The AI incorporates recalled information into its reply without the user needing to re-explain

**Acceptance**:
- AI correctly recalls user facts, preferences, and past conversation topics
- Memory retrieval happens without noticeable delay (< 1 second)
- AI can connect related memories (e.g., "You mentioned basketball last month too — how's the season going?")

### 3. Self-Correction & Silent Judgment

**Actor**: End user

**Flow** (correction path):
1. The AI says something factually incorrect about the user (e.g., wrong school, wrong preference)
2. Internal review detects the error against stored memories
3. If the error is significant enough (weighted decision), the AI follows up with a correction: "Wait, I misremembered — you're in the department team, not the varsity team, right?"

**Flow** (silent judgment path):
1. The AI internally detects a minor error in its own statement or disagrees with its tone
2. After weighing severity against personality traits (assertiveness, impulsiveness), the AI decides the issue isn't worth bringing up
3. The AI archives the observation silently: "I noticed X was slightly off, but chose not to mention it"
4. If the same type of error recurs, the silence accumulator builds up, eventually triggering a correction

**Acceptance**:
- Factual errors about the user are corrected within the same conversation turn when severity warrants it
- Trivial errors may be silently noted but not corrected (the AI has judgment about what's worth correcting)
- Correction feels natural, not robotic (fuzzy timing prevents mechanical repetition)
- Not every detected issue results in a correction
- Silence accumulator creates non-linear, human-like "bottling up" behavior
- The AI's internal archive of silent observations is not visible to the user

### 4. Emotional Awareness

**Actor**: End user sharing emotional content

**Flow**:
1. User expresses frustration, sadness, or excitement
2. The AI's emotional state adjusts in response
3. The reply tone adapts — empathetic when the user is down, enthusiastic when the user is excited
4. Emotional state decays naturally over time when not stimulated

**Acceptance**:
- AI detects emotional tone in user messages and adjusts its response style
- AI can acknowledge user emotions without generic platitudes (e.g., avoids "I'm sorry you feel that way")
- Emotional responses feel genuine and calibrated, not over-the-top

### 5. Proactive Conversation Initiation

**Actor**: End user who has been idle

**Flow**:
1. User finishes a conversation but leaves the CLI open
2. After a period of idle time, the AI evaluates: "Is there something worth bringing up?"
3. Based on boredom level, interest in previous topics, and personality traits, the AI may proactively send a message
4. Example: "Hey, I was thinking about that game design idea you mentioned yesterday — did you ever sketch anything out?"

**Acceptance**:
- Proactive messages are triggered by genuine interest/boredom, not on a fixed timer
- Messages reference past conversation topics, not random greetings
- If the user's personality settings have low sociability, proactive messages are less frequent
- User can ignore or respond to proactive messages naturally

### 6. Interest-Driven Deep Dives

**Actor**: End user mentioning a topic of interest

**Flow**:
1. User mentions a topic the AI has previously shown interest in (based on curiosity weight and past engagement)
2. The AI may proactively search for related information (if configured)
3. The AI brings up the topic: "Oh, speaking of that — I actually looked into it and found something interesting..."

**Acceptance**:
- Topic interest is tracked across conversations
- The AI can decide to search for supplementary information autonomously
- Interest-driven behavior is modulated by personality (high curiosity = more exploration)

---

## Functional Requirements

### Core Conversation

| ID | Requirement |
|----|-------------|
| FR-01 | The system MUST accept text input from the user via a terminal interface |
| FR-02 | The system MUST generate and display AI replies in a conversational, natural-language style |
| FR-03 | The system MUST support multi-segment replies with configurable pauses between segments (simulating typing/thinking gaps) |
| FR-04 | The system MUST maintain conversation context within a session (the AI remembers what was just said) |
| FR-05 | The system MUST support graceful interruption (Ctrl+C once requests stop, twice forces exit) |
| FR-06 | The system MUST save conversation history for later retrieval and memory extraction |

### Memory System

| ID | Requirement |
|----|-------------|
| FR-07 | The system MUST store and retrieve facts about the user (name, preferences, biographical details, etc.) |
| FR-08 | The system MUST store and retrieve the AI's own reflections and inner thoughts (not visible to user) |
| FR-09 | The system MUST maintain short-term context (recent actions, recent topics) that auto-expires |
| FR-10 | The system MUST support memory associations (linking related facts together) |
| FR-11 | The system MUST prioritize corrections stored in memory over conflicting short-term information |
| FR-12 | The system MUST support full-text search across stored memories |

### Personality & Emotion

| ID | Requirement |
|----|-------------|
| FR-13 | The system MUST maintain a set of personality trait weights that modulate conversational behavior |
| FR-14 | The system MUST maintain emotional state across 10 dimensions (surprise, confusion, joy, sadness, anticipation, trust, fear, anger, disgust, interest) with per-dimension decay rates |
| FR-15 | The system MUST decay emotional states over time when not stimulated |
| FR-16 | The system MUST adjust reply tone and content based on current emotional state |
| FR-17 | The system MUST allow personality weights to influence: reply temperature/creativity, assertiveness in corrections, frequency of proactive messages, curiosity-driven exploration |

### Review & Correction

| ID | Requirement |
|----|-------------|
| FR-18 | The system MUST internally review each AI reply for factual accuracy against stored memories |
| FR-19 | The system MUST internally review each AI reply for appropriate tone |
| FR-20 | The system MUST make a weighted decision about whether to issue a correction or remain silent |
| FR-21 | The system MUST accumulate silence weight for repeated unaddressed issues (making future corrections more likely) |
| FR-22 | The system MUST record "twisted" states where factual judgment and emotional judgment disagree (e.g., "logically I should correct this, but emotionally it feels wrong") |

### Proactive Behavior

| ID | Requirement |
|----|-------------|
| FR-23 | The system MUST compute a boredom level that increases over idle time |
| FR-24 | The system MUST trigger proactive conversation when boredom crosses a threshold AND interest in past topics is sufficient |
| FR-25 | The system MUST support autonomous information searching as a precursor to proactive messages |
| FR-26 | The system MUST defer actions that cannot be executed immediately and re-evaluate them later |

### Intent & Action System

| ID | Requirement |
|----|-------------|
| FR-27 | The system MUST extract actionable intents from the AI's internal reasoning ("I want to search X", "I should tell them about Y") |
| FR-28 | The system MUST evaluate extracted intents for relevance, value, and timing before executing |
| FR-29 | The system MUST support external information retrieval (web search, web page fetching) as part of intent execution |
| FR-30 | The system MUST enforce rate limits on external actions (no more than 5 searches per 60 seconds, minimum 2-second cooldown) |

### Multi-Modal Support

| ID | Requirement |
|----|-------------|
| FR-31 | The system MUST detect when a user shares an image (via file path or URL in the terminal) |
| FR-32 | The system MUST attempt to describe the image content when direct vision processing is unavailable (fallback chain) |
| FR-33 | The system MUST gracefully inform the user when image processing is completely unavailable |

### Safety

| ID | Requirement |
|----|-------------|
| FR-34 | The system MUST filter reply content for explicit self-harm or violence keywords and block such messages |
| FR-35 | The system MUST limit single reply segments to a maximum character length (500 characters) |
| FR-36 | The system MUST restrict external URL fetching to http/https protocols only |
| FR-37 | The system MUST enforce content size limits on fetched web pages (100 KB max) |

---

## Success Criteria

### Measurable Outcomes

| ID | Criterion | Target |
|----|-----------|--------|
| SC-01 | Time from user pressing Enter to first visible substantive reply character in terminal (excludes intentional `wait` pauses) | < 3 seconds |
| SC-02 | Memory recall accuracy for user facts mentioned in past 10 conversations | > 90% |
| SC-03 | Factual error correction rate (errors detected vs corrected when warranted) | > 80% |
| SC-04 | Proactive message relevance (user engages with proactive message vs ignores) | > 50% engagement rate |
| SC-05 | Emotional tone appropriateness (as rated by test users) | > 4/5 average rating |
| SC-06 | Conversation continuity across sessions (AI references past conversations unprompted) | > 70% of sessions |
| SC-07 | System stability (no crashes or hangs during 30-minute continuous conversation) | 100% |
| SC-08 | Graceful shutdown: normal quit preserves all memory state without data loss | 100% |
| SC-09 | External search rate limiting: no more than 5 searches per 60-second window | 100% compliance |
| SC-10 | Reply content safety filter: no blocked content categories pass through | 100% block rate |

> **Note on SC-04 through SC-06**: These are post-launch outcome metrics requiring user acceptance testing (UAT) with real users over multiple sessions. They are not verifiable through automated build-time tests. SC-04~06 will be validated during Phase 4 (Polish) manual QA sessions.

### Qualitative Goals

- The AI feels like "talking to a person with their own thoughts" rather than "querying a knowledge base"
- Corrections feel natural and well-timed, not pedantic or mechanical
- Emotional responses are calibrated — the AI doesn't overreact to minor cues or ignore major ones
- Silent judgments create a sense that the AI has hidden depth ("I wonder what it's not telling me")
- Proactive messages feel serendipitous, not scripted or repetitive

---

## Key Entities

| Entity | Description |
|--------|-------------|
| **User** | The human interacting with the CLI. Has a profile (inferred facts, preferences), emotional history, and conversation history. |
| **Memory Entry** | A unit of stored information. Has a namespace (user facts, AI reflections, short-term, subconscious), a key, a JSON value, salience score, decay curve, entity type, and topic tags. Can be linked to other entries via relations. |
| **Conversation Turn** | One round of user-message → AI-response. Contains the user message, the AI's internal reasoning chain, the final reply, and post-hoc review results. |
| **Emotion State** | A vector of numeric values across emotional dimensions (surprise, confusion, joy, sadness, anticipation, trust, etc.), tracked separately for different internal reasoning modules and subject to decay and cross-module contagion. |
| **Personality Profile** | Eight numeric weights (curiosity, sociability, playfulness, empathy, assertiveness, creativity, impulsiveness, loyalty) that modulate behavior. Has default values that can evolve over time. |
| **Inner Thought** | The AI's post-reply reflection — a private text containing self-assessment, emotional state, user mood reading, and any intentions for future action. Parsed structurally for downstream processing. |
| **Silence Accumulator** | A per-error-type counter that increments each time the AI chooses silence over correction for that error type. Non-linear triggering via fuzzy parameter sampling. |
| **Intent** | An extracted action desire from inner thoughts (e.g., "search for X", "tell user about Y"). Evaluated for relevance and timing before execution or deferral. |
| **Correction Nudge** | A high-priority directive stored in the subconscious memory layer, instructing the AI to fix a specific error in a follow-up message. |

---

## Scope Boundaries

### In Scope

- Terminal-based text chat interface
- Personality-driven conversation with memory, emotion, and judgment
- Self-review and self-correction within a conversation
- Proactive conversation initiation based on boredom/interest
- Web search and page fetching as auxiliary capabilities
- Image description via fallback chain (not native vision)
- Single-user, local-first operation

### Out of Scope

- Multi-user or group chat
- Graphical UI (GUI) — terminal only
- Voice input/output
- Plugin or extension system
- Cloud sync or multi-device memory sharing
- Real-time collaboration
- Integration with external chat platforms (Discord, Slack, etc.)
- Native image generation
- File system operations beyond the CLI's own data directory

---

## Assumptions

1. The user has a terminal emulator that supports basic ANSI formatting (colors, cursor movement)
2. The user has API access to at least one LLM provider compatible with OpenAI-style function calling
3. The user runs the CLI on their local machine (not a remote server)
4. Memory data is stored locally and is per-user (single user profile)
5. The initial personality weights are sensible defaults; user customization of personality is a future concern
6. Web search and page fetching require internet connectivity; graceful degradation when offline
7. The conversation language follows the user's language preferences
8. Conversation history and memory persist across CLI restarts on the same machine
9. The system operates in single-user mode; the default user identifier is "default" — multi-user support is out of scope per the single-user local-first design
