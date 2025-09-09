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

    def get_account_equity(self):
        """查詢帳戶餘額 (equity)"""
        url = self.base_url + "/accounts"
        headers = self.headers.copy()
        headers["Version"] = "1"
        resp = self.session.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"查詢帳戶失敗：{resp.status_code} {resp.text}")
        accounts = resp.json().get("accounts", [])
        acc = next((a for a in accounts if a["accountId"] == self.account_id), None)
        if not acc:
            raise Exception("找不到當前帳戶資訊")
        return float(acc.get("equity", 0))

    def get_market_info(self, epic):
        """查詢商品資訊，回傳 contractSize"""
        url = self.base_url + f"/markets/{epic}"
        headers = self.headers.copy()
        headers["Version"] = "3"
        resp = self.session.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"查詢商品資訊失敗：{resp.status_code} {resp.text}")
        data = resp.json()
        return float(data["instrument"]["contractSize"])

    def calc_position_size(self, entry, stop_loss, equity, contract_size, risk_pct=0.01):
        """計算下單手數"""
        risk_amount = equity * risk_pct
        risk_per_unit = abs(entry - stop_loss) * contract_size
        if risk_per_unit <= 0:
            raise ValueError("停損與進場價差異必須大於 0")
        size = risk_amount / risk_per_unit
        return max(0.01, round(size, 2))  # 保證不會是 0

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

        if deal_id:
            target = next((p for p in positions if p["position"]["dealId"] == deal_id), None)
        elif epic:
            target = next((p for p in positions if p["market"]["epic"] == epic), None)

        if not target:
            return {"error": f"找不到符合條件的持倉 (epic={epic}, dealId={deal_id})", "status_code": 404}

        deal_id = target["position"]["dealId"]
        current_dir = target["position"]["direction"].upper()
        if not size:
            size = target["position"].get("dealSize") or target["position"].get("size")

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
        raw = request.data.decode("utf-8")
        print("收到 Webhook raw:", raw)

        data = dict(item.split("=") for item in raw.split("&") if "=" in item)
        print("解析後:", data)

        mode = data.get("mode")
        epic = data.get("epic")

        if mode == "order":
            direction = data.get("direction")
            entry = float(data.get("entry", 0))
            stop_loss = float(data.get("stop_loss", 0))

            if not epic or not direction or entry == 0 or stop_loss == 0:
                return jsonify({"error": "epic, direction, entry, stop_loss 都要提供"}), 400

            equity = trader.get_account_equity()
            contract_size = trader.get_market_info(epic)
            size = trader.calc_position_size(entry, stop_loss, equity, contract_size)

            result = trader.place_order(epic, direction, size)

        elif mode == "close":
            deal_id = data.get("dealId")
            size = float(data.get("size", 1))
            result = trader.close_position(deal_id=deal_id, epic=epic, size=size)

        elif mode == "positions":
            result = trader.get_positions()

        else:
            return jsonify({"error": f"未知的 mode: {mode}"}), 400

        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
