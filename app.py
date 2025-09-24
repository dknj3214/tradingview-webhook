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

        print("登入回應資料:", resp.json())

        accounts = resp.json().get("accounts", [])
        if accounts:
            self.account_id = accounts[0]["accountId"]
            self.account_info = resp.json().get("accountInfo")
            print("帳戶 ID:", self.account_id)
        else:
            raise Exception("無法找到帳戶資料")

        self.headers["X-SECURITY-TOKEN"] = resp.headers["X-SECURITY-TOKEN"]
        self.headers["CST"] = resp.headers["CST"]
        print("登入成功，帳戶 ID:", self.account_id)

        self.available_funds = float(self.account_info.get("available", 0))
        print(f"[登入] 可用保證金 available_funds: {self.available_funds}")

    def get_positions(self):
        url = self.base_url + "/positions"
        headers = self.headers.copy()
        headers["Version"] = "2"
        resp = self.session.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"查詢持倉失敗：{resp.status_code} {resp.text}")
        return resp.json().get("positions", [])

    def get_account_info(self):
        return self.account_info

    def get_spread(self, epic, pip_factor=10000):
        """
        取得指定 epic 的實時點差(pips)
        """
        url = self.base_url + "/prices"
        params = {"epics": epic}
        headers = self.headers.copy()
        headers["Version"] = "3"

        resp = self.session.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            print(f"[錯誤] 取得價格失敗: {resp.status_code} {resp.text}")
            return 0

        data = resp.json()
        prices = data.get("prices", [])
        if not prices:
            print(f"[錯誤] 找不到價格資料 epic={epic}")
            return 0

        price_info = prices[0]
        bid = float(price_info.get("bid", 0))
        offer = float(price_info.get("offer", 0))
        spread = (offer - bid) * pip_factor
        spread = max(spread, 0)
        print(f"[點差] epic: {epic}, bid: {bid}, offer: {offer}, spread: {spread:.2f} pips")
        return spread

    def calculate_size(self, entry, stop_loss, epic=None):
        try:
            entry = float(entry)
            stop_loss = float(stop_loss)

            if entry == stop_loss:
                raise ValueError("Entry price and stop loss cannot be the same.")

            pip_factor = 10000
            pip_value_per_standard_lot = 10
            risk_percent = 0.01
            equity = float(self.account_info.get("available", 0))
            risk_amount = equity * risk_percent

            pip_distance = abs(entry - stop_loss) * pip_factor
            pip_distance = max(pip_distance, 1)

            spread_pips = 0
            if epic:
                spread_pips = self.get_spread(epic, pip_factor)

            effective_pip_distance = pip_distance + spread_pips

            position_size = risk_amount / (effective_pip_distance * pip_value_per_standard_lot)

            print("[倉位計算 - 風控法（含點差）]")
            print(f"  ▶ 本金             : ${equity:.2f}")
            print(f"  ▶ 可承受風險金額   : ${risk_amount:.2f}")
            print(f"  ▶ 止損距離（pips）: {pip_distance:.1f}")
            print(f"  ▶ 點差（pips）      : {spread_pips:.1f}")
            print(f"  ▶ 有效止損距離(pips): {effective_pip_distance:.1f}")
            print(f"  ▶ ✅ 建議倉位大小   : {position_size:.2f} 手")

            return round(position_size, 2)

        except Exception as e:
            print(f"[錯誤] 倉位計算失敗: {str(e)}")
            return 0.0

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
        print(f"[下單] payload: {payload}")
        resp = self.session.post(url, json=payload, headers=headers)
        if resp.status_code not in [200, 201]:
            return {"error": resp.text, "status_code": resp.status_code}
        return resp.json()

    def close_position(self, epic=None):
        positions = self.get_positions()
        targets = [p for p in positions if p["market"]["epic"] == epic]
        if not targets:
            return {"error": f"找不到符合條件的持倉 (epic={epic})", "status_code": 404}

        results = []
        for target in targets:
            deal_id = target["position"]["dealId"]
            size = target["position"].get("dealSize") or target["position"].get("size")
            current_dir = target["position"]["direction"].upper()
            opposite = "SELL" if current_dir == "BUY" else "BUY"

            url = self.base_url + "/positions/otc"
            payload = {
                "dealId": deal_id,
                "epic": epic,
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
                results.append({"dealId": deal_id, "error": resp.text, "status_code": resp.status_code})
            else:
                results.append(resp.json())
        return results

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

@app.route("/get_account_info", methods=["GET"])
def api_get_account_info():
    try:
        return jsonify(trader.get_account_info())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/webhook", methods=["POST"])
def api_webhook():
    try:
        raw = request.data.decode("utf-8")
        print("收到 Webhook raw:", raw)

        data = dict(item.split("=") for item in raw.split("&") if "=" in item)
        print("解析後:", data)

        mode = data.get("mode")
        epic = data.get("epic")
        direction = data.get("direction")
        entry = data.get("entry")
        stop_loss = data.get("stop_loss")

        if mode == "order":
            if not epic or not direction or not entry or not stop_loss:
                return jsonify({"error": "epic, direction, entry, stop_loss 都要提供"}), 400
            size = trader.calculate_size(entry, stop_loss, epic=epic)
            result = trader.place_order(epic, direction, size)

        elif mode == "close":
            if not epic:
                return jsonify({"error": "close 時必須提供 epic"}), 400
            result = trader.close_position(epic=epic)

        else:
            return jsonify({"error": f"未知的 mode: {mode}"}), 400

        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
