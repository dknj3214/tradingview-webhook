@app.route("/webhook", methods=["POST"])
def api_webhook():
    try:
        data = request.get_json(force=True)
        print("收到 Webhook:", data)

        mode = data.get("mode")
        epic = data.get("epic")
        size = data.get("size", None)
        deal_id = data.get("dealId")

        if mode == "order":
            direction = data.get("direction")
            if not epic or not direction:
                return jsonify({"error": "epic 和 direction 都要提供"}), 400
            result = trader.place_order(epic, direction, size or 1)

        elif mode == "close":
            if not epic and not deal_id:
                return jsonify({"error": "epic 或 dealId 至少要有一個"}), 400
            result = trader.close_position(deal_id, epic, size)

        elif mode == "positions":
            result = trader.get_positions()

        else:
            return jsonify({"error": "未知的 mode"}), 400

        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
