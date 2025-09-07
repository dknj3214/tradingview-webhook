from flask import Flask, request
import requests
import os

# =============================
# IGTrader 類
# =============================
class IGTrader:
    def __init__(self, api_key, username, password, account_type="DEMO"):
        self.api_key = api_key
        self.username = username
        self.password = password
        self.account_type = account_type.upper()
        self.base_url = "https://demo-api.ig.com/gateway/deal" if self.account_type == "DEMO" else "https://api.ig.com/gateway/deal"
        self.session = requests.Session()
        self.headers = {
            "X-IG-API-KEY": self.api_key,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8"
        }
        self._login()

    def _login(self):
        url = self.base_url + "/session"
        payload = {"identifier": self.username, "password": self.password}
        resp = self.session.post(url, json=payload, headers=self.headers)
        if resp.status_code != 200:
            raise Exception(f"登入失敗：{resp.status_code} {resp.text}")
        self.headers["X-SECURITY-TOKEN"] = resp.headers["X-SECURITY-TOKEN"]
        self.headers["CST"] = resp.headers["CST"]
        account_info = resp.json()
        self.account_id = account_info["accounts"][0]["accountId"]
        print(f"✅ 登入成功，帳號 ID：{self.account_id}")

    def place_order(self, epic, direction, size=1, order_type="MARKET"):
        url = self.base_url + "/positions/otc"
        payload = {
            "epic": epic,
            "direction": direction.upper(),
            "size": size,
            "orderType": order_type,
            "currencyCode": "USD",
            "forceOpen": True,
            "guaranteedStop": False,
            "timeInForce": "FILL_OR_KILL",
            "dealReference": "tv_auto_order",
            "expiry": "-"
        }
        headers = self.headers.copy()
        headers["Version"] = "2"
        resp = self.session.post(url, json=payload, headers=headers)
        if resp.status_code not in [200, 201]:
            print(f"❌ 下單失敗：{resp.status_code} {resp.text}")
        else:
            print("✅ 成功下單：", resp.json())

    def get_positions(self):
        url = self.base_url + "/positions"
        headers = self.headers.copy()
        headers["Version"] = "1"
        resp = self.session.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"查詢持倉失敗：{resp.status_code} {resp.text}")
        return resp.json()["positions"]

    def close_position(self, deal_id, direction, size):
        url = self.base_url + "/positions/otc"
        payload = {
            "dealId": deal_id,
            "direction": direction.upper(),
            "size": size,
            "orderType": "MARKET",
            "forceOpen": False,
            "dealReference": f"close-{deal_id}"
        }
        headers = self.headers.copy()
        headers["Version"] = "2"
        resp = self.session.post(url, json=payload, headers=headers)
        if resp.status_code not in [200, 201]:
            print(f"❌ 平倉失敗：{resp.status_code} {resp.text}")
        else:
            print("✅ 成功平倉：", resp.json())

    def get_market_info(self, epic):
        url = self.base_url + f"/markets/{epic}"
        headers = self.headers.copy()
        headers["Version"] = "3"
        resp = self.session.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"查詢商品資訊失敗：{resp.status_code} {resp.text}")
        return resp.json()

# =============================
# Flask Webhook Server 初始化
# =============================
app = Flask(__name__)

# TradingView ticker → IG EPIC 映射表
TICKER_MAP = {
    "EURUSD": "CS.D.EURUSD.CFD.IP",
    "GBPUSD": "CS.D.GBPUSD.CFD.IP",
    "BTCUSD": "CS.D.BITCOIN.CFD.IP"
}

# =============================
# Webhook Endpoint
# =============================
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("📩 收到 TradingView 訊號：", data)

    action = data.get("action", "").lower()
    raw_size = float(data.get("size", 0))
    ticker = data.get("ticker", "").upper()

    if raw_size <= 0:
        return "Ignored", 200

    epic = TICKER_MAP.get(ticker)
    if not epic:
        return "Unknown ticker", 400

    try:
        ig = IGTrader(
            api_key=os.getenv("IG_API_KEY"),
            username=os.getenv("IG_USERNAME"),
            password=os.getenv("IG_PASSWORD"),
            account_type=os.getenv("IG_ACCOUNT_TYPE", "DEMO")
        )

        market_info = ig.get_market_info(epic)
        min_size = float(market_info["dealingRules"]["minDealSize"]["value"])
        size = max(round(raw_size, 2), min_size)

        positions = ig.get_positions()
        current_pos = None
        for pos in positions:
            if pos["market"]["epic"] == epic:
                current_pos = pos["position"]
                break

        # -----------------------------
        # 平倉邏輯（全部平倉）
        # -----------------------------
        if current_pos:
            pos_dir = current_pos["direction"]      # BUY / SELL
            pos_size = float(current_pos.get("size", 0))
            deal_id = current_pos.get("dealId")

            # 方向相反 → 平倉
            if (pos_dir == "BUY" and action == "sell") or (pos_dir == "SELL" and action == "buy"):
                close_dir = pos_dir  # 與現有倉同方向
                print(f"🛑 平倉 {epic}, size={pos_size}, direction={close_dir}")
                ig.close_position(deal_id, direction=close_dir, size=pos_size)
                print("✅ 平倉完成")

                # 平倉後開新單
                new_dir = "BUY" if action == "buy" else "SELL"
                print(f"📦 平倉後開新單: EPIC={epic}, direction={new_dir}, size={size}")
                ig.place_order(epic, direction=new_dir, size=size)
                return "Closed and New Order Placed", 200

            # 同方向持倉 → 忽略，不開新單
            print(f"⚡ 已有同方向持倉，略過下單: {pos_dir}")
            return "Existing position same direction, ignored", 200

        # -----------------------------
        # 沒有持倉 → 開新單
        # -----------------------------
        new_dir = "BUY" if action == "buy" else "SELL"
        print(f"📦 下單資訊: EPIC={epic}, direction={new_dir}, size={size}")
        ig.place_order(epic, direction=new_dir, size=size)

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
