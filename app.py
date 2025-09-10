def calculate_size(self, entry, stop_loss):
    entry = float(entry)
    stop_loss = float(stop_loss)

    # === 固定參數 ===
    pip_value_per_lot = 10
    risk_percent = 0.01  # 1%風控
    margin_rate = 0.005  # 保證金率 0.5%

    equity = float(self.account_info.get("balance", 10000))
    available_margin = self.available_funds

    # 計算風控允許虧損金額
    risk_amount = equity * risk_percent

    # 計算止損距離 (點數)
    pip_count = abs(entry - stop_loss) * 10000
    pip_count = pip_count if pip_count != 0 else 1  # 防止除以 0

    # === 風控計算出的 size ===
    size_by_risk = (risk_amount) / (pip_count * pip_value_per_lot)

    # === 依據可用保證金算出的最大 size ===
    size_by_margin = available_margin / (entry * margin_rate)

    # 取兩者中較小值
    final_size = min(size_by_risk, size_by_margin)

    print(f"[倉位計算] risk_amount: {risk_amount:.2f}, pip_count: {pip_count},")
    print(f"size_by_risk: {size_by_risk:.2f}, size_by_margin: {size_by_margin:.2f}, 最終 size: {final_size:.2f}")

    return round(final_size, 2)
