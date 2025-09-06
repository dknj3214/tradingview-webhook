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
    size = float(data.get("size", 0))  # 預設 1 手

    try:
        # 每次訊號來才建立 IG 連線
        ig = IGTrader(
            api_key=os.getenv("2cf23e4c88a23770faaf86d6399541f411884430"),
            username=os.getenv("Dknj3214"),
            password=os.getenv("Dknj3213"),
            account_type=os.getenv("DEMO", "DEMO")
        )

        if action == "buy":
            print(f"🚀 執行買單：{size} 手")
            ig.place_order(EPIC, direction="BUY", size=size)
        elif action == "sell":
            print(f"🔻 執行賣單：{size} 手")
            ig.place_order(EPIC, direction="SELL", size=size)
        else:
            print("⚠️ 未知訊號，略過")

    except Exception as e:
        print(f"❌ 執行錯誤：{e}")
        return f"Error: {e}", 500

    return 'OK'

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
