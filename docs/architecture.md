# Architecture

## Backend

- FastAPI exposes game actions and filtered views.
- SQLAlchemy stores games, players, assignments, role states, events, nominations, votes, AI memories, API usage, and results.
- The domain engine owns legality, role ability resolution, deaths, transformations, voting, and win conditions.
- `AIProvider` has `MockAIProvider` and `OpenAIProvider`; all model outputs use Pydantic schemas and are validated again by the domain layer.

## State Layers

- `TruthState`: complete grimoire and hidden data, backend domain only.
- `PublicState`: seats, alive/dead state, public events, nominations, votes, phase, result.
- `PlayerPrivateView`: apparent role, legal private info, that player's private chats, own memory, legal actions.

`TruthState.pending_action_prompts` stores unresolved human choices such as Imp night target, Chambermaid target pair, and Klutz death choice. `PlayerPrivateView.pending_actions` exposes only the prompt owned by the requesting player, while `PublicState` exposes only public phase/timer status. The submitted target ids are validated against the prompt before the domain engine continues the state transition.

## AI Storyteller Policy

BOTC has legal storyteller discretion, such as decoy choices and misinformation choices. The implemented `AIStorytellerPolicy` is constrained:

1. Domain engine computes legal options.
2. The storyteller policy chooses only from those legal options.
3. The choice is recorded as `STORYTELLER_INTERNAL`.
4. Normal frontend responses and AI contexts never receive those internal decisions.

This preserves the rule that AI may add style and balancing pressure but cannot invent legality, deaths, wins, or impossible information. A future LLM storyteller should use the same legal-option schema and validator.

## Online Table Cadence

AI autonomy is driven by `/api/games/{game_id}/ai-tick`. Each tick advances only a small piece of table behavior: a couple of public speakers, a couple of private chat decisions, one nomination attempt, or one rules transition. `PublicState` exposes only safe table status such as current AI status, active public player id, cooldown seconds, and discussion round count. It does not expose hidden roles, alignments, or storyteller-internal events.

`/api/games/{game_id}/ai-until-human` repeatedly performs bounded ticks until a human decision is required, such as voting, a human Klutz choice, budget pause, or game over. It is meant to feel closer to an online table flow without consuming mandatory human choices.

Day discussion uses seat-order cadence for primary AI speeches. Reactive replies may prioritize players named by the human, but otherwise preserve seat order. Public logs, nominations, votes, deaths, and AI context use one-based seat labels such as `3號 林鏡`, because BOTC table talk usually references seats as much as names.

If one or more players died during the previous night, day discussion order starts with those dead players before returning to normal seat order. This gives dead players a natural first chance to share final information or frame the day, and it also prevents living AI players from speculating about a dead player's information before that player has spoken.

## API Player Context

OpenAI-backed players receive a bounded player context rather than raw database rows. The context includes persona, table cadence, recent public events, only that player's private chats, public-safe player status, memory summary, visible role information, and an action contract for the current structured schema. Model outputs may include `memory_update`; the backend validates and clamps it before updating only that player's private AI memory.

The context builder also adds public-only reasoning aids: claim conflicts derived from that player's memory, recent pressure counts, nomination results, and vote patterns. These aids are summaries of public events and player-specific memory, not TruthState fields.

## Public Observation Memory

The engine updates each AI memory from public table events. Human and AI public role claims are parsed conservatively, nominations slightly adjust suspicion toward the nominee, and vote results nudge the table read based on whether the vote reached execution threshold. Each memory also keeps short public fact notes and vote notes so AI players can refer back to concrete seats and earlier outcomes instead of repeating generic pressure language. These updates are independent per AI and never use hidden role or alignment data.

AI speech prompts include a real-player protocol: respond to the current table moment, use seat numbers, volunteer legal role information when appropriate, ask one concrete question at a time, and avoid repeated abstract phrases such as "可驗證的點" unless attached to a specific seat and reason. Mock AI follows the same product direction with deterministic table-talk templates for offline tests.

Open nominations are serialized by the domain engine and by a per-game API action lock. If a nomination is already waiting for votes, new human or AI nominations are rejected until that vote resolves. This prevents simultaneous browser requests or an AI tick from creating overlapping nominations.

## AI Brain Pipeline

Before an AI speaks or acts, `refresh_ai_brain` builds a `TableNotebook` for that specific player. The notebook contains only public events, public claim memory, vote notes, the player's own private information, and that player's isolated `AIMemory`. It never reads other players' true roles, true alignments, `current_demon_id`, storyteller-internal events, ORM rows, or database records.

The notebook produces `CandidateScore` rows for legal table targets and a small set of `WorldHypothesis` objects. These are not rule adjudications; they are player-facing guesses used to make speech, nominations, votes, private chats, and night targets less template-like. Domain validation still decides whether an action is legal, and the domain engine still owns deaths, role results, transformations, and win conditions.

OpenAI prompts include this notebook as a bounded public-safe table read. Mock AI uses the same notebook directly, so offline play and API fallback follow the same reasoning surface instead of falling back to fixed phrases.

Candidate scores include whether each player has spoken today, how many public speeches they have made, and their latest public statement. AI players must not criticize the information content of a player who has not yet spoken today; they may only ask that player to speak. Information roles are prompted to share complete or partial information in their first day speech unless they have an explicit bluff or secrecy reason.

## Multiplayer Rooms

The app now supports a small real multiplayer table for 1 to 6 human seats, with remaining seats filled by AI. A game creator chooses the human count during setup, claims the first human seat, and receives a share link containing only `game_id`. Other browsers open the link, view the lobby, and claim an open human seat. The game remains in `SETUP` until every configured human seat is claimed; only the host seat can start the game.

Human seats are protected by per-seat session tokens. The token is stored in that browser's local storage and sent as `X-Player-Token` for private game views and actions. The token is returned only to the claiming player and is not included in public lobby state, other players' views, AI context, transcript exports before game over, or normal frontend responses. During `SETUP`, private views mask role cards and role-info events so players do not receive hidden setup information before the host starts.

Backend endpoints build a fresh player-scoped `GameView` for every request. Public lobby responses show seat order, player names, AI/human type, alive/dead state, and whether a human seat has been claimed; they do not include role, alignment, private chats, or hidden game state. Shared public events are safe to poll from multiple devices, while private events remain filtered by the requesting player.

Voting is server-authoritative. During a nomination, each eligible human vote is recorded independently and the table stays in `VOTING` until every required human has voted or abstained. Only then does the engine resolve remaining AI votes and finalize the nomination result. This prevents the first human voter from accidentally consuming the whole vote for the table.

Discussion cadence is also server-authoritative. `free` mode keeps the lightweight chat-like table flow. `ordered` mode stores `ordered_speaker_id`; only that player receives `public_speech`/`skip_speech` actions during day discussion, and AI ticks speak only when the current speaker is an AI. Human turns stop autonomous AI progress until the player speaks or skips.

Phase timing is server-authoritative as well. Night, day discussion, private chat, nominations, and voting each carry `phase_started_at`, `phase_deadline_at`, and `phase_remaining_seconds` in public state. Humans may mark a phase ready; when every human seat is ready the engine advances early. If a deadline expires, the engine resolves the phase with legal safe defaults such as abstaining from an unresolved vote or using the first valid pending night target.

This is intentionally a lightweight room model rather than a full public platform account system. It is suitable for a private friend table shared by URL. The next hardening step is scoped real-time push with SSE/WebSocket, room moderation, reconnect UI, formal accounts, invite codes, and anti-abuse controls.
