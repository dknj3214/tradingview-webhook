import os
import time
import json
import string
import requests
from flask import Flask, request, jsonify

# ===========================
# 檢查 ASCII（避免 header 出錯）
# ===========================
def check_ascii(s, name):
    if not all(c in string.printable for c in s):
        raise ValueError(f"{name} 不能含非 ASCII 字元")

# ===========================
# IGTrader 類
# ===========================
class IGTrader:
    def __init__(self):
        self.api_key = os.environ["IG_API_KEY"]
        self.username = os.environ["IG_USERNAME"]
        self.password = os.environ["IG_PASSWORD"]
        self.account_type = os.environ.get("IG_ACCOUNT_TYPE", "DEMO").upper()

        check_ascii(self.api_key, "IG_API_KEY")
        check_ascii(self.username, "IG_USERNAME")
        check_ascii(self.password, "IG_PASSWORD")

        self.base_url = "https://demo-api.ig.com/gateway/deal" if self.account_type=="DEMO" else "https://api.ig.com/gateway/deal"
        self.session = requests.Session()
        self.headers = {
            "X-IG-API-KEY": self.api_key,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8"
        }
        self._login()

    def _login(self):
        url = f"{self.base_url}/session"
        payload = {"identifier": self.username, "password": self.password}
        resp = self.session.post(url, json=payload, headers=self.headers)
        if resp.status_code != 200:
            raise Exception(f"登入失敗：{resp.status_code} {resp.text}")
        self.headers["X-SECURITY-TOKEN"] = resp.headers["X-SECURITY-TOKEN"]
        self.headers["CST"] = resp.headers["CST"]
        self.account_id = resp.json()["accounts"][0]["accountId"]
        print("Login success, Account ID:", self.account_id)

    def get_positions(self):
        url = f"{self.base_url}/positions"
        headers = self.headers.copy()
        headers["Version"] = "1"
        resp = self.session.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"查詢持倉失敗：{resp.status_code} {resp.text}")
        return resp.json().get("positions", [])

    def place_order(self, epic, direction, size=1, order_type="MARKET"):
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
            "expiry": "-"
        }
        headers = self.headers.copy()
        headers["Version"] = "2"
        resp = self.session.post(f"{self.base_url}/positions/otc", json=payload, headers=headers)
        if resp.status_code not in [200, 201]:
            return {"error": resp.text, "status": resp.status_code}
        return resp.json()

    def close_position(self, epic, deal_id, size, direction):
        payload = {
            "epic": epic,
            "dealId": deal_id,
            "size": size,
            "direction": direction.upper(),
            "orderType": "MARKET",
            "currencyCode": "USD",
            "forceOpen": False,
            "expiry": "-",
            "guaranteedStop": False,
            "dealReference": f"close-{deal_id}"
        }
        headers = self.headers.copy()
        headers["Version"] = "2"
        resp = self.session.post(f"{self.base_url}/positions/otc", json=payload, headers=headers)
        if resp.status_code not in [200, 201]:
            return {"error": resp.text, "status": resp.status_code}
        return resp.json()

# ===========================
# Flask API
# ===========================
app = Flask(__name__)
trader = IGTrader()

@app.route("/positions", methods=["GET"])
def get_positions():
    try:
        positions = trader.get_positions()
        return jsonify(positions)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/order", methods=["POST"])
def order():
    data = request.json
    epic = data.get("epic")
    direction = data.get("direction")
    size = data.get("size", 1)
    if not epic or not direction:
        return jsonify({"error": "epic 和 direction 必填"}), 400
    result = trader.place_order(epic, direction, size)
    return jsonify(result)

@app.route("/close", methods=["POST"])
def close():
    data = request.json
    epic = data.get("epic")
    deal_id = data.get("dealId")
    direction = data.get("direction")
    size = data.get("size", 1)
    if not epic or not deal_id or not direction:
        return jsonify({"error": "epic, dealId, direction 必填"}), 400
    result = trader.close_position(epic, deal_id, size, direction)
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
