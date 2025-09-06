from flask import Flask, request
from ig_trader import IGTrader
import os

# =============================
# Flask Webhook Server 初始化
# =============================
app = Flask(__name__)

# =============================
# TradingView ticker → IG EPIC 映射表
# =============================
TICKER_MAP = {
    "EURUSD": "CS.D.EURUSD.CFD.IP",
    "GBPUSD": "CS.D.GBPUSD.CFD.IP",
    "BTCUSD": "CS.D.BITCOIN.CFD.IP"
}

# =============================
# Webhook Endpoint
# TradingView 快訊會 POST JSON 到這裡
# =============================
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("📩 收到 TradingView 訊號：", data)

    action = data.get("action", "").lower()      # buy 或 sell
    size = float(data.get("size", 0))           # 手數
    ticker = data.get("ticker", "").upper()     # 商品代碼

    print(f"👉 action={action}, size={size}, ticker={ticker}")

    if size <= 0:
        print("⚠️ size 無效，略過下單")
        return "Ignored", 200

    epic = TICKER_MAP.get(ticker)
    if not epic:
        print(f"⚠️ 找不到對應 EPIC: {ticker}")
        return "Unknown ticker", 400

    try:
        ig = IGTrader(
            api_key=os.getenv("IG_API_KEY"),
            username=os.getenv("IG_USERNAME"),
            password=os.getenv("IG_PASSWORD"),
            account_type=os.getenv("IG_ACCOUNT_TYPE", "DEMO")
        )

        # -----------------------------
        # 查詢現有持倉
        # -----------------------------
        positions = ig.get_positions()
        current_pos = None
        for pos in positions:
            if pos["market"]["epic"] == epic:
                current_pos = pos["position"]
                break

        # -----------------------------
        # 平倉邏輯：若持倉方向與訊號相反
        # 平倉後不開新單
        # -----------------------------
        if current_pos:
            pos_dir = current_pos["direction"]  # "BUY" 或 "SELL"
            pos_size = current_pos.get("size", 0)
            deal_id = current_pos["dealId"]

            if (pos_dir == "BUY" and action == "sell") or (pos_dir == "SELL" and action == "buy"):
                print(f"🛑 平倉 {epic}, dealId={deal_id}, size={pos_size}")
                # 呼叫平倉方法，方向用現有倉位
                ig.close_position(deal_id, size=pos_size, direction=pos_dir)
                print("✅ 已平倉，Webhook 結束")
                return "Closed", 200  # 平倉後不開新單

        # -----------------------------
        # 沒有持倉 → 開新單
        # -----------------------------
        if not current_pos:
            print(f"📦 下單資訊: EPIC={epic}, direction={action.upper()}, size={size}")
            if action == "buy":
                ig.place_order(epic, direction="BUY", size=size)
            elif action == "sell":
                ig.place_order(epic, direction="SELL", size=size)

    except Exception as e:
        print(f"❌ webhook 執行錯誤：{e}")
        return f"Error: {e}", 500

    return "OK"


# =============================
# Flask Server 啟動
# =============================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
