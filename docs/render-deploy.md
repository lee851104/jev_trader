# Render 部署

保留目前 Python 交易引擎；以 Caddy 提供登入驗證與反向代理，Render 負責 HTTPS。
Python 仍僅監聽 127.0.0.1:8767，不將本機服務直接公開。

## 部署

1. 將專案程式碼放到自己的 GitHub 儲存庫。不要上傳 `.env`、`.venv`、`artifacts` 或交易 JSON。
2. Render 選 New → Blueprint，選此儲存庫，使用根目錄 `render.yaml`。
3. 預設為 Free Web Service，Docker 建置；健康檢查 `/healthz`。不自動部署後續 Git 推送。
4. `TRADING_LOGIN_PASSWORD` 由 Render 自動產生。在服務 Environment 查看，登入帳號為 `owner`。
5. 在 Render 的 Environment 安全設定 `TYPESAFE_API_KEY`，保留 `TYPESAFE_MODEL=jev-latest`。未設定金鑰時可使用離線規則。
6. 若選一般 New Web Service 而非 Blueprint，選 Docker、Free，手動新增至少 24 字元的 `TRADING_LOGIN_PASSWORD`，健康檢查設 `/healthz`。
7. Render 自動提供 `PORT` 與 `RENDER_EXTERNAL_URL`。自訂網域時另外設定 `TRADING_PUBLIC_ORIGIN=https://你的網域`，不可包含路徑或尾端斜線。

## 費用與資料

- Free Web Service 不支援持久磁碟，重啟、休眠或重新部署後紀錄可能遺失；請及時下載 JSON／CSV。此版本不會把本機既有紀錄上傳。
- 如需長期保留紀錄，先確認付費方案，再掛載 persistent disk 並將 `TRADING_DATA_DIR` 設為掛載目錄；不要在未確認費用時啟用。
- Jev 推論另計費。只有登入者能存取介面與呼叫 API。不要公開登入密碼。
- 目前為單一使用者／共用帳戶實驗室；所有持有登入密碼的人會看到相同交易狀態。不提供多人帳戶隔離。

## 保護範圍

- 沒有登入密碼或 HTTPS 網址時，雲端入口拒絕啟動。
- Caddy 驗證登入，檢查 Host 及 POST Origin，再把請求送往本機 Python；既有 CSRF 與版本檢查保留。
- `/healthz` 只回傳健康狀態，不提供金鑰、CSRF token 或交易資料。
- 密碼以 stdin 傳入 Caddy 雜湊，不出現在命令列或專案檔；Caddy 子程序不接收 Jev 金鑰。
- Docker 採允許清單複製檔案，不複製本機 `.env`、歷史紀錄或工具。
- 雲端不載入本機 `.env`，金鑰由 Render 環境變數提供。

來源：[Render 部署](https://render.com/docs/docker)、[免費方案限制](https://render.com/docs/free)、[Caddy 登入驗證](https://caddyserver.com/docs/caddyfile/directives/basic_auth)。
