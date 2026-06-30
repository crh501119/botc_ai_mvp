import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

type Phase =
  | "SETUP"
  | "FIRST_NIGHT"
  | "DAWN"
  | "DAY_DISCUSSION"
  | "PRIVATE_CHAT"
  | "NOMINATIONS"
  | "VOTING"
  | "EXECUTION"
  | "NIGHT"
  | "GAME_OVER";

type EventView = {
  id: string;
  day: number;
  phase: Phase;
  scope: string;
  message: string;
  type: string;
  actor_id?: string | null;
  target_ids: string[];
  participants: string[];
};

type Player = {
  id: string;
  name: string;
  seat: number;
  is_human: boolean;
  alive: boolean;
  ghost_vote_available: boolean;
  nominated_today: boolean;
  was_nominated_today: boolean;
  claimed?: boolean;
};

type RoleView = {
  slug: string;
  zh_name: string;
  role_type: string;
  ability: string;
};

type TargetPrompt = {
  action: string;
  prompt: string;
  target_count: number;
  valid_target_ids: string[];
};

type Nomination = {
  id: string;
  day: number;
  nominator_id: string;
  nominee_id: string;
  reason: string;
  defense?: string | null;
  votes: number;
  threshold: number;
  eligible_for_execution: boolean;
  resolved: boolean;
};

type Usage = {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  estimated_usd?: number | null;
  budget_usd: number;
  remaining_usd?: number | null;
  by_player: Record<string, Record<string, number | null>>;
  by_purpose: Record<string, Record<string, number | null>>;
  estimate_note: string;
};

type GameView = {
  public: {
    game_id: string;
    day: number;
    phase: Phase;
    mock_ai: boolean;
    players: Player[];
    public_events: EventView[];
    nominations: Nomination[];
    last_night_deaths: string[];
    current_on_the_block?: string | null;
    current_high_votes: number;
    result?: { winner: "good" | "evil"; reason: string; day: number } | null;
    usage: Usage;
    ai_status: string;
    ai_active_player_id?: string | null;
    ai_cooldown_seconds: number;
    phase_started_at: string;
    phase_deadline_at?: string | null;
    phase_remaining_seconds?: number | null;
    host_player_id: string;
    discussion_mode: "free" | "ordered";
    discussion_rounds_today: number;
    current_speaker_id?: string | null;
    human_seats_ready: boolean;
  };
  private: {
    player_id: string;
    name: string;
    seat: number;
    alive: boolean;
    ghost_vote_available: boolean;
    role: RoleView;
    apparent_alignment: "good" | "evil";
    private_events: EventView[];
    private_chats: EventView[];
    legal_actions: string[];
    pending_actions: TargetPrompt[];
  };
  script: RoleView[];
  postgame?: {
    players: Array<{
      id: string;
      name: string;
      seat: number;
      true_role: string;
      true_role_zh: string;
      apparent_role_zh?: string | null;
      alignment: "good" | "evil";
      alive: boolean;
      death_cause?: string | null;
    }>;
    all_events: EventView[];
  } | null;
  dev_reveal?: unknown;
  session_token?: string | null;
};

type SavedGame = {
  id: string;
  day: number;
  phase: Phase;
  seed?: number | null;
  updated_at?: string | null;
  mock_ai: boolean;
};

type AppConfig = {
  dialogue_model: string;
  decision_model: string;
  mock_ai: boolean;
  openai_configured: boolean;
  budget_usd: number;
  dev_reveal: boolean;
  personas: Array<Record<string, unknown>>;
};

type LobbyView = {
  game_id: string;
  day: number;
  phase: Phase;
  mock_ai: boolean;
  discussion_mode: "free" | "ordered";
  host_player_id: string;
  human_seats_ready: boolean;
  open_human_seats: string[];
  players: Array<{
    id: string;
    name: string;
    seat: number;
    is_human: boolean;
    alive: boolean;
    claimed: boolean;
  }>;
};

type PlayerSessionState = {
  game_id: string;
  player_id: string;
  token: string;
};

const API = "";

async function api<T>(
  path: string,
  init?: RequestInit,
  playerToken?: string | null,
): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(playerToken ? { "X-Player-Token": playerToken } : {}),
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!response.ok) {
    const detail = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new Error(detail.detail ?? "操作失敗");
  }
  return (await response.json()) as T;
}

function sessionStorageKey(gameId: string): string {
  return `botc-player-session:${gameId}`;
}

function loadStoredSession(gameId: string): PlayerSessionState | null {
  const raw = window.localStorage.getItem(sessionStorageKey(gameId));
  if (!raw) return null;
  try {
    return JSON.parse(raw) as PlayerSessionState;
  } catch {
    return null;
  }
}

function storeSession(session: PlayerSessionState): void {
  window.localStorage.setItem(
    sessionStorageKey(session.game_id),
    JSON.stringify(session),
  );
}

