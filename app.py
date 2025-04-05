import os
from flask import Flask, request, jsonify
import psycopg2.pool

app = Flask(__name__)

# PostgreSQL Connection Pool
postgres_pool = psycopg2.pool.SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    user=os.getenv('PGUSER'),
    password=os.getenv('PGPASSWORD'),
    host=os.getenv('PGHOST'),
    port=os.getenv('PGPORT'),
    database=os.getenv('PGDATABASE')
)


# Dummy in-memory data store
bank_data = {}

@app.route('/bank/deposit', methods=['POST'])
def deposit():
    data = request.json
    uuid = data['uuid']
    world = data['world']
    item = data['item']
    amount = data['amount']
    
    try:
        conn = postgres_pool.getconn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO bank (uuid, world, item, amount)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (uuid, world, item) DO UPDATE
            SET amount = bank.amount + EXCLUDED.amount
        """, (uuid, world, item, amount))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        print(f"[DEPOSIT] {uuid} deposited {amount}x {item}")
        cursor.close()
        postgres_pool.putconn(conn)

@app.route('/bank/withdraw', methods=['POST'])
def withdraw():
    data = request.json
    uuid = data['uuid']
    world = data['world']
    item = data['item']
    amount = data['amount']
    
    try:
        conn = postgres_pool.getconn()
        cursor = conn.cursor()
        
        # Check current balance
        cursor.execute("""
            SELECT amount FROM bank
            WHERE uuid = %s AND world = %s AND item = %s
        """, (uuid, world, item))
        result = cursor.fetchone()
        
        if not result or result[0] < amount:
            return jsonify({"success": False}), 400
        
        # Update balance
        cursor.execute("""
            UPDATE bank SET amount = amount - %s
            WHERE uuid = %s AND world = %s AND item = %s
        """, (amount, uuid, world, item))
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        print(f"[WITHDRAW] {uuid} withdraws {amount}x {item}")
        cursor.close()
        postgres_pool.putconn(conn)

@app.route('/bank/balance')
def balance():
    uuid = request.args.get('uuid')
    world = request.args.get('world')
    
    try:
        conn = postgres_pool.getconn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT item, amount FROM bank
            WHERE uuid = %s AND world = %s
        """, (uuid, world))
        items = cursor.fetchall()
        lines = [f"{item}: {amount}" for item, amount in items]
        return "\n".join(lines)
    except Exception as e:
        return str(e), 500
    finally:
        print(f"[BALANCE] Request from {uuid}")
        cursor.close()
        postgres_pool.putconn(conn)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
