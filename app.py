from flask import Flask, request
from ig_trader import IGTrader
import os

app = Flask(__name__)

# 你要交易的商品 (範例 EURUSD CFD)
EPIC = "CS.D.EURUSD.CFD.IP"

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("📩 收到 TradingView 訊號：", data)

    action = data.get("action", "").lower()
    size = float(data.get("size", 0))  # 預設 0 手
    ticker = data.get("ticker", "")
    position_size = data.get("position_size", 0)

    print(f"👉 action={action}, size={size}, ticker={ticker}, position_size={position_size}")

    if size <= 0:
        print("⚠️ size 為 0 或無效，略過下單")
        return "Ignored", 200

    try:
        print("🔑 嘗試登入 IG API...")
        ig = IGTrader(
            api_key=os.getenv("IG_API_KEY"),
            username=os.getenv("IG_USERNAME"),
            password=os.getenv("IG_PASSWORD"),
            account_type=os.getenv("IG_ACCOUNT_TYPE", "DEMO")
        )
        print(f"✅ IG 登入成功，帳號 ID：{ig.account_id}")

        payload_info = f"EPIC={EPIC}, direction={action.upper()}, size={size}"
        print("📦 下單資訊:", payload_info)

        if action == "buy":
            ig.place_order(EPIC, direction="BUY", size=size)
        elif action == "sell":
            ig.place_order(EPIC, direction="SELL", size=size)
        else:
            print("⚠️ 未知訊號，略過下單")

    except Exception as e:
        print(f"❌ webhook 執行錯誤：{e}")
        return f"Error: {e}", 500

    return 'OK'

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
