# 公開部署指南

這份指南的目標是讓知道網址的人可以加入同一局。請先理解目前安全邊界：這是私人朋友桌，不是大型公開平台。知道遊戲網址的人可以進 lobby，未認領的真人座位可被認領；座位認領後會用瀏覽器保存的 session token 保護。

## 最推薦路線：VPS + Docker Compose + HTTPS 反向代理

1. 準備一台 Linux VPS，安裝 Docker 與 Docker Compose。
2. 把 repository 放到 VPS。
3. 複製 `.env.example` 為 `.env`，至少設定：

```env
OPENAI_API_KEY=你的_key
MOCK_AI=false
DEV_REVEAL=false
GAME_BUDGET_USD=3.00
CORS_ORIGINS=https://你的網域
```

4. 啟動服務：

```bash
docker compose up -d --build
```

`--build` 會在啟動前重建映像，`-d` 會讓服務在背景執行。首次部署或程式更新後都建議使用這個指令。

5. 用 Caddy、Nginx、Traefik 或 Cloudflare Tunnel 把 HTTPS 網域轉到後端服務。

## Caddy 範例

如果主機上 Caddy 與 Docker 服務在同一台機器，Caddyfile 可以像這樣：

```caddyfile
botc.example.com {
  reverse_proxy 127.0.0.1:8000
}
```

然後瀏覽 `https://botc.example.com`。

## Cloudflare Tunnel 範例

Cloudflare Tunnel 適合不想直接開防火牆 port 的情況。

1. 在 Cloudflare Zero Trust 建立一個 tunnel。
2. 把 connector 裝在同一台 VPS。
3. 建立 public hostname，例如 `botc.example.com`。
4. Service 指向 `http://localhost:8000`。
5. 開啟網址，確認能看到 setup 畫面。

如果你把 `cloudflared` 放進同一個 Docker Compose network，service 也可以指向 compose service 名稱，例如 `http://botc-ai:8000`，實際名稱依 `docker-compose.yml` 而定。

## 開局與分享

1. 開啟公開網址。
2. 在 setup 畫面選擇真人人數，例如 3 真人 + 3 AI。
3. 建立新遊戲。
4. 複製遊戲畫面上的分享連結。
5. 把連結傳給朋友；每個人用自己的手機或瀏覽器選座位加入。

## 營運注意

- 正式放到網路上請一定使用 HTTPS，否則 session token 可能被攔截。
- `DEV_REVEAL=false` 必須保持關閉。
- 不要把 `.env`、SQLite 資料庫或 transcript 私下資訊提交到 git。
- SQLite 適合小型朋友桌；若要長期多人公開服務，下一步應改成 PostgreSQL、加入帳號/邀請碼與更嚴格的 action lock。
- 建議定期備份 Docker volume 或 SQLite 檔。

## 更新程式

```bash
docker compose down
docker compose up -d --build
```

若你要保留遊戲紀錄，不要刪除 Docker volume，也不要刪除 SQLite 資料檔。
