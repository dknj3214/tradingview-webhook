try:
    # -----------------------------
    # 查詢現有持倉
    # -----------------------------
    positions = ig.get_positions()
    current_pos = None
    for pos in positions:
        if pos["market"]["epic"] == epic:
            current_pos = pos["position"]
            break

    # -----------------------------
    # 平倉邏輯：若持倉方向與訊號相反
    # -----------------------------
    if current_pos:
        pos_dir = current_pos["direction"]  # "BUY" 或 "SELL"
        pos_size = round(float(current_pos.get("size", 0)), 2)
        deal_id = current_pos["dealId"]

        if (pos_dir == "BUY" and action == "sell") or (pos_dir == "SELL" and action == "buy"):
            print(f"🛑 平倉 {epic}, dealId={deal_id}, size={pos_size}")
            ig.close_position(deal_id, size=pos_size, direction=pos_dir)
            print("✅ 已平倉，Webhook 結束")
            return "Closed", 200

    # -----------------------------
    # 沒有持倉 → 開新單
    # -----------------------------
    if not current_pos:
        print(f"📦 下單資訊: EPIC={epic}, direction={action.upper()}, size={size}")
        if action == "buy":
            ig.place_order(epic, direction="BUY", size=size)
        elif action == "sell":
            ig.place_order(epic, direction="SELL", size=size)

except Exception as e:
    print(f"❌ webhook 執行錯誤：{e}")
    return f"Error: {e}", 500
