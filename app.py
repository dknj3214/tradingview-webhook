from flask import Flask, request
from ig_trader import IGTrader
import os

# =============================
# Flask Webhook Server 初始化
# =============================
app = Flask(__name__)

# =============================
# TradingView ticker → IG EPIC 映射表
# 用來把 TradingView 快訊的 ticker 轉成 IG 下單需要的 EPIC
# =============================
TICKER_MAP = {
    "EURUSD": "CS.D.EURUSD.CFD.IP",
    "GBPUSD": "CS.D.GBPUSD.CFD.IP",
    "BTCUSD": "CS.D.BTCUSD.CFD.IP"
}

# =============================
# Webhook Endpoint
# TradingView 快訊會 POST JSON 到這裡
# =============================
@app.route('/webhook', methods=['POST'])
def webhook():
    # -----------------------------
    # 解析收到的 JSON 資料
    # -----------------------------
    data = request.json
    print("📩 收到 TradingView 訊號：", data)

    # 取得快訊中的關鍵欄位
    action = data.get("action", "").lower()             # 買或賣
    size = float(data.get("size", 0))                  # 手數
    ticker = data.get("ticker", "").upper()           # 商品代碼
    position_size = data.get("position_size", 0)      # 未使用，可擴充

    print(f"👉 action={action}, size={size}, ticker={ticker}, position_size={position_size}")

    # -----------------------------
    # 檢查 size 是否有效
    # -----------------------------
    if size <= 0:
        print("⚠️ size 為 0 或無效，略過下單")
        return "Ignored", 200

    # -----------------------------
    # 轉換 TradingView ticker → IG EPIC
    # -----------------------------
    epic = TICKER_MAP.get(ticker)
    if not epic:
        print(f"⚠️ 找不到對應 EPIC，略過下單: {ticker}")
        return "Unknown ticker", 400

    # =============================
    # 下單區塊
    # 每次 webhook 收到訊號才登入 IG
    # =============================
    try:
        print("🔑 嘗試登入 IG API...")
        ig = IGTrader(
            api_key=os.getenv("IG_API_KEY"),         # 從環境變數讀取
            username=os.getenv("IG_USERNAME"),
            password=os.getenv("IG_PASSWORD"),
            account_type=os.getenv("IG_ACCOUNT_TYPE", "DEMO")  # 預設 DEMO
        )
        print(f"✅ IG 登入成功，帳號 ID：{ig.account_id}")

        # -----------------------------
        # 印出下單資訊
        # -----------------------------
        payload_info = f"EPIC={epic}, direction={action.upper()}, size={size}"
        print("📦 下單資訊:", payload_info)

        # -----------------------------
        # 執行下單
        # -----------------------------
        if action == "buy":
            ig.place_order(epic, direction="BUY", size=size)
        elif action == "sell":
            ig.place_order(epic, direction="SELL", size=size)
        else:
            print("⚠️ 未知訊號，略過下單")

    # -----------------------------
    # 捕捉所有錯誤，避免 webhook 崩潰
    # -----------------------------
    except Exception as e:
        print(f"❌ webhook 執行錯誤：{e}")
        return f"Error: {e}", 500

    return 'OK'


# =============================
# Flask Server 啟動
# =============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))  # Render 上通常用環境變數 PORT
    app.run(host="0.0.0.0", port=port)
