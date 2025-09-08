import os
import string
import time
import requests
from flask import Flask, request, jsonify

# ===========================
# 檢查 ASCII（避免 header 出錯）
# ===========================
def check_ascii(s, name):
    if not all(c in string.printable for c in s):
        raise ValueError(f"{name} 不能含非 ASCII 字元")

check_ascii(os.environ["IG_API_KEY"], "IG_API_KEY")
check_ascii(os.environ["IG_USERNAME"], "IG_USERNAME")
check_ascii(os.environ["IG_PASSWORD"], "IG_PASSWORD")

# ===========================
# IGTrader 類
# ===========================
class IGTrader:
    def __init__(self, api_key, username, password, account_type="DEMO"):
        self.api_key = api_key
        self.username = username
        self.password = password
        self.account_type = account_type.upper()
        self.base_url = (
            "https://demo-api.ig.com/gateway/deal"
            if self.account_type == "DEMO"
            else "https://api.ig.com/gateway/deal"
        )
        self.session = requests.Session()
        self.headers = {
            "X-IG-API-KEY": self.api_key,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
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
        self.account_id = resp.json()["accounts"][0]["accountId"]
        print("Login success, Account ID:", self.account_id)

    def get_positions(self):
        url = self.base_url + "/positions"
        headers = self.headers.copy()
        headers["Version"] = "1"
        resp = self.session.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"查詢持倉失敗：{resp.status_code} {resp.text}")
        return resp.json().get("positions", [])

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
            "dealReference": f"order-{int(time.time())}",
            "expiry": "-",
        }
        headers = self.headers.copy()
        headers["Version"] = "2"
        resp = self.session.post(url, json=payload, headers=headers)
        if resp.status_code not in [200, 201]:
            return {"error": resp.text, "status_code": resp.status_code}
        return resp.json()

    def close_position(self, deal_id=None, epic=None, size=None):
        positions = self.get_positions()
        target = None

        # 找持倉
        if deal_id:
            target = next(
                (p for p in positions if p["position"]["dealId"] == deal_id), None
            )
        elif epic:
            target = next(
                (p for p in positions if p["market"]["epic"] == epic), None
            )

        if not target:
            return {"error": f"找不到符合條件的持倉 (epic={epic}, dealId={deal_id})", "status_code": 404}

        # 拿必要資訊
        deal_id = target["position"]["dealId"]
        current_dir = target["position"]["direction"].upper()
        if not size:
            size = target["position"].get("dealSize") or target["position"].get("size")

        # 平倉要用反向方向
        opposite = "SELL" if current_dir == "BUY" else "BUY"

        url = self.base_url + "/positions/otc"
        payload = {
            "dealId": deal_id,
            "epic": target["market"]["epic"],
            "size": size,
            "direction": opposite,
            "orderType": "MARKET",
            "currencyCode": "USD",
            "forceOpen": False,
            "expiry": "-",
            "guaranteedStop": False,
            "dealReference": f"close-{deal_id}",
        }
        headers = self.headers.copy()
        headers["Version"] = "2"
        resp = self.session.post(url, json=payload, headers=headers)
        if resp.status_code not in [200, 201]:
            return {"error": resp.text, "status_code": resp.status_code}
        return resp.json()

# ===========================
# Flask App
# ===========================
app = Flask(__name__)
trader = IGTrader(
    api_key=os.environ["IG_API_KEY"],
    username=os.environ["IG_USERNAME"],
    password=os.environ["IG_PASSWORD"],
    account_type=os.environ.get("IG_ACCOUNT_TYPE", "DEMO"),
)

@app.route("/webhook", methods=["POST"])
def api_webhook():
    try:
        data = request.get_json(force=True)
        print("收到 Webhook:", data)

        mode = data.get("mode")
        epic = data.get("epic")
        size = data.get("size")
        deal_id = data.get("dealId")

        if mode == "order":
            direction = data.get("direction")
            if not epic or not direction:
                return jsonify({"error": "epic 和 direction 都要提供"}), 400
            result = trader.place_order(epic, direction, size or 1)

        elif mode == "close":
            if not epic and not deal_id:
                return jsonify({"error": "epic 或 dealId 至少要有一個"}), 400
            result = trader.close_position(deal_id=deal_id, epic=epic, size=size)

        elif mode == "positions":
            result = trader.get_positions()

        else:
            return jsonify({"error": "未知的 mode"}), 400

        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
