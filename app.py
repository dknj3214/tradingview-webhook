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
        
        # 檢查登入回應是否成功
        if resp.status_code != 200:
            raise Exception(f"登入失敗：{resp.status_code} {resp.text}")
        
        # 打印完整的登入回應
        print("登入回應資料:", resp.json())
        
        # 確認是否有帳戶資料並提取帳戶 ID 和帳戶資訊
        accounts = resp.json().get("accounts", [])
        if accounts:
            self.account_id = accounts[0]["accountId"]
            self.account_info = resp.json().get("accountInfo")  # 直接取得帳戶資訊
            print("帳戶 ID:", self.account_id)  # 打印帳戶 ID
        else:
            raise Exception("無法找到帳戶資料，登入成功但沒有帳戶信息")
        
        # 設置標頭以後續使用
        self.headers["X-SECURITY-TOKEN"] = resp.headers["X-SECURITY-TOKEN"]
        self.headers["CST"] = resp.headers["CST"]
        print("登入成功，帳戶 ID 設置為:", self.account_id)

    def get_positions(self):
        url = self.base_url + "/positions"
        headers = self.headers.copy()
        headers["Version"] = "2"
        resp = self.session.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"查詢持倉失敗：{resp.status_code} {resp.text}")
        return resp.json().get("positions", [])

    def get_account_info(self):
        # 直接返回登入時獲取的帳戶資訊
        return self.account_info

    def get_market_price(self, epic, direction):
        url = f"{self.base_url}/markets/{epic}"
        headers = self.headers.copy()
        headers["Version"] = "1"
        resp = self.session.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"無法取得市場價格 {epic}: {resp.text}")
        market_data = resp.json()["market"]["snapshot"]
        return market_data["bid"] if direction.upper() == "SELL" else market_data["offer"]

    def calculate_size(self, epic, direction, stop_loss):
        # 直接從帳戶資訊中取得餘額
        account_info = self.get_account_info()
        equity = float(account_info.get("available") or account_info.get("balance") or 10000)
        risk_amount = equity * 0.01  # 每單風險 1%

        market_price = self.get_market_price(epic, direction)
        pip_value = abs(market_price - float(stop_loss))
        if pip_value == 0:
            pip_value = 1
        size = risk_amount / pip_value
        return size

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

# ===========================
# 新增帳戶資訊路由
# ===========================
@app.route("/get_account_info", methods=["GET"])
def api_get_account_info():
    try:
        account_info = trader.get_account_info()  # 呼叫 IGTrader 的 get_account_info 方法
        return jsonify(account_info)  # 返回帳戶資訊作為 JSON
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===========================
# Webhook 路由
# ===========================
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
            size = trader.calculate_size(epic, direction, stop_loss)
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