function seatName(player: Pick<Player, "seat" | "name">): string {
  return `${player.seat + 1}號 ${player.name}`;
}

function playerName(view: GameView, id?: string | null): string {
  if (!id) return "無";
  const player = view.public.players.find((candidate) => candidate.id === id);
  return player ? seatName(player) : id;
}

function gameLink(gameId: string): string {
  const url = new URL(window.location.href);
  url.searchParams.set("game", gameId);
  return url.toString();
}

export default function App() {
  const [view, setView] = useState<GameView | null>(null);
  const [lobby, setLobby] = useState<LobbyView | null>(null);
  const [games, setGames] = useState<SavedGame[]>([]);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [playerSession, setPlayerSession] = useState<PlayerSessionState | null>(
    null,
  );
  const [humanName, setHumanName] = useState("旅人");
  const [joinName, setJoinName] = useState("玩家");
  const [humanCount, setHumanCount] = useState("1");
  const [discussionMode, setDiscussionMode] = useState<"free" | "ordered">(
    "ordered",
  );
  const [shuffleSeats, setShuffleSeats] = useState(true);
  const [nightSeconds, setNightSeconds] = useState("90");
  const [dayDiscussionSeconds, setDayDiscussionSeconds] = useState("240");
  const [privateChatSeconds, setPrivateChatSeconds] = useState("180");
  const [nominationsSeconds, setNominationsSeconds] = useState("180");
  const [votingSeconds, setVotingSeconds] = useState("60");
  const [seed, setSeed] = useState("");
  const [budget, setBudget] = useState("1.00");
  const [mockAi, setMockAi] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function refreshGames() {
    setGames(await api<SavedGame[]>("/api/games"));
  }

  async function openGame(gameId: string) {
    const stored = loadStoredSession(gameId);
    if (stored) {
      const next = await api<GameView>(
        `/api/games/${gameId}?player_id=${encodeURIComponent(stored.player_id)}`,
        undefined,
        stored.token,
      );
      setPlayerSession(stored);
      setView(next);
      setLobby(null);
      window.history.replaceState(null, "", `?game=${gameId}`);
      return;
    }
    const nextLobby = await api<LobbyView>(`/api/games/${gameId}/lobby`);
    setView(null);
    setLobby(nextLobby);
    window.history.replaceState(null, "", `?game=${gameId}`);
  }

  async function run<T>(
    operation: () => Promise<T>,
    onOk?: (value: T) => void,
  ) {
    setBusy(true);
    setError("");
    try {
      const value = await operation();
      onOk?.(value);
      await refreshGames();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失敗");
    } finally {
      setBusy(false);
    }
  }

  async function createGame(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await run(
      () =>
        api<GameView>("/api/games", {
          method: "POST",
          body: JSON.stringify({
            human_name: humanName,
            human_count: Number(humanCount),
            discussion_mode: discussionMode,
            shuffle_seats_on_start: shuffleSeats,
            night_seconds: Number(nightSeconds),
            day_discussion_seconds: Number(dayDiscussionSeconds),
            private_chat_seconds: Number(privateChatSeconds),
            nominations_seconds: Number(nominationsSeconds),
            voting_seconds: Number(votingSeconds),
            seed: seed.trim() ? Number(seed) : null,
            budget_usd: Number(budget),
            mock_ai: mockAi,
          }),
        }),
      (next) => {
        const token = next.session_token;
        if (!token) throw new Error("後端沒有回傳玩家憑證。");
        const nextSession = {
          game_id: next.public.game_id,
          player_id: next.private.player_id,
          token,
        };
        storeSession(nextSession);
        setPlayerSession(nextSession);
        setView(next);
        setLobby(null);
        window.history.replaceState(null, "", `?game=${next.public.game_id}`);
      },
    );
  }

  async function joinSeat(playerId: string) {
    if (!lobby) return;
    await run(
      () =>
        api<GameView>(`/api/games/${lobby.game_id}/join`, {
          method: "POST",
          body: JSON.stringify({ player_id: playerId, player_name: joinName }),
        }),
      (next) => {
        const token = next.session_token;
        if (!token) throw new Error("後端沒有回傳玩家憑證。");
        const nextSession = {
          game_id: next.public.game_id,
          player_id: next.private.player_id,
          token,
        };
        storeSession(nextSession);
        setPlayerSession(nextSession);
        setView(next);
        setLobby(null);
      },
    );
  }

  useEffect(() => {
    let cancelled = false;
    async function loadInitialState() {
      const nextConfig = await api<AppConfig>("/api/config");
      if (cancelled) return;
      setConfig(nextConfig);
      setBudget(String(nextConfig.budget_usd));
      setMockAi(nextConfig.mock_ai || !nextConfig.openai_configured);
      await refreshGames();
      const gameId = new URL(window.location.href).searchParams.get("game");
      if (gameId) await openGame(gameId);
    }
    loadInitialState().catch((err) => {
      setError(err instanceof Error ? err.message : "載入失敗");
      refreshGames().catch(() => undefined);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (view && playerSession) {
    return (
      <GameScreen
        view={view}
        playerSession={playerSession}
        busy={busy}
        error={error}
        setView={setView}
        run={run}
        leave={() => {
          setView(null);
          setLobby(null);
          setPlayerSession(null);
          window.history.replaceState(null, "", window.location.pathname);
        }}
      />
    );
  }

  return (
    <main className="app">
      <section className="setup">
        <div>
          <p className="eyebrow">非官方 fan project</p>
          <h1>六人血染鐘樓 AI</h1>
          <p className="intro">
            建立多人房間後，把網址傳給朋友。每位真人加入自己的座位，其他座位由
            AI 操作。
          </p>
          {lobby ? (
            <section className="panel join-panel">
              <h2>加入房間</h2>
              <p className="small">分享網址：{gameLink(lobby.game_id)}</p>
              <label>
                你的名稱
                <input
                  value={joinName}
                  onChange={(event) => setJoinName(event.target.value)}
                />
              </label>
              <div className="seat-list">
                {lobby.players.map((player) => (
                  <button
                    key={player.id}
                    disabled={!player.is_human || player.claimed || busy}
                    onClick={() => joinSeat(player.id)}
                  >
                    {player.seat + 1}號 {player.name}
                    {player.is_human
                      ? player.claimed
                        ? " · 已加入"
                        : " · 加入"
                      : " · AI"}
                  </button>
                ))}
              </div>
            </section>
          ) : null}
        </div>
        <form
          onSubmit={createGame}
          className="setup-form"
          aria-label="setup form"
        >
          <label>
            你的玩家名稱
            <input
              value={humanName}
              onChange={(event) => setHumanName(event.target.value)}
            />
          </label>
          <label>
            真人人數
            <select
              value={humanCount}
              onChange={(event) => setHumanCount(event.target.value)}
            >
              {[1, 2, 3, 4, 5, 6].map((count) => (
                <option key={count} value={count}>
                  {count} 真人 / {6 - count} AI
                </option>
              ))}
            </select>
          </label>
          <label>
            白天發言模式
            <select
              value={discussionMode}
              onChange={(event) =>
                setDiscussionMode(event.target.value as "free" | "ordered")
              }
            >
              <option value="ordered">順序發言：每人一個回合</option>
              <option value="free">自由發言：像聊天室</option>
            </select>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={shuffleSeats}
              onChange={(event) => setShuffleSeats(event.target.checked)}
            />
            開始時隨機座位
          </label>
          <fieldset className="timer-grid">
            <legend>階段時間（秒）</legend>
            <label>
              夜晚
              <input
                value={nightSeconds}
                onChange={(event) => setNightSeconds(event.target.value)}
              />
            </label>
            <label>
              白天討論
              <input
                value={dayDiscussionSeconds}
                onChange={(event) =>
                  setDayDiscussionSeconds(event.target.value)
                }
              />
            </label>
            <label>
              私聊
              <input
                value={privateChatSeconds}
                onChange={(event) => setPrivateChatSeconds(event.target.value)}
              />
            </label>
            <label>
              提名
              <input
                value={nominationsSeconds}
                onChange={(event) => setNominationsSeconds(event.target.value)}
              />
            </label>
            <label>
              投票
              <input
                value={votingSeconds}
                onChange={(event) => setVotingSeconds(event.target.value)}
              />
            </label>
          </fieldset>
          <label>
            Random seed（留空即隨機）
            <input
              value={seed}
              onChange={(event) => setSeed(event.target.value)}
            />
          </label>
          <label>
            本局預算 USD
            <input
              value={budget}
              onChange={(event) => setBudget(event.target.value)}
            />
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={mockAi}
              onChange={(event) => setMockAi(event.target.checked)}
            />
            Mock AI
          </label>
          <p className="mode-hint">
            {mockAi
              ? "新局使用 Mock AI，適合測試規則。"
              : "新局使用 OpenAI API，AI 以隔離資訊的玩家視角回覆。"}
            {!mockAi && config && !config.openai_configured
              ? " 後端未偵測到 API key，實際仍會退回 Mock。"
              : ""}
          </p>
          <button disabled={busy}>新遊戲</button>
        </form>
        <section className="saved">
          <h2>載入或加入舊遊戲</h2>
          {games.length === 0 ? <p>目前沒有保存的遊戲。</p> : null}
          {games.map((game) => (
            <button
              key={game.id}
              className="saved-row"
              onClick={() => run(() => openGame(game.id))}
            >
              Day {game.day} / {game.phase} / seed {game.seed ?? "無"}
              {game.mock_ai ? " / Mock" : " / OpenAI"}
            </button>
          ))}
        </section>
        {error ? (
          <p role="alert" className="error">
            {error}
          </p>
        ) : null}
      </section>
    </main>
  );
}

export function GameScreen({
  view,
  playerSession,
  busy,
  error,
  setView,
  run,
  leave,
}: {
  view: GameView;
  playerSession?: PlayerSessionState;
  busy: boolean;
  error: string;
  setView: (view: GameView) => void;
  run: <T>(
    operation: () => Promise<T>,
    onOk?: (value: T) => void,
  ) => Promise<void>;
  leave: () => void;
}) {
  const effectiveSession = playerSession ?? {
    game_id: view.public.game_id,
    player_id: view.private.player_id,
    token: "test-token",
  };
  const [speech, setSpeech] = useState("");
  const [chatTarget, setChatTarget] = useState(
    view.public.players.find((player) => player.id !== view.private.player_id)
      ?.id ?? "",
  );
  const [chatMessage, setChatMessage] = useState("");
  const [nominee, setNominee] = useState(
    view.public.players.find(
      (player) => player.alive && player.id !== view.private.player_id,
    )?.id ?? "",
  );
  const [nightTarget, setNightTarget] = useState("");
  const [chambermaidTargets, setChambermaidTargets] = useState<string[]>([]);
  const [klutzTarget, setKlutzTarget] = useState("");
  const [reason, setReason] = useState("我想測試這個說法。");
  const [artistQuestion, setArtistQuestion] = useState("");
  const [budget, setBudget] = useState(String(view.public.usage.budget_usd));
  const [nowMs, setNowMs] = useState(Date.now());
  const [aiTickStatus, setAiTickStatus] =
    useState("AI 會依本局冷卻時間一位一位發言。");
  const shareLink = gameLink(view.public.game_id);

  const pendingNomination = useMemo(
    () =>
      view.public.nominations.find(
        (item) => item.day === view.public.day && !item.resolved,
      ),
    [view.public.day, view.public.nominations],
  );
  const chatTargets = view.public.players.filter(
    (player) => player.id !== view.private.player_id,
  );
  const nominationTargets = view.public.players.filter(
    (player) => player.alive && player.id !== view.private.player_id,
  );
  const activeAiName = playerName(view, view.public.ai_active_player_id);
  const tableStatus = view.public.ai_status || aiTickStatus;
  const currentSpeakerName = playerName(view, view.public.current_speaker_id);
  const isDeveloper = Boolean(view.dev_reveal);
  const canStartGame = view.private.legal_actions.includes("start_game");
  const canPublicSpeak = view.private.legal_actions.includes("public_speech");
  const canSkipSpeech = view.private.legal_actions.includes("skip_speech");
  const canPrivateChat = view.private.legal_actions.includes("private_chat");
  const canVote = view.private.legal_actions.includes("vote_yes");
  const canNominate = view.private.legal_actions.includes("nominate");
  const canPhaseReady = view.private.legal_actions.includes("phase_ready");
  const canKlutzChoose = view.private.legal_actions.includes("klutz_choose");
  const nightPrompt = view.private.pending_actions.find(
    (prompt) => prompt.action === "night_target",
  );
  const chambermaidPrompt = view.private.pending_actions.find(
    (prompt) => prompt.action === "chambermaid_choice",
  );
  const klutzTargets = useMemo(
    () => view.public.players.filter((player) => player.alive),
    [view.public.players],
  );
  const phaseRemainingSeconds = view.public.phase_deadline_at
    ? Math.max(
        0,
        Math.floor(
          (new Date(view.public.phase_deadline_at).getTime() - nowMs) / 1000,
        ),
      )
    : view.public.phase_remaining_seconds;
  const aiTickIntervalMs = Math.max(
    5000,
    view.public.ai_cooldown_seconds * 1000,
  );

  const pathWithPlayer = useCallback(
    (path: string): string => {
      const separator = path.includes("?") ? "&" : "?";
      return `${path}${separator}player_id=${encodeURIComponent(effectiveSession.player_id)}`;
    },
    [effectiveSession.player_id],
  );

  async function action(path: string, body?: unknown, queryPlayer = false) {
    await run(
      () =>
        api<GameView>(
          queryPlayer ? pathWithPlayer(path) : path,
          {
            method: "POST",
            body: body ? JSON.stringify(body) : undefined,
          },
          effectiveSession.token,
        ),
      (next) => {
        setView(next);
        if (next.session_token) {
          storeSession({ ...effectiveSession, token: next.session_token });
        }
      },
    );
  }

  useEffect(() => {
    if (view.public.ai_status) setAiTickStatus(view.public.ai_status);
  }, [view.public.ai_status]);

  useEffect(() => {
    const interval = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (nightPrompt && !nightPrompt.valid_target_ids.includes(nightTarget)) {
      setNightTarget(nightPrompt.valid_target_ids[0] ?? "");
    }
  }, [nightPrompt, nightTarget]);

  useEffect(() => {
    if (chambermaidPrompt) {
      setChambermaidTargets((current) =>
        current.filter((targetId) =>
          chambermaidPrompt.valid_target_ids.includes(targetId),
        ),
      );
    }
  }, [chambermaidPrompt]);

  useEffect(() => {
    if (!klutzTargets.some((player) => player.id === klutzTarget)) {
      setKlutzTarget(klutzTargets[0]?.id ?? "");
    }
  }, [klutzTarget, klutzTargets]);

  useEffect(() => {
    if (
      busy ||
      view.public.result ||
      view.public.phase === "SETUP" ||
      (view.public.discussion_mode === "ordered" &&
        view.public.current_speaker_id === view.private.player_id)
    )
      return undefined;
    let cancelled = false;
    let inFlight = false;
    const interval = window.setInterval(async () => {
      if (inFlight) return;
      if (
        view.public.phase === "VOTING" ||
        view.public.phase === "GAME_OVER" ||
        view.public.phase === "SETUP"
      ) {
        setAiTickStatus("AI 已停下，等待真人必要操作。");
        return;
      }
      inFlight = true;
      setAiTickStatus("AI 思考中，正在嘗試自主行動...");
      try {
        const next = await api<GameView>(
          pathWithPlayer(`/api/games/${view.public.game_id}/ai-tick`),
          { method: "POST" },
          effectiveSession.token,
        );
        if (!cancelled) {
          setView(next);
          setAiTickStatus(
            next.public.phase === "VOTING"
              ? "AI 已提出提名，等待真人投票。"
              : "AI 已完成一步，自主行動冷卻中。",
          );
        }
      } catch {
        if (!cancelled)
          setAiTickStatus("AI 自主行動暫停；你仍可手動操作或稍後重試。");
      } finally {
        inFlight = false;
      }
    }, aiTickIntervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [
    busy,
    effectiveSession.player_id,
    effectiveSession.token,
    setView,
    view.public.game_id,
    view.public.current_speaker_id,
    view.public.discussion_mode,
    view.public.phase,
    view.public.result,
    view.private.player_id,
    aiTickIntervalMs,
    pathWithPlayer,
  ]);

  return (
    <main className="game">
      <header className="topbar">
        <div>
          <strong>第 {view.public.day} 天</strong>
          <span>{phaseLabel(view.public.phase)}</span>
          <small>{tableStatus}</small>
        </div>
        <div className="top-actions">
          {view.public.phase === "SETUP" ? (
            <button
              disabled={busy || !canStartGame}
              onClick={() =>
                action(
                  `/api/games/${view.public.game_id}/start`,
                  undefined,
                  true,
                )
              }
            >
              {view.public.human_seats_ready ? "開始遊戲" : "等待真人入座"}
            </button>
          ) : (
            <button
              disabled={busy}
              onClick={() =>
                action(
                  `/api/games/${view.public.game_id}/advance`,
                  undefined,
                  true,
                )
              }
            >
              推進階段
            </button>
          )}
          {isDeveloper ? (
            <>
              <button
                disabled={busy}
                onClick={() =>
                  action(
                    `/api/games/${view.public.game_id}/ai-tick`,
                    undefined,
                    true,
                  )
                }
              >
                AI 自主一步
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  action(
                    `/api/games/${view.public.game_id}/ai-until-human`,
                    undefined,
                    true,
                  )
                }
              >
                跑到需要我決策
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  action(
                    `/api/games/${view.public.game_id}/auto-play`,
                    undefined,
                    true,
                  )
                }
              >
                Mock 跑完整局
              </button>
            </>
          ) : null}
          {canPhaseReady ? (
            <button
              aria-label="phase ready"
              disabled={busy}
              onClick={() =>
                action(`/api/games/${view.public.game_id}/phase-ready`, {
                  player_id: view.private.player_id,
                })
              }
            >
              我已完成/跳過
            </button>
          ) : null}
          <button onClick={leave}>儲存並離開</button>
        </div>
      </header>

      {error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : null}
      {busy ? <p className="thinking">AI 或規則引擎正在處理...</p> : null}

      <section className="live-strip" aria-label="ai table status">
        <div>
          <span>你的座位</span>
          <strong>
            {seatName({ seat: view.private.seat, name: view.private.name })}
          </strong>
        </div>
        <div>
          <span>正在行動</span>
          <strong>
            {view.public.ai_active_player_id ? activeAiName : "等待中"}
          </strong>
        </div>
        <div>
          <span>AI 冷卻</span>
          <strong>{view.public.ai_cooldown_seconds} 秒</strong>
        </div>
        <div>
          <span>階段倒數</span>
          <strong>
            {phaseRemainingSeconds == null
              ? "不限"
              : `${phaseRemainingSeconds} 秒`}
          </strong>
        </div>
        <div>
          <span>發言模式</span>
          <strong>
            {view.public.discussion_mode === "ordered" ? "順序" : "自由"}
          </strong>
        </div>
        <div>
          <span>目前發言</span>
          <strong>
            {view.public.current_speaker_id ? currentSpeakerName : "未指定"}
          </strong>
        </div>
        <div>
          <span>AI 模式</span>
          <strong>{view.public.mock_ai ? "Mock" : "OpenAI"}</strong>
        </div>
        <div>
          <span>分享</span>
          <strong className="share-link">{shareLink}</strong>
        </div>
      </section>

      {view.public.phase === "SETUP" ? (
        <section className="panel table-gate">
          <h2>等待所有真人入座</h2>
          <p>
            {view.public.human_seats_ready
              ? "真人座位已滿，房主可以開始遊戲。開始後才會揭露各自角色。"
              : "把分享連結傳給朋友；所有真人座位認領後才能開始。"}
          </p>
          <div className="seat-list">
            {view.public.players.map((player) => (
              <span key={player.id} className="seat-chip">
                {player.seat + 1}號 {player.name}{" "}
                {player.is_human
                  ? player.claimed
                    ? "已入座"
                    : "等待加入"
                  : "AI"}
              </span>
            ))}
          </div>
        </section>
      ) : null}

      <section className="layout">
        <section className="left-col">
          <SeatCircle view={view} />
          <RoleCard view={view} />
          <UsagePanel
            usage={view.public.usage}
            budget={budget}
            setBudget={setBudget}
            update={() =>
              action(
                `/api/games/${view.public.game_id}/budget`,
                { budget_usd: Number(budget) },
                true,
              )
            }
          />
        </section>

        <section className="middle-col">
          <section className="panel">
            <h2>公開聊天</h2>
            <div className="log" aria-label="public chat">
              {view.public.public_events.map((event) => (
                <p key={event.id}>{event.message}</p>
              ))}
            </div>
            <form
              className="inline-form"
              onSubmit={(event) => {
                event.preventDefault();
                if (!canPublicSpeak) return;
                action(`/api/games/${view.public.game_id}/speech`, {
                  player_id: view.private.player_id,
                  speech,
                }).then(() => setSpeech(""));
              }}
            >
              <input
                aria-label="公開發言"
                value={speech}
                onChange={(event) => setSpeech(event.target.value)}
                placeholder={
                  canPublicSpeak
                    ? "輪到你了，公開發言"
                    : view.public.discussion_mode === "ordered"
                      ? "等待你的發言回合"
                      : "目前階段不能公頻發言"
                }
                disabled={!canPublicSpeak}
              />
              <button disabled={busy || !canPublicSpeak || !speech.trim()}>
                送出
              </button>
              {canSkipSpeech ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    action(
                      `/api/games/${view.public.game_id}/speech-skip`,
                      undefined,
                      true,
                    )
                  }
                >
                  略過
                </button>
              ) : null}
            </form>
          </section>

          <section className="panel">
            <h2>私聊</h2>
            <div className="log private" aria-label="private chat">
              {view.private.private_chats.length === 0 ? (
                <p>尚無你參與的私聊。</p>
              ) : null}
              {view.private.private_chats.map((event) => (
                <p key={event.id}>{event.message}</p>
              ))}
            </div>
            <form
              className="inline-form"
              onSubmit={(event) => {
                event.preventDefault();
                if (!canPrivateChat) return;
                action(`/api/games/${view.public.game_id}/private-chat`, {
                  from_id: view.private.player_id,
                  to_id: chatTarget,
                  message: chatMessage,
                }).then(() => setChatMessage(""));
              }}
            >
              <select
                aria-label="私聊對象"
                value={chatTarget}
                onChange={(event) => setChatTarget(event.target.value)}
                disabled={!canPrivateChat}
              >
                {chatTargets.map((player) => (
                  <option key={player.id} value={player.id}>
                    {seatName(player)}
                  </option>
                ))}
              </select>
              <input
                aria-label="私聊訊息"
                value={chatMessage}
                onChange={(event) => setChatMessage(event.target.value)}
                placeholder={canPrivateChat ? "私聊訊息" : "目前階段不能私聊"}
                disabled={!canPrivateChat}
              />
              <button disabled={busy || !canPrivateChat || !chatMessage.trim()}>
                送出
              </button>
            </form>
          </section>
        </section>

        <section className="right-col">
          {(nightPrompt || chambermaidPrompt || canKlutzChoose) && (
            <section className="panel">
              <h2>目標選擇</h2>
              {nightPrompt ? (
                <form
                  className="stack-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    action(`/api/games/${view.public.game_id}/night-target`, {
                      player_id: view.private.player_id,
                      target_id: nightTarget,
                    });
                  }}
                >
                  <p>{nightPrompt.prompt}</p>
                  <select
                    aria-label="night target"
                    value={nightTarget}
                    onChange={(event) => setNightTarget(event.target.value)}
                  >
                    {nightPrompt.valid_target_ids.map((targetId) => {
                      const player = view.public.players.find(
                        (candidate) => candidate.id === targetId,
                      );
                      return (
                        <option key={targetId} value={targetId}>
                          {player ? seatName(player) : targetId}
                        </option>
                      );
                    })}
                  </select>
                  <button disabled={busy || !nightTarget}>送出目標</button>
                </form>
              ) : null}

              {chambermaidPrompt ? (
                <form
                  className="stack-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    action(
                      `/api/games/${view.public.game_id}/chambermaid-choice`,
                      {
                        player_id: view.private.player_id,
                        target_ids: chambermaidTargets,
                      },
                    );
                  }}
                >
                  <p>{chambermaidPrompt.prompt}</p>
                  <div className="choice-grid">
                    {chambermaidPrompt.valid_target_ids.map((targetId) => {
                      const player = view.public.players.find(
                        (candidate) => candidate.id === targetId,
                      );
                      const checked = chambermaidTargets.includes(targetId);
                      return (
                        <label key={targetId} className="check">
                          <input
                            aria-label={`chambermaid target ${targetId}`}
                            type="checkbox"
                            checked={checked}
                            onChange={(event) => {
                              setChambermaidTargets((current) => {
                                if (event.target.checked) {
                                  return current.length >= 2
                                    ? current
                                    : [...current, targetId];
                                }
                                return current.filter(
                                  (item) => item !== targetId,
                                );
                              });
                            }}
                          />
                          {player ? seatName(player) : targetId}
                        </label>
                      );
                    })}
                  </div>
                  <button disabled={busy || chambermaidTargets.length !== 2}>
                    送出兩名目標
                  </button>
                </form>
              ) : null}

              {canKlutzChoose ? (
                <form
                  className="stack-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    action(`/api/games/${view.public.game_id}/klutz`, {
                      player_id: view.private.player_id,
                      target_id: klutzTarget,
                    });
                  }}
                >
                  <p>你是笨蛋，請選擇一名存活玩家。</p>
                  <select
                    aria-label="klutz target"
                    value={klutzTarget}
                    onChange={(event) => setKlutzTarget(event.target.value)}
                  >
                    {klutzTargets.map((player) => (
                      <option key={player.id} value={player.id}>
                        {seatName(player)}
                      </option>
                    ))}
                  </select>
                  <button disabled={busy || !klutzTarget}>送出笨蛋選擇</button>
                </form>
              ) : null}
            </section>
          )}

          <section className="panel">
            <h2>提名與投票</h2>
            {pendingNomination ? (
              <p>
                正在投票：{playerName(view, pendingNomination.nominee_id)} 被{" "}
                {playerName(view, pendingNomination.nominator_id)} 提名。
              </p>
            ) : view.public.current_on_the_block ? (
              <p>
                目前上台：{playerName(view, view.public.current_on_the_block)}，
                {view.public.current_high_votes} 票。
              </p>
            ) : (
              <p>目前沒有正在投票的提名，也沒有人達到處決門檻。</p>
            )}
            {pendingNomination ? (
              <div className="vote-box">
                <p>
                  等待投票：{playerName(view, pendingNomination.nominee_id)}，
                  理由：{pendingNomination.reason}
                </p>
                <button
                  disabled={busy || !canVote}
                  onClick={() =>
                    action(`/api/games/${view.public.game_id}/vote`, {
                      player_id: view.private.player_id,
                      vote: true,
                    })
                  }
                >
                  贊成
                </button>
                <button
                  disabled={busy || !canVote}
                  onClick={() =>
                    action(`/api/games/${view.public.game_id}/vote`, {
                      player_id: view.private.player_id,
                      vote: false,
                    })
                  }
                >
                  不投票
                </button>
              </div>
            ) : (
              <form
                className="stack-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  action(`/api/games/${view.public.game_id}/nominations`, {
                    nominator_id: view.private.player_id,
                    nominee_id: nominee,
                    reason,
                  });
                }}
              >
                <label>
                  提名對象
                  <select
                    value={nominee}
                    onChange={(event) => setNominee(event.target.value)}
                  >
                    {nominationTargets.map((player) => (
                      <option key={player.id} value={player.id}>
                        {seatName(player)}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  理由
                  <input
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                  />
                </label>
                <button disabled={busy || !canNominate || !nominee}>
                  提名
                </button>
              </form>
            )}
            <p className="small">
              Ghost vote：
              {view.private.ghost_vote_available ? "尚可使用" : "已用或不適用"}
            </p>
          </section>

          <section className="panel">
            <h2>藝術家問題</h2>
            <form
              className="inline-form"
              onSubmit={(event) => {
                event.preventDefault();
                run(
                  () =>
                    api<GameView>(
                      `/api/games/${view.public.game_id}/artist`,
                      {
                        method: "POST",
                        body: JSON.stringify({
                          player_id: view.private.player_id,
                          question: artistQuestion,
                        }),
                      },
                      effectiveSession.token,
                    ),
                  (next) => setView(next),
                ).then(() => setArtistQuestion(""));
              }}
            >
              <input
                aria-label="Artist question"
                value={artistQuestion}
                onChange={(event) => setArtistQuestion(event.target.value)}
                placeholder="例如：林鏡是否為惡魔？"
              />
              <button
                disabled={
                  !view.private.legal_actions.includes("artist_question")
                }
              >
                詢問
              </button>
            </form>
          </section>

          <ScriptSheet roles={view.script} />
        </section>
      </section>

      {view.postgame ? <Postgame view={view} /> : null}
      <p className="notice">
        非官方 fan project；未使用官方美術、圖示、logo 或字型。
      </p>
    </main>
  );
}

