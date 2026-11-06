# 生豬 Gamma Flip 戰情板

大連商品交易所生豬（LH）期權的造市商 Gamma 曝險與 Gamma Flip 每日自動計算，提供電腦版與手機版兩種頁面。架構與 TXO_Gamma_Flip、PTA_Gamma_Flip 相同。

## 必要設定：大商所 API 憑證

大商所自 2025-12 起改用「對外門戶 API 服務」，需免費註冊帳號取得 apikey 與 secret（有每分鐘呼叫次數限制）。取得後在本 repo 的 Settings → Secrets and variables → Actions 新增：

- `DCE_API_KEY`
- `DCE_SECRET`

沒有設定時，程式會改試舊版公開匯出頁（可能已停用）。

## 資料與計算

- 介面：`POST /dceapi/cms/auth/accessToken` 取得 token，再 `POST /dceapi/forward/publicweb/dailystat/dayQuotes`（varietyId=lh，tradeType 1=期貨、2=期權）
- 每個期權月份以同月份生豬期貨為標的；以持倉量最大的主力期貨為基準價，履約價換算成主力等價後合併計算
- GEX 單位萬元（每手 16 噸）；ATR 為主力期貨近 5 日真實波幅平均
- 到期日：標的期貨交割月份前一個月的第 12 個交易日；大陸休市日列在 `data/holidays.json`，長假前（例如春節）要更新
- 第一次取得資料時會存 `data/_sample_fut.json`、`data/_sample_opt.json`，用來核對大商所欄位

## 檔案

- `scripts/lh_gex.py`：下載與計算；`.github/workflows/update.yml`：交易日 16:37／17:13／18:07 自動執行
- `src/desktop.html`、`src/mobile.src.html` → `python scripts/build_pages.py` 產生 `index.html`、`mobile.html`、`build/`
- `scripts/build_offline.py LH`：產生 `offline/` 離線版（每日隨資料自動重產）
