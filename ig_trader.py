import requests

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
        """登入 IG API"""
        url = self.base_url + "/session"
        payload = {
            "identifier": self.username,
            "password": self.password
        }
        resp = self.session.post(url, json=payload, headers=self.headers)
        if resp.status_code != 200:
            raise Exception(f"登入失敗：{resp.status_code} {resp.text}")

        self.headers["X-SECURITY-TOKEN"] = resp.headers["X-SECURITY-TOKEN"]
        self.headers["CST"] = resp.headers["CST"]

        account_info = resp.json()
        self.account_id = account_info["accounts"][0]["accountId"]
        print(f"✅ 登入成功，帳號 ID：{self.account_id}")

    def place_order(self, epic, direction, size=1, order_type="MARKET"):
        """下單"""
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
        """取得所有持倉"""
        url = self.base_url + "/positions"
        headers = self.headers.copy()
        headers["Version"] = "1"
        resp = self.session.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"查詢持倉失敗：{resp.status_code} {resp.text}")
        return resp.json()["positions"]

    def close_position(self, deal_id, size, direction):
        """平倉 OTC CFD"""
        url = self.base_url + "/positions/otc"
        payload = {
            "dealId": deal_id,
            "size": size,
            "direction": direction.upper(),  # 與現有倉位方向一致
            "orderType": "MARKET",
            "forceOpen": False,              # 平倉一定要 False
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
        """查詢單一商品的規格 (最小下單單位等)"""
        url = self.base_url + f"/markets/{epic}"
        headers = self.headers.copy()
        headers["Version"] = "3"
        resp = self.session.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"查詢商品資訊失敗：{resp.status_code} {resp.text}")
        return resp.json()

