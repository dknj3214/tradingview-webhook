"""
Flask + IG REST (requests) + IG Streaming (trading-ig)

新增功能與原腳本比較：
  - 使用 trading-ig 的 streaming client (Lightstreamer) 接收即時 BID/OFFER 報價
    並在記憶體中快取。
  - calculate_size() 首先嘗試使用快取的 streaming 價格計算點差（更快，避免 REST 呼叫和頻率限制）。
    如果沒有最近的 streaming 價格，則回退到 REST 價格端點。
  - 動態訂閱：當 webhook 引用某個 epic 時，我們按需訂閱它，這樣我們只 streaming 需要的內容。
  - 更安全的行為：我們*不*發送倉位大小 <= 0 的市價單；而是回傳錯誤並記錄原因。

依賴項：
  pip install trading-ig flask requests
  (trading-ig 依賴於 Lightstreamer 客戶端；安裝說明請參閱
   trading-ig 文件：https://trading-ig.readthedocs.io)

簡要工作原理：
  - 啟動時，模組登入 REST API（保持您的 requests.Session）
    並同時建立一個僅用於 streaming 的 IGService 實例。
  - 一個後台執行緒管理 Lightstreamer 連接並執行訂閱。即時價格被寫入 PriceCache。
  - calculate_size() 首先詢問 PriceCache；如果不可用，則回退呼叫原始的 REST /prices 端點。

注意事項 / 警告：
  - Streaming 使用 Lightstreamer，偶爾演示環境可能不穩定。預期會有重新連接；streamer 有一個簡單的重試/退避循環。
  - 確保您的 IG API 金鑰、使用者名稱、密碼和（可選）帳戶類型
    像之前一樣設定在環境變數中。

"""

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
    """通過 trading-ig 管理後台 Lightstreamer 連接。

    用法：
        streamer = IGStreamer(ig_service, price_cache)
        streamer.start()
        streamer.subscribe_epic('CS.D.GBPUSD.CFD.IP')

    此類在失敗時會嘗試重新連接，並帶有小的退避時間。
    """
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
        """註冊對某個 epic 的興趣；將 (a) 添加到集合中，並且 (b) 如果已連接，
        則嘗試立即訂閱。
        """
        with self._lock:
            if epic in self._subscribed:
                return
            self._subscribed.add(epic)
            logging.info(f"Streamer: 已排隊訂閱 {epic}")
            # 如果已連接，嘗試立即訂閱
            if self._ls_client is not None:
                try:
                    self._do_subscribe(epic)
                except Exception:
                    logging.exception("立即訂閱失敗；將從執行循環重試")

    def _do_subscribe(self, epic):
        """在 Lightstreamer 客戶端上執行訂閱呼叫。
        使用 trading-ig 的 lightstreamer.Subscription 類。
        """
        # 在此處導入以避免如果未使用時產生頂層硬依賴
        from trading_ig.lightstreamer import Subscription

        item_name = f"L1:{epic}"

        def make_listener(epic_key):
            def on_update(item):
                # item 是一個包含 'values' 的字典
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
        """後台循環：連接，訂閱當前集合，然後休眠直到停止。
        發生異常時重新連接，並帶有退避時間。
        """
        from trading_ig import IGStreamService

        backoff = 1
        while not self._stop.is_set():
            try:
                logging.info("Streamer: 建立 streaming 會話")
                stream_service = IGStreamService(self.ig_service)
                stream_service.create_session()  # 協商 Lightstreamer 會話
                self._ls_client = stream_service.ls_client
                logging.info("Streamer: 已連接到 Lightstreamer")

                # 訂閱已請求的 epics
                with self._lock:
                    to_sub = list(self._subscribed)
                for epic in to_sub:
                    try:
                        self._do_subscribe(epic)
                    except Exception:
                        logging.exception("為 %s 初始訂閱失敗", epic)

                # 保持連接存活，直到停止或出錯
                while not self._stop.wait(1):
                    # noop - ls_client 在後台調用監聽器
                    pass

                # 如果因為停止標誌設定而中斷，則斷開連接
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
        # REST 登入 (將 CST & X-SECURITY-TOKEN 保存在 self.headers 中)
        self._login()

        # 價格快取和 streamer (streaming 使用 trading-ig 及其自己的會話)
        self.price_cache = PriceCache()

        try:
            # trading-ig 需要建立一個 IGService 實例；我們僅將其
            # 用於 streaming，因此不會替換您的 REST 程式碼。trading-ig 將
            # 在底層建立自己的會話令牌。
            from trading_ig import IGService

            ig_stream_service = IGService(self.username, self.password, self.api_key, self.account_type)
            # 不要在此處呼叫 create_session()；IGStreamer 會在啟動時執行
            self.streamer = IGStreamer(ig_stream_service, self.price_cache)
            self.streamer.start()
            logging.info("IGTrader: 已啟動 streamer 後台執行緒")
        except Exception:
            logging.exception("IGTrader: 無法啟動 streaming (可能缺少 trading-ig)。Streaming 將被禁用。")
            self.streamer = None

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

        # 複製會話標頭以供後續 REST 呼叫使用
        self.headers["X-SECURITY-TOKEN"] = resp.headers.get("X-SECURITY-TOKEN")
        self.headers["CST"] = resp.headers.get("CST")
        logging.info("登入成功，帳戶 ID: %s", self.account_id)

        self.available_funds = float(self.account_info.get("available", 0))
        logging.info(f"[登入] 可用保證金 available_funds: {self.available_funds}")

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

    def get_spread_rest(self, epic, pip_factor=10000):
        """如果 streaming 價格不可用，則回退到 REST 呼叫。"""
        url = self.base_url + "/prices"
        params = {"epics": epic}
        headers = self.headers.copy()
        headers["Version"] = "3"

        try:
            resp = self.session.get(url, headers=headers, params=params, timeout=5)
            if resp.status_code != 200:
                logging.error("[錯誤] 取得價格失敗: %s %s", resp.status_code, resp.text)
                return None
            data = resp.json()
            prices = data.get("prices", [])
            if not prices:
                logging.error("[錯誤] 找不到價格資料 epic=%s", epic)
                return None
            price_info = prices[0]
            bid = float(price_info.get("bid", 0))
            offer = float(price_info.get("offer", 0))
            spread = max((offer - bid) * pip_factor, 0.0)
            logging.debug(f"[REST 點差] epic={epic} bid={bid} offer={offer} spread={spread}")
            return spread
        except requests.RequestException:
            logging.exception("REST 價格請求失敗")
            return None

    def get_spread(self, epic, pip_factor=10000):
        """先嘗試 streaming 快取，然後回退到 REST。"""
        # 確保 streamer 已訂閱此 epic (非阻塞)
        try:
            if self.streamer:
                self.streamer.subscribe_epic(epic)
        except Exception:
            logging.exception("請求訂閱時出錯")

        # 嘗試快取
        spread = self.price_cache.get_spread_pips(epic, pip_factor=pip_factor, max_age=3.0)
        if spread is not None:
            logging.info(f"[點差 - streaming] epic: {epic}, spread: {spread:.2f} 點")
            return spread

        # 回退到 REST
        rest_spread = self.get_spread_rest(epic, pip_factor=pip_factor)
        if rest_spread is not None:
            logging.info(f"[點差 - REST] epic: {epic}, spread: {rest_spread:.2f} 點")
            return rest_spread

        # 放棄
        logging.warning("[點差] 無法取得 %s 的點差 (streaming+rest 都失敗)", epic)
        return 0.0

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

            logging.info("[倉位計算 - 風控法（含點差）]")
            logging.info(f"  ▶ 本金             : ${equity:.2f}")
            logging.info(f"  ▶ 可承受風險金額   : ${risk_amount:.2f}")
            logging.info(f"  ▶ 止損距離（點）: {pip_distance:.1f}")
            logging.info(f"  ▶ 點差（點）      : {spread_pips:.1f}")
            logging.info(f"  ▶ 有效止損距離(點): {effective_pip_distance:.1f}")
            logging.info(f"  ▶ ✅ 建議倉位大小   : {position_size:.2f} 手")

            # 不回傳負數或零的倉位大小
            if position_size <= 0:
                logging.warning("計算出的倉位大小 <= 0; 中止")
                return 0.0

            return round(position_size, 2)

        except Exception as e:
            logging.exception("[錯誤] 倉位計算失敗: %s", e)
            return 0.0

    def place_order(self, epic, direction, size=1, order_type="MARKET"):
        if size <= 0:
            return {"error": "計算出來的倉位為 0，未送出下單", "status_code": 400}

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
        logging.info(f"[下單] payload: {payload}")
        resp = self.session.post(url, json=payload, headers=headers, timeout=10)
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


# ----------------------
# Flask 應用程式
# ----------------------
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
        logging.info("收到 Webhook raw: %s", raw)

        data = dict(item.split("=") for item in raw.split("&") if "=" in item)
        logging.info("解析後: %s", data)

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
