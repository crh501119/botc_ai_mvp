# 大型公開平台硬化路線

目前版本已經能支援朋友用網址加入同一局，但大型公開平台還需要更多非遊戲規則工程。下面是建議順序。

## 已有底座

- 每個真人座位有獨立 session token。
- 正常 API 依玩家視角過濾，不回傳其他人的私密資訊。
- SETUP 等待室會等所有真人入座，房主才能開始。
- SETUP 期間遮住角色卡與角色私訊，避免提前得知身分。
- 開局者可選順序發言或自由發言。
- 順序發言由後端驗證目前發言者，不只靠前端 disable。
- `auto-play` 與 dev reveal 只在 `DEV_REVEAL=true` 可用。

## 下一階段必做

1. 帳號系統：Email/OAuth 登入、玩家名稱保留、跨裝置恢復。
2. 房間權限：邀請碼、房主轉讓、鎖房、踢人、觀戰者、重連碼。
3. 即時推送：SSE 或 WebSocket，公開事件與私人事件分流。
4. 並發控制：per-game action lock、樂觀版本號、重複提交防護。
5. 後台治理：檢舉、封鎖、管理員審計、速率限制。
6. 資料庫：PostgreSQL、連線池、備份、遷移策略。
7. 秘密管理：部署平台 secret store，不用純 `.env` 管正式密鑰。
8. 成本保護：每房、每帳號、每日 API 預算與硬上限。
9. 觀測性：結構化 log、request id、錯誤追蹤、API usage dashboard。
10. 安全測試：端點權限矩陣、滲透測試、prompt/context leak regression。

## 不應該用 UI 假裝完成的部分

- 不要只在前端隱藏按鈕就當作權限控管。
- 不要讓知道 game id 的人可以刪房或取得 postgame 前資訊。
- 不要讓 AI 或使用者端送入任意 state transition。
- 不要把 session token 放在公開 log、URL query 或 transcript。
- 不要在沒有 HTTPS 的公開網路傳 token。

## 建議里程碑

### Milestone 1：私人公開網址

目前版本大致達成。適合朋友桌、測試局、小規模部署。

### Milestone 2：受邀房間

加入房間邀請碼、房主鎖座位、跨裝置重連碼、SSE 推送。

### Milestone 3：公開 lobby

加入帳號、公開/私密房列表、旁觀者、管理工具、速率限制。

### Milestone 4：正式服務

PostgreSQL、多副本部署、背景工作隊列、監控告警、備份與資料保留政策。
