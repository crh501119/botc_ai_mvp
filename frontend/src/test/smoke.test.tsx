import { render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { GameScreen } from "../App";
import { makeGameView } from "./fixtures";

describe("smoke", () => {
  test("main game screen renders", () => {
    render(
      <GameScreen
        view={makeGameView()}
        busy={false}
        error=""
        setView={vi.fn()}
        run={vi.fn()}
        leave={vi.fn()}
      />,
    );
    expect(screen.getByText("座位")).toBeInTheDocument();
    expect(screen.getByText("公開聊天")).toBeInTheDocument();
  });
});
