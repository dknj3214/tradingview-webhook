from flask import Flask, request
from ig_trader import IGTrader

app = Flask(__name__)

# 初始化 IG API（請改成你的憑證）
ig = IGTrader(
    api_key="你的APIKEY",
    username="你的帳號",
    password="你的密碼",
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
        print("🚀 觸發買進操作")
        ig.place_order(EPIC, direction="BUY")
    elif action == "sell":
        print("🔻 觸發賣出操作")
        ig.place_order(EPIC, direction="SELL")
    else:
        print("⚠️ 未知訊號")

    return 'OK'

