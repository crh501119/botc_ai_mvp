# Implementation Plan

## 現況

- Repository 目前是小型 Python CLI 原型，文字編碼已損壞，且沒有可用的 Web UI、資料庫 schema、測試或資訊隔離邊界。
- 既有原型把角色與陣營直接放入 AI view，無法保證 hidden information safety，因此不保留核心架構。
- 可保留的方向只有「六人局、真人 + AI、Mock AI 可跑」這些產品概念。

## 目標架構

- Backend 使用 FastAPI + Pydantic 2 + SQLAlchemy 2 + SQLite。
- Domain layer 以 `TruthState` 作為唯一真實魔典；`PublicState` 與 `PlayerPrivateView` 由 context builder 明確投影，禁止直接序列化 truth/ORM 給 AI 或前端。
- 角色能力拆成 role handler/domain service，勝負檢查集中於 game engine。
- AI 透過 `AIProvider` abstraction 呼叫；OpenAI provider 使用 Responses API，Mock provider 離線可完整跑局。
- 每名 AI 有獨立 memory record、context budget、prompt 與 usage accounting。
- Frontend 使用 React + TypeScript + Vite，提供繁體中文 setup、遊戲主畫面、聊天、提名投票、用量、存取與結算揭露。

## 實作階段

1. 建立角色資料、狀態模型、事件 audience scope 與 setup generator。
2. 實作 No Greater Joy 六人規則、夜間能力、Drunk/Baron/Scarlet Woman/Imp starpass、投票與勝負。
3. 實作 persistence、API endpoints、OpenAI/Mock AI provider、context builder、budget accounting。
4. 建立 React UI 與 smoke 可用流程。
5. 補齊文件、Docker/run scripts、pricing config。
6. 建立 backend/frontend/unit/integration/security tests 並修正問題。

## 主要風險

- 官方完整《血染鐘樓》規則很大，本版只實作需求指定的六人 No Greater Joy 子集。
- 自然語言 Artist 問題只支援受限制 DSL；無法解析時不消耗能力。
- 真實 OpenAI API 行為與 token usage 欄位可能因模型而異，因此 provider 必須容錯並允許 Mock AI。
- 成本價格是估算，依 `config/model-pricing.json` 可更新。

## 驗收方式

- `ruff format`、`ruff check`、`mypy`、`pytest` 全部通過。
- 前端 `npm/pnpm` formatter、lint、type check、Vitest 通過。
- 使用固定 seed + Mock AI 從 setup 跑到 game over，至少各覆蓋 good 與 evil 勝利。
- 安全測試確認 truth 不進 AI context/正常 frontend response，私聊 audience 正確，postgame 前不可揭露。
- 本機可用 `run.ps1`、`run.sh` 或 Docker 啟動，Mock AI 無 API key 可玩。
