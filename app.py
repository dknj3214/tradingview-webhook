from flask import Flask, request
from ig_trader import IGTrader

app = Flask(__name__)

# 初始化 IG API（請改成你的憑證）
ig = IGTrader(
    api_key="2cf23e4c88a23770faaf86d6399541f411884430",
    username="Dknj3214",
    password="Dknj3213",
    account_type="DEMO"  # 或 "LIVE"
)

# 設定你要交易的商品
EPIC = "CS.D.EURUSD.CFD.IP"

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("收到 TradingView 訊號：", data)

    action = data.get("action", "").lower()

    if action == "buy":
        print(f"🚀 執行買單：{size} 手")
        ig.place_order(EPIC, direction="BUY", size=size)
    elif action == "sell":
        print(f"🔻 執行賣單：{size} 手")
        ig.place_order(EPIC, direction="SELL", size=size)
    else:
        print("⚠️ 未知訊號，略過")

    return 'OK'

