# 六人血染鐘樓 AI MVP

## 1. 這個程式做什麼

這是一個本機 Web app：1 名真人玩家與 5 名彼此資訊隔離的 AI 玩家，遊玩六人 No Greater Joy Teensyville 子集。Python 規則引擎負責角色能力、死亡、投票與勝負；AI 只負責發言、策略與選擇。

## 2. 系統需求

- Python 3.12+
- Node.js 20+
- pnpm 或 npm
- Docker Desktop（可選）

## 3. Windows 安裝步驟

```powershell
copy .env.example .env
.\run.ps1
```

若使用真實模型，請先編輯 `.env` 填入 `OPENAI_API_KEY` 並把 `MOCK_AI=false`。

## 4. macOS/Linux 安裝步驟

```bash
cp .env.example .env
chmod +x run.sh
./run.sh
```

## 5. Docker 啟動步驟

```bash
cp .env.example .env
docker compose up --build
```

開啟 `http://localhost:8000`。

## 6. OpenAI API key 設定

`.env` 中：

```env
OPENAI_API_KEY=sk-...
AI_DIALOGUE_MODEL=gpt-5.4-mini
AI_DECISION_MODEL=gpt-5.4-nano
OPENAI_STORE=false
MOCK_AI=false
```

API key 只留在後端環境變數，不會傳到前端。

## 7. Mock AI 模式

預設 `.env.example` 使用 `MOCK_AI=true`，完全不需要 API key，可完整開局、遊玩、保存、結算與測試。

使用真實 OpenAI API 時，每名 AI 會收到各自隔離的玩家 context：persona、近期公開發言、自己參與的私聊、自己的記憶摘要、當前合法行動與劇本資訊。模型可回傳結構化 memory update，後端會驗證後只寫入該 AI 自己的記憶。

## 8. 如何開始一局

在 Setup 畫面輸入真人名稱、預算與 Mock AI 開關，random seed 可留空；留空會像一般線上遊戲一樣產生新亂數，填入數字則可重現同一局。按「新遊戲」後，主畫面會顯示六人座位、你的角色、公開聊天、私聊、提名投票、劇本角色表、AI 桌面狀態與 API 用量。白天 AI 會每隔數秒嘗試自主發言、私聊、提名或推進流程；公開討論與私聊會分批進行，遇到真人投票時會停下等待你操作。

## 9. 如何查看用量

主畫面的「API 用量」會顯示呼叫次數、input/output/reasoning token、估算費用、預算上限與剩餘量。價格來自 `config/model-pricing.json`；缺少價格時只顯示 token，不虛構費用。

## 10. 如何儲存和恢復

所有狀態會自動保存到 SQLite。按「儲存並離開」回到 Setup 畫面後，可在「載入舊遊戲」選取保存紀錄。

## 11. 如何執行測試

後端：

```bash
python -m pytest
python -m ruff format .
python -m ruff check .
python -m mypy botc_ai
python scripts/smoke.py
```

前端：

```bash
cd frontend
pnpm install
pnpm run format
pnpm run lint
pnpm run typecheck
pnpm run test
pnpm run smoke
pnpm run build
```

## 12. 常見錯誤

- 沒有 API key：使用 `MOCK_AI=true`。
- pnpm 找不到 node：確認 Node.js 在 PATH。
- 預算用完：提高本局上限或切換 Mock AI。
- Docker 沒保存：確認 compose 的 `botc-data` volume 沒被刪除。

## 13. 資訊隔離原理

後端分成 `TruthState`、`PublicState`、`PlayerPrivateView`。AI 呼叫前只能由 context builder 產生該玩家 private view；不序列化 ORM、Game object 或完整資料庫列給 AI。正常前端 API 在遊戲結束前不回傳其他玩家真實角色。

## 14. 已知限制

首版只支援需求指定的 No Greater Joy 六人子集。AI 說書人已實作為受限策略層，只能從規則引擎提供的合法選項中挑選 Drunk 錯訊、Investigator/Sage 誘餌等資訊；尚未做成自由對話式 LLM 說書人人格。

## 15. 非官方 fan project 聲明

本專案不是官方產品，未隸屬或代表 The Pandemonium Institute。未使用或散布官方 PDF、美術、角色圖示、logo、字型或官方完整角色文字。
