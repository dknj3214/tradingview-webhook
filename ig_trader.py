import requests

class IGTrader:
    def __init__(self, api_key, username, password, account_type="DEMO"):
        self.api_key = api_key
        self.username = username
        self.password = password
        self.account_type = account_type.upper()
        self.base_url = "https://demo-api.ig.com/gateway/deal" if account_type == "DEMO" else "https://api.ig.com/gateway/deal"
        self.session = requests.Session()
        self.headers = {
            "X-IG-API-KEY": self.api_key,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8"
        }
        self._login()

    def _login(self):
        url = self.base_url + "/session"
        payload = {
            "identifier": self.username,
            "password": self.password
        }

        response = self.session.post(url, json=payload, headers=self.headers)
        if response.status_code != 200:
            raise Exception("登入失敗：" + response.text)

        # 更新 headers 加入 session token
        self.headers["X-SECURITY-TOKEN"] = response.headers["X-SECURITY-TOKEN"]
        self.headers["CST"] = response.headers["CST"]

        account_info = response.json()
        self.account_id = account_info["accounts"][0]["accountId"]

    def place_order(self, epic, direction, size=1, order_type="MARKET"):
        url = self.base_url + "/positions/otc"

        payload = {
            "epic": epic,
            "direction": direction.upper(),  # "BUY" or "SELL"
            "size": size,
            "orderType": order_type,
            "currencyCode": "USD",
            "forceOpen": True,
            "guaranteedStop": False,
            "timeInForce": "FILL_OR_KILL",
            "dealReference": "tv_auto_order"
        }

        headers = self.headers.copy()
        headers["Version"] = "2"

        response = self.session.post(url, json=payload, headers=headers)
        if response.status_code not in [200, 201]:
            print("下單失敗：", response.text)
        else:
            print("✅ 成功下單：", response.json())