function SeatCircle({ view }: { view: GameView }) {
  return (
    <section className="panel seat-panel">
      <h2>座位</h2>
      <div className="seats">
        {view.public.players.map((player) => (
          <div
            key={player.id}
            className={`seat seat-${player.seat} ${
              player.id === view.private.player_id ? "me" : ""
            } ${player.alive ? "alive" : "dead"}`}
          >
            <span className="seat-number">{player.seat + 1}號</span>
            <strong>{player.name}</strong>
            <span>
              {player.is_human ? (player.claimed ? "真人" : "待加入") : "AI"}
            </span>
            <span>{player.alive ? "存活" : "死亡"}</span>
            {!player.alive ? (
              <span>
                {player.ghost_vote_available ? "鬼票未用" : "鬼票已用"}
              </span>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}

function RoleCard({ view }: { view: GameView }) {
  return (
    <section className="panel role-card">
      <h2>你的角色</h2>
      <p className="role-name">{view.private.role.zh_name}</p>
      <p>{view.private.role.role_type}</p>
      <p>{view.private.role.ability}</p>
      <p>
        你所知陣營：
        {view.private.apparent_alignment === "good" ? "善良" : "邪惡"}
      </p>
      <div className="private-info">
        {view.private.private_events.map((event) => (
          <p key={event.id}>{event.message}</p>
        ))}
      </div>
    </section>
  );
}

function UsagePanel({
  usage,
  budget,
  setBudget,
  update,
}: {
  usage: Usage;
  budget: string;
  setBudget: (value: string) => void;
  update: () => void;
}) {
  return (
    <section className="panel">
      <h2>API 用量</h2>
      <p>呼叫：{usage.calls}</p>
      <p>
        Token：in {usage.input_tokens} / out {usage.output_tokens} / reasoning{" "}
        {usage.reasoning_tokens}
      </p>
      <p>
        估算費用：
        {usage.estimated_usd == null ? "價格未知" : `$${usage.estimated_usd}`}
      </p>
      <p>
        剩餘：
        {usage.remaining_usd == null ? "無法估算" : `$${usage.remaining_usd}`}
      </p>
      <p className="small">{usage.estimate_note}</p>
      <div className="inline-form">
        <input
          aria-label="budget"
          value={budget}
          onChange={(event) => setBudget(event.target.value)}
        />
        <button onClick={update}>更新上限</button>
      </div>
    </section>
  );
}

function ScriptSheet({ roles }: { roles: RoleView[] }) {
  return (
    <section className="panel script">
      <h2>劇本角色表</h2>
      {roles.map((role) => (
        <details key={role.slug}>
          <summary>
            {role.zh_name} · {role.role_type}
          </summary>
          <p>{role.ability}</p>
        </details>
      ))}
    </section>
  );
}

function Postgame({ view }: { view: GameView }) {
  if (!view.postgame || !view.public.result) return null;
  return (
    <section className="postgame">
      <h2>遊戲結束</h2>
      <p>
        {view.public.result.winner === "good" ? "善良" : "邪惡"}勝利：
        {view.public.result.reason}
      </p>
      <div className="reveal-grid">
        {view.postgame.players.map((player) => (
          <div key={player.id} className="reveal">
            <strong>
              {player.seat + 1}號 {player.name}
            </strong>
            <span>{player.true_role_zh}</span>
            {player.apparent_role_zh ? (
              <span>看到：{player.apparent_role_zh}</span>
            ) : null}
            <span>{player.alignment === "good" ? "善良" : "邪惡"}</span>
          </div>
        ))}
      </div>
      <div className="export-links">
        <a href={`/api/games/${view.public.game_id}/export.json`}>匯出 JSON</a>
        <a href={`/api/games/${view.public.game_id}/export.md`}>
          匯出 Markdown
        </a>
      </div>
    </section>
  );
}

function phaseLabel(phase: Phase): string {
  return {
    SETUP: "設置",
    FIRST_NIGHT: "第一夜",
    DAWN: "黎明",
    DAY_DISCUSSION: "公開討論",
    PRIVATE_CHAT: "私聊",
    NOMINATIONS: "提名",
    VOTING: "投票",
    EXECUTION: "處決",
    NIGHT: "夜晚",
    GAME_OVER: "遊戲結束",
  }[phase];
}
