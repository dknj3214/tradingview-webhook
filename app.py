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
            self.account_info = resp.json().get("accountInfo")  # 直接取得帳戶資訊
            print("帳戶 ID:", self.account_id)
        else:
            raise Exception("無法找到帳戶資料，登入成功但沒有帳戶信息")
        
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
        return self.account_info

    def calculate_size(self, entry, stop_loss):
        # === 帳戶資訊 ===
        account_info = self.get_account_info()
        equity = float(account_info.get("available") or account_info.get("balance") or 10000)

        # === 固定風險參數 ===
        risk_percent = 0.01  # 每筆交易風險 1%
        risk_amount = equity * risk_percent  # 風險金額

        # === 市場參數 ===
        pip_value_per_lot = 10  # 每 lot 每 pip 損益 10 美元（對 EUR/USD, GBP/USD 為固定值）
        pip_diff = abs(float(entry) - float(stop_loss))
        pip_count = pip_diff * 10000  # 點差轉換為 pip 數（EUR/USD 精度為 0.0001）

        if pip_count == 0:
            pip_count = 1  # 防止除以零錯誤

        # === 槓桿假設 ===
        leverage = 200  # 寫死為 200 倍槓桿

        # === 倉位大小計算 ===
        # 說明：風險金額 = 倉位大小 * pip 數 * 每 pip 價值 / 槓桿
        # 所以：倉位大小 = 風險金額 * 槓桿 ÷ (pip 數 × pip 價值)
        size = (risk_amount * leverage) / (pip_count * pip_value_per_lot)
        size = round(size, 2)  # 保留兩位小數

        print(f"[倉位計算] Equity: {equity}, Risk: {risk_amount}, Pip: {pip_count}, Size: {size}")
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
            size = trader.calculate_size(entry, stop_loss)
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
