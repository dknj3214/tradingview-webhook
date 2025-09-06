from flask import Flask, request
from ig_trader import IGTrader
import os

app = Flask(__name__)

# TradingView ticker → IG EPIC
TICKER_MAP = {
    "EURUSD": "CS.D.EURUSD.CFD.IP",
    "GBPUSD": "CS.D.GBPUSD.CFD.IP",
    "BTCUSD": "CS.D.BITCOIN.CFD.IP"
}

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("📩 收到 TradingView 訊號：", data)

    action = data.get("action", "").lower()      # buy / sell
    size = float(data.get("size", 0))
    ticker = data.get("ticker", "").upper()

    print(f"👉 action={action}, size={size}, ticker={ticker}")

    if size <= 0:
        print("⚠️ size 無效，略過")
        return "Ignored", 200

    epic = TICKER_MAP.get(ticker)
    if not epic:
        print(f"⚠️ 找不到 EPIC: {ticker}")
        return "Unknown ticker", 400

    try:
        ig = IGTrader(
            api_key=os.getenv("IG_API_KEY"),
            username=os.getenv("IG_USERNAME"),
            password=os.getenv("IG_PASSWORD"),
            account_type=os.getenv("IG_ACCOUNT_TYPE", "DEMO")
        )

        # 取得當前持倉
        current_pos = ig.get_open_position(epic)

        # 若有持倉且收到反向訊號 → 平倉
        if current_pos:
            pos_dir = current_pos["direction"]
            pos_size = current_pos["size"]
            if (pos_dir=="BUY" and action=="sell") or (pos_dir=="SELL" and action=="buy"):
                print(f"🛑 平倉 {epic}, size={pos_size}")
                ig.close_position(epic, direction=action.upper(), size=pos_size)
                return "Closed", 200  # 平倉後不開新單

        # 沒持倉 → 下新單
        if not current_pos:
            print(f"📦 下單 EPIC={epic}, direction={action.upper()}, size={size}")
            ig.place_order(epic, direction=action.upper(), size=size)

    except Exception as e:
        print(f"❌ webhook 執行錯誤：{e}")
        return f"Error: {e}", 500

    return 'OK'

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
