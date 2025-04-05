import os

from flask import Flask, request, jsonify

app = Flask(__name__)

# Dummy in-memory data store
bank_data = {}

@app.route('/bank/deposit', methods=['POST'])
def deposit():
    data = request.json
    uuid = data['uuid']
    item = data['item']
    amount = data['amount']

    print(f"[DEPOSIT] {uuid} deposited {amount}x {item}")

    if uuid not in bank_data:
        bank_data[uuid] = {}

    bank_data[uuid][item] = bank_data[uuid].get(item, 0) + amount
    return jsonify({"success": True})

@app.route('/bank/withdraw', methods=['POST'])
def withdraw():
    data = request.json
    uuid = data['uuid']
    item = data['item']
    amount = data['amount']

    print(f"[WITHDRAW] {uuid} tries to withdraw {amount}x {item}")

    if uuid not in bank_data or bank_data[uuid].get(item, 0) < amount:
        return jsonify({"success": False, "error": "Not enough items"}), 400

    bank_data[uuid][item] -= amount
    return jsonify({"success": True})

@app.route('/bank/balance')
def balance():
    uuid = request.args.get('uuid')
    print(f"[BALANCE] Request from {uuid}")

    items = bank_data.get(uuid, {})
    lines = [f"{item}: {amount}" for item, amount in items.items()]
    return "\n".join(lines)  # matches the plugin's line-by-line reading

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
