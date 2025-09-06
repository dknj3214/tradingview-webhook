import requests

class IGTrader:
    def __init__(self, api_key, username, password, account_type="DEMO"):
        self.api_key = api_key
        self.username = username
        self.password = password
        self.account_type = account_type.upper()
        self.base_url = "https://demo-api.ig.com/gateway/deal" if self.account_type=="DEMO" else "https://api.ig.com/gateway/deal"
        self.session = requests.Session()
        self.headers = {
            "X-IG-API-KEY": self.api_key,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8"
        }
        self._login()

    def _login(self):
        """登入 IG API"""
        url = self.base_url + "/session"
        payload = {"identifier": self.username, "password": self.password}
        response = self.session.post(url, json=payload, headers=self.headers)
        if response.status_code != 200:
            raise Exception(f"登入失敗：{response.status_code} {response.text}")

        # 更新 headers 加入 token
        self.headers["X-SECURITY-TOKEN"] = response.headers["X-SECURITY-TOKEN"]
        self.headers["CST"] = response.headers["CST"]

        account_info = response.json()
        self.account_id = account_info["accounts"][0]["accountId"]
        print(f"✅ 登入成功，帳號 ID：{self.account_id}")

    def place_order(self, epic, direction, size=1, order_type="MARKET"):
        """下新單"""
        url = self.base_url + "/positions/otc"
        payload = {
            "epic": epic,
            "direction": direction.upper(),
            "size": size,
            "orderType": order_type,
            "currencyCode": "USD",
            "forceOpen": True,          # 新單開倉
            "guaranteedStop": False,
            "timeInForce": "FILL_OR_KILL",
            "dealReference": "tv_auto_order",
            "expiry": "-"
        }
        headers = self.headers.copy()
        headers["Version"] = "2"
        response = self.session.post(url, json=payload, headers=headers)
        if response.status_code not in [200, 201]:
            print(f"❌ 下單失敗：{response.status_code} {response.text}")
        else:
            print("✅ 成功下單：", response.json())

    def get_open_position(self, epic):
        """回傳該 EPIC 的持倉資訊，沒有則回 None"""
        url = self.base_url + "/positions"
        headers = self.headers.copy()
        headers["Version"] = "2"
        response = self.session.get(url, headers=headers)
        if response.status_code != 200:
            print("❌ 查詢持倉失敗：", response.text)
            return None

        positions = response.json().get("positions", [])
        for pos in positions:
            if pos["market"]["epic"] == epic:
                return {
                    "direction": pos["position"]["direction"],  # "BUY" 或 "SELL"
                    "size": pos["position"]["dealSize"],
                    "dealId": pos["position"]["dealId"]
                }
        return None

    def close_position(self, epic, direction, size, deal_reference="tv_close"):
        """平倉"""
        url = self.base_url + "/positions/otc"
        payload = {
            "epic": epic,
            "direction": direction.upper(),
            "size": size,
            "orderType": "MARKET",
            "currencyCode": "USD",
            "forceOpen": False,       # ⚠️ 關鍵，反向單會平倉
            "guaranteedStop": False,
            "timeInForce": "FILL_OR_KILL",
            "dealReference": deal_reference,
            "expiry": "-"
        }
        headers = self.headers.copy()
        headers["Version"] = "2"
        response = self.session.post(url, json=payload, headers=headers)
        if response.status_code not in [200, 201]:
            print(f"❌ 平倉失敗：{response.status_code} {response.text}")
        else:
            print("✅ 已平倉：", response.json())
