import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";
import App, { GameScreen } from "../App";
import { makeGameView } from "./fixtures";

describe("setup", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  test("setup form creates a game", async () => {
    const view = makeGameView();
    const fetchMock = vi
      .fn()
      .mockImplementation((path: string, init?: RequestInit) => {
        if (path === "/api/config") {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              dialogue_model: "gpt-test-dialogue",
              decision_model: "gpt-test-decision",
              mock_ai: false,
              openai_configured: true,
              budget_usd: 1,
              dev_reveal: false,
              personas: [],
            }),
          });
        }
        if (path === "/api/games" && init?.method === "POST") {
          return Promise.resolve({ ok: true, json: async () => view });
        }
        if (path === "/api/games") {
          return Promise.resolve({ ok: true, json: async () => [] });
        }
        return Promise.resolve({ ok: true, json: async () => view });
      });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    expect(await screen.findByText(/OpenAI API/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "新遊戲" }));
    expect(await screen.findByText("你的角色")).toBeInTheDocument();
    const createCall = fetchMock.mock.calls.find(
      ([path, init]) => path === "/api/games" && init?.method === "POST",
    );
    expect(createCall).toBeDefined();
    const body = JSON.parse(createCall?.[1]?.body as string);
    expect(body.seed).toBeNull();
    expect(body.human_count).toBe(1);
    expect(body.mock_ai).toBe(false);
  });
});

describe("game screen", () => {
  const run = async <T,>(
    operation: () => Promise<T>,
    onOk?: (value: T) => void,
  ) => {
    const value = await operation();
    onOk?.(value);
  };
  const setView = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => makeGameView() }),
    );
    setView.mockClear();
  });

  test("role card only shows own private information", () => {
    render(
      <GameScreen
        view={makeGameView()}
        busy={false}
        error=""
        setView={setView}
        run={run}
        leave={vi.fn()}
      />,
    );
    const roleCard = screen.getByText("你的角色").closest("section");
    expect(roleCard).not.toBeNull();
    expect(
      within(roleCard as HTMLElement).getByText("藝術家"),
    ).toBeInTheDocument();
    expect(
      within(roleCard as HTMLElement).queryByText("小惡魔"),
    ).not.toBeInTheDocument();
  });

  test("seat circle shows seat numbers for table talk", () => {
    render(
      <GameScreen
        view={makeGameView()}
        busy={false}
        error=""
        setView={setView}
        run={run}
        leave={vi.fn()}
      />,
    );

    const seatPanel = screen.getByText("座位").closest("section");
    expect(seatPanel).not.toBeNull();
    expect(
      within(seatPanel as HTMLElement).getByText("1號"),
    ).toBeInTheDocument();
    expect(
      within(seatPanel as HTMLElement).getByText("2號"),
    ).toBeInTheDocument();
    expect(
      within(seatPanel as HTMLElement).getByText("林鏡"),
    ).toBeInTheDocument();
  });

  test("public and private chat are separated", () => {
    render(
      <GameScreen
        view={makeGameView()}
        busy={false}
        error=""
        setView={setView}
        run={run}
        leave={vi.fn()}
      />,
    );
    expect(
      within(screen.getByLabelText("public chat")).getByText("公開訊息"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByLabelText("private chat")).getByText(/私下對/),
    ).toBeInTheDocument();
    expect(
      within(screen.getByLabelText("public chat")).queryByText(/私下對/),
    ).not.toBeInTheDocument();
  });

  test("nomination UI is available", () => {
    render(
      <GameScreen
        view={makeGameView()}
        busy={false}
        error=""
        setView={setView}
        run={run}
        leave={vi.fn()}
      />,
    );
    expect(screen.getByText("提名與投票")).toBeInTheDocument();
    expect(screen.getAllByText(/AI 自主行動/).length).toBeGreaterThan(0);
    expect(screen.getByLabelText("ai table status")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "AI 自主一步" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "跑到需要我決策" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提名" })).toBeInTheDocument();
  });

  test("pending nomination shows vote buttons before anyone is on the block", async () => {
    const view = makeGameView({
      public: {
        ...makeGameView().public,
        phase: "VOTING",
        current_on_the_block: null,
        current_high_votes: 0,
        nominations: [
          {
            id: "nom-1",
            day: 1,
            nominator_id: "human",
            nominee_id: "ai_1",
            reason: "我想測這個說法。",
            defense: "我先辯護。",
            votes: 0,
            threshold: 0,
            eligible_for_execution: false,
            resolved: false,
          },
        ],
      },
      private: {
        ...makeGameView().private,
        legal_actions: ["vote_yes", "vote_no"],
      },
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => makeGameView() });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <GameScreen
        view={view}
        busy={false}
        error=""
        setView={setView}
        run={run}
        leave={vi.fn()}
      />,
    );

    expect(screen.getByText(/正在投票：2號 林鏡/)).toBeInTheDocument();
    expect(screen.queryByText("目前沒有人上台。")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "贊成" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/games/game-1/vote",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ player_id: "human", vote: true }),
        headers: expect.objectContaining({ "X-Player-Token": "test-token" }),
      }),
    );
  });

  test("ghost vote UI shows status", () => {
    render(
      <GameScreen
        view={makeGameView()}
        busy={false}
        error=""
        setView={setView}
        run={run}
        leave={vi.fn()}
      />,
    );
    expect(screen.getByText(/Ghost vote：尚可使用/)).toBeInTheDocument();
  });

  test("budget warning and usage are displayed", () => {
    const view = makeGameView({
      public: {
        ...makeGameView().public,
        usage: {
          ...makeGameView().public.usage,
          remaining_usd: 0,
          budget_usd: 0,
        },
      },
    });
    render(
      <GameScreen
        view={view}
        busy={false}
        error=""
        setView={setView}
        run={run}
        leave={vi.fn()}
      />,
    );
    expect(screen.getByText("API 用量")).toBeInTheDocument();
    expect(screen.getByText("剩餘：$0")).toBeInTheDocument();
  });

  test("game-over reveal is shown after result", () => {
    const base = makeGameView();
    const view = makeGameView({
      public: {
        ...base.public,
        result: { winner: "good", reason: "惡魔死亡", day: 2 },
      },
      postgame: {
        players: [
          {
            id: "human",
            name: "旅人",
            seat: 0,
            true_role: "artist",
            true_role_zh: "藝術家",
            alignment: "good",
            alive: true,
          },
        ],
        all_events: [],
      },
    });
    render(
      <GameScreen
        view={view}
        busy={false}
        error=""
        setView={setView}
        run={run}
        leave={vi.fn()}
      />,
    );
    expect(screen.getByText("遊戲結束")).toBeInTheDocument();
    expect(screen.getByText(/善良勝利/)).toBeInTheDocument();
    expect(screen.getAllByText("藝術家").length).toBeGreaterThan(1);
  });
});
