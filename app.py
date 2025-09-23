import os
import string
import time
import threading
import logging
import requests
from flask import Flask, request, jsonify

# ----------------------
# 基本檢查與日誌記錄
# ----------------------
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")

def check_ascii(s, name):
    if not all(c in string.printable for c in s):
        raise ValueError(f"{name} 不能含非 ASCII 字元")

check_ascii(os.environ["IG_API_KEY"], "IG_API_KEY")
check_ascii(os.environ["IG_USERNAME"], "IG_USERNAME")
check_ascii(os.environ["IG_PASSWORD"], "IG_PASSWORD")

# ----------------------
# 價格快取
# ----------------------
class PriceCache:
    """執行緒安全的快取，用於儲存每個 epic 的最新 BID/OFFER 報價。"""
    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}  # epic -> {"bid": float, "offer": float, "ts": float}

    def set_price(self, epic, bid, offer):
        with self._lock:
            self._data[epic] = {"bid": float(bid), "offer": float(offer), "ts": time.time()}
            logging.debug(f"PriceCache 設定 {epic}: bid={bid} offer={offer}")

    def get_price(self, epic, max_age=3.0):
        """如果快取存在且時間在 max_age 秒內，則回傳 (bid, offer)，否則回傳 None。"""
        with self._lock:
            rec = self._data.get(epic)
            if not rec:
                return None
            if time.time() - rec["ts"] > max_age:
                return None
            return rec["bid"], rec["offer"]

    def get_spread_pips(self, epic, pip_factor=10000, max_age=3.0):
        p = self.get_price(epic, max_age=max_age)
        if not p:
            return None
        bid, offer = p
        return max((offer - bid) * pip_factor, 0.0)

# ----------------------
# IG streaming 管理器 (使用 trading-ig)
# ----------------------
class IGStreamer:
    """通過 trading-ig 管理後台 Lightstreamer 連接。"""
    def __init__(self, ig_service, price_cache):
        self.ig_service = ig_service
        self.price_cache = price_cache
        self._subscribed = set()     # epic 的集合 (例如 'CS.D.GBPUSD.CFD.IP')
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop = threading.Event()
        self._ls_client = None

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=5)

    def subscribe_epic(self, epic):
        with self._lock:
            if epic in self._subscribed:
                return
            self._subscribed.add(epic)
            logging.info(f"Streamer: 已排隊訂閱 {epic}")
            if self._ls_client is not None:
                try:
                    self._do_subscribe(epic)
                except Exception:
                    logging.exception("立即訂閱失敗；將從執行循環重試")

    def _do_subscribe(self, epic):
        from trading_ig.lightstreamer import Subscription

        item_name = f"L1:{epic}"

        def make_listener(epic_key):
            def on_update(item):
                values = item.get("values", {})
                try:
                    bid = float(values.get("BID", 0) or 0)
                    offer = float(values.get("OFFER", 0) or 0)
                    self.price_cache.set_price(epic_key, bid, offer)
                except Exception:
                    logging.exception("解析 %s 的 streaming 更新時出錯", epic_key)

            return on_update

        sub = Subscription(mode="MERGE", items=[item_name], fields=["UPDATE_TIME", "BID", "OFFER"]) 
        sub.addlistener(make_listener(epic))
        logging.info(f"Streamer: 訂閱 {item_name}")
        self._ls_client.subscribe(sub)

    def _run(self):
        from trading_ig import IGStreamService

        backoff = 1
        while not self._stop.is_set():
            try:
                logging.info("Streamer: 建立 streaming 會話")
                stream_service = IGStreamService(self.ig_service)
                stream_service.create_session()  
                self._ls_client = stream_service.ls_client
                logging.info("Streamer: 已連接到 Lightstreamer")

                with self._lock:
                    to_sub = list(self._subscribed)
                for epic in to_sub:
                    try:
                        self._do_subscribe(epic)
                    except Exception:
                        logging.exception("為 %s 初始訂閱失敗", epic)

                while not self._stop.wait(1):
                    pass

                try:
                    stream_service.disconnect()
                except Exception:
                    logging.exception("斷開 stream_service 時出錯")
                logging.info("Streamer: 依請求停止")
                return

            except Exception:
                logging.exception("Streamer: 連接錯誤 - 將重試")
                self._ls_client = None
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue


