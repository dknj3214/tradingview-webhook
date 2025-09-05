from flask import Flask, request

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    print("收到 TradingView 訊號：", data)

    action = data.get("action")
    if action == "buy":
        print("執行買進操作")
    elif action == "sell":
        print("執行賣出操作")
    else:
        print("未知動作")

    return 'OK'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)  # Render 使用 port 10000