# ----------------------
# IGTrader (REST) 與 streaming 整合
# ----------------------
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
        self.price_cache = PriceCache()
        self.streamer = None
        self.account_info = None
        self._login()

    def _login(self):
        url = self.base_url + "/session"
        payload = {"identifier": self.username, "password": self.password}
        resp = self.session.post(url, json=payload, headers=self.headers)

        if resp.status_code != 200:
            raise Exception(f"登入失敗：{resp.status_code} {resp.text}")

        logging.info("登入回應資料: %s", resp.json())

        accounts = resp.json().get("accounts", [])
        if accounts:
            self.account_id = accounts[0]["accountId"]
            self.account_info = resp.json().get("accountInfo")
            logging.info("帳戶 ID: %s", self.account_id)
        else:
            raise Exception("無法找到帳戶資料")

        self.headers["X-SECURITY-TOKEN"] = resp.headers.get("X-SECURITY-TOKEN")
        self.headers["CST"] = resp.headers.get("CST")
        logging.info("登入成功，帳戶 ID: %s", self.account_id)

        if self.account_info:
            self.available_funds = float(self.account_info.get("available", 0))
            logging.info(f"[登入] 可用保證金 available_funds: {self.available_funds}")

        # 初始化 streaming 服务
        try:
            from trading_ig import IGService

            ig_stream_service = IGService(self.username, self.password, self.api_key, self.account_type)
            self.streamer = IGStreamer(ig_stream_service, self.price_cache)
            self.streamer.start()
            logging.info("IGTrader: 已啟動 streamer 後台執行緒")
        except Exception:
            logging.exception("IGTrader: 無法啟動 streaming (可能缺少 trading-ig)。Streaming 將被禁用。")
            self.streamer = None

    def refresh_session(self):
        """刷新會話，獲取最新的 token。"""
        self._login()

    def get_headers(self):
        """檢查會話是否過期，如果過期則刷新 session。"""
        if not self.account_info:
            logging.error("Session 無效，需要重新登入。")
            self.refresh_session()
        return self.headers

        headers["Version"] = "2"
        resp = self.session.get(url, headers=headers)
        if resp.status_code == 401:
            logging.warning("Session 過期，正在重新登入...")
            self.refresh_session()
            return self.get_positions()
        elif resp.status_code != 200:
            raise Exception(f"查詢持倉失敗：{resp.status_code} {resp.text}")
        return resp.json().get("positions", [])

    def get_account_info(self):
        return self.account_info

    def get_spread_rest(self, epic, pip_factor=10000):
        url = self.base_url + "/prices"
        params = {"epics": epic}
        headers = self.get_headers()
        headers["Version"] = "3"
        try:
            resp = self.session.get(url, headers=headers, params=params, timeout=5)
            if resp.status_code == 401:
                self.refresh_session()
                return self.get_spread_rest(epic, pip_factor)
            if resp.status_code != 200:
                logging.error("[錯誤] 取得價格失敗: %s %s", resp.status_code, resp.text)
                return None
            data = resp.json()
            prices = data.get("prices", [])
            if not prices:
                return None
            bid = float(prices[0].get("bid", 0))
            offer = float(prices[0].get("offer", 0))
            return max((offer - bid) * pip_factor, 0.0)
        except requests.RequestException:
            logging.exception("REST 價格請求失敗")
            return None

    def get_spread(self, epic, pip_factor=10000):
        if self.streamer:
            self.streamer.subscribe_epic(epic)

        spread = self.price_cache.get_spread_pips(epic, pip_factor=pip_factor, max_age=3.0)
        if spread is not None:
            return spread
        return self.get_spread_rest(epic, pip_factor)

    def calculate_size(self, entry, stop_loss, epic=None):
        try:
            entry = float(entry)
            stop_loss = float(stop_loss)

            if entry == stop_loss:
                raise ValueError("Entry price and stop loss cannot be the same.")

            pip_factor = 10000
            pip_value = 10  # 1 lot 每點價值 $10
            risk_percent = 0.01
            equity = float(self.account_info.get("available", 0))
            risk_amount = equity * risk_percent

            pip_distance = max(abs(entry - stop_loss) * pip_factor, 1)
            spread_pips = self.get_spread(epic, pip_factor) if epic else 0
            effective_pip = pip_distance + spread_pips
            size = risk_amount / (effective_pip * pip_value)
            return round(max(size, 0.0), 2)
        except Exception:
            logging.exception("計算倉位失敗")
            return 0.0

    def place_order(self, epic, direction, size=1, order_type="MARKET"):
        if size <= 0:
            return {"error": "倉位為 0，未送出訂單", "status_code": 400}

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
        headers = self.get_headers()
        headers["Version"] = "2"
        resp = self.session.post(url, json=payload, headers=headers, timeout=10)

        if resp.status_code == 401:
            self.refresh_session()
            return self.place_order(epic, direction, size, order_type)

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
            size = target["position"]["dealSize"]
            direction = target["position"]["direction"].upper()
            opposite = "SELL" if direction == "BUY" else "BUY"
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
            headers = self.get_headers()
            headers["Version"] = "2"
            resp = self.session.post(url, json=payload, headers=headers)
            if resp.status_code == 401:
                self.refresh_session()
                return self.close_position(epic)
            if resp.status_code not in [200, 201]:
                results.append({"dealId": deal_id, "error": resp.text})
            else:
                results.append(resp.json())
        return results

