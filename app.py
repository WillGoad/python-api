import os
from flask import Flask, request, jsonify
import psycopg2.pool
from math import gcd

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
        conn.autocommit = False 
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                COALESCE(b.amount, 0) - COALESCE(SUM(o.amount_to_sell), 0) as available
            FROM bank b
            LEFT JOIN orders o 
                ON o.uuid = b.uuid 
                AND o.world = b.world 
                AND o.item_to_sell = b.item
                AND o.status != 'filled'
            WHERE b.uuid = %s
                AND b.world = %s
                AND b.item = %s
            GROUP BY b.amount
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
        conn.rollback()
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

@app.route('/exchange/order_book', methods=['GET'])
def get_order_book():
    item_to_sell = request.args.get('item_to_sell')
    item_to_buy = request.args.get('item_to_buy')
    
    try:
        conn = postgres_pool.getconn()
        cursor = conn.cursor()
        
        # Get asks (selling item_to_buy for item_to_sell)
        cursor.execute("""
            SELECT amount_to_sell, amount_to_buy, amount_filled 
            FROM orders 
            WHERE item_to_buy = %s AND item_to_sell = %s AND status != 'filled'
            ORDER BY (amount_to_sell::FLOAT / amount_to_buy) ASC
        """, (item_to_buy, item_to_sell))
        asks = cursor.fetchall()
        
        # Get bids (buying item_to_buy with item_to_sell)
        cursor.execute("""
            SELECT amount_to_sell, amount_to_buy, amount_filled 
            FROM orders 
            WHERE item_to_buy = %s AND item_to_sell = %s AND status != 'filled'
            ORDER BY (amount_to_sell::FLOAT / amount_to_buy) DESC
        """, (item_to_sell, item_to_buy))
        bids = cursor.fetchall()
        
        return jsonify({
            "asks": [{"sell": a[0], "buy": a[1], "filled": a[2]} for a in asks],
            "bids": [{"sell": b[0], "buy": b[1], "filled": b[2]} for b in bids]
        })
    finally:
        cursor.close()
        postgres_pool.putconn(conn)

@app.route('/exchange/trade', methods=['POST'])
def create_trade():
    data = request.json
    conn = None
    try:
        conn = postgres_pool.getconn()
        cursor = conn.cursor()
        conn.autocommit = False
        
        # 1. Validate new order ratio
        order_gcd = gcd(data['buy_amount'], data['sell_amount'])
        if order_gcd == 0:
            return jsonify({"success": False, "error": "Invalid trade amounts"}), 400

        # 1. Verify seller balance (including existing reservations)
        cursor.execute("""
            SELECT 
                COALESCE(b.amount, 0) - COALESCE(SUM(o.amount_to_sell), 0) as available
            FROM bank b
            LEFT JOIN orders o 
                ON o.uuid = b.uuid 
                AND o.world = b.world 
                AND o.item_to_sell = b.item
                AND o.status != 'filled'
            WHERE b.uuid = %s AND b.world = %s AND b.item = %s
            GROUP BY b.amount
        """, (data['uuid'], data['world'], data['sell_item']))
        
        available = cursor.fetchone()[0] or 0
        if available < data['sell_amount']:
            return jsonify({
                "success": False,
                "error": f"Insufficient {data['sell_item']} (available: {available})"
            }), 400

        # 2. Create new order
        cursor.execute("""
            INSERT INTO orders 
            (uuid, world, item_to_buy, item_to_sell, amount_to_buy, amount_to_sell)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (data['uuid'], data['world'], data['buy_item'], 
             data['sell_item'], data['buy_amount'], data['sell_amount']))
        new_order_id = cursor.fetchone()[0]

        # 3. Find matching orders (best price first)
        cursor.execute("""
            SELECT id, uuid, amount_to_buy, amount_to_sell, amount_filled
            FROM orders
            WHERE item_to_buy = %s
                AND item_to_sell = %s
                AND status != 'filled'
                AND uuid != %s
                AND (amount_to_sell * %s <= %s * amount_to_buy)  -- Match price check
            ORDER BY (amount_to_sell::FLOAT / amount_to_buy) ASC  -- Best price first
            FOR UPDATE
        """, (data['sell_item'], data['buy_item'], data['uuid'],
             data['buy_amount'], data['sell_amount']))
        
        matches = cursor.fetchall()
        remaining = data['buy_amount']  # How much we still need to buy

        # 4. Process matches
        total_filled = 0
        for (order_id, seller_uuid, match_buy, match_sell, match_filled) in matches:
            if remaining <= 0:
                break
            
            # Calculate GCD-based trade unit
            ratio_gcd = gcd(match_buy, match_sell)
            if ratio_gcd == 0:
                continue
            
            unit_buy = match_buy // ratio_gcd  # 7 → 7
            unit_sell = match_sell // ratio_gcd  # 13 → 13
            
            match_remaining = match_buy - match_filled
            max_units_match = match_remaining // unit_buy
            max_units_buyer = remaining // unit_buy
            max_units_seller = (data['sell_amount'] - total_filled) // unit_sell

            max_units = min(max_units_match, max_units_buyer, max_units_seller)
            
            if max_units < 1:
                continue

            actual_fill = unit_buy * max_units
            actual_sell = unit_sell * max_units
            
            # 5. Execute the trade            

            # Update buyer's bank (add bought items)
            cursor.execute("""
                INSERT INTO bank (uuid, world, item, amount)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (uuid, world, item) DO UPDATE
                SET amount = bank.amount + EXCLUDED.amount
            """, (data['uuid'], data['world'], data['buy_item'], actual_fill))

            # Update seller's bank (add their sell amount)
            cursor.execute("""
                INSERT INTO bank (uuid, world, item, amount)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (uuid, world, item) DO UPDATE
                SET amount = bank.amount + EXCLUDED.amount
            """, (seller_uuid, data['world'], data['sell_item'], actual_sell))

            # Deduct from both parties' sell items
            cursor.execute("""
                UPDATE bank
                SET amount = amount - %s
                WHERE uuid = %s AND world = %s AND item = %s
            """, (actual_fill, data['uuid'], data['world'], data['sell_item']))

            cursor.execute("""
                UPDATE bank
                SET amount = amount - %s
                WHERE uuid = %s AND world = %s AND item = %s
            """, (actual_sell, seller_uuid, data['world'], data['buy_item']))

            # 6. Update order statuses
            # For matched order
            cursor.execute("""
                UPDATE orders
                SET amount_filled = amount_filled + %s
                WHERE id = %s
            """, (actual_fill, order_id))

            # For our new order
            total_filled += actual_fill
            remaining -= actual_fill

        # Update status for both orders
            for oid in [order_id, new_order_id]:
                cursor.execute("""
                    UPDATE orders
                    SET status = CASE
                        WHEN amount_filled >= amount_to_buy THEN 'filled'
                        WHEN amount_filled > 0 THEN 'partial'
                        ELSE 'unfilled'
                    END
                    WHERE id = %s
                """, (oid,))

        conn.commit()
        return jsonify({
            "success": True,
            "filled": total_filled,
            "remaining": data['buy_amount'] - total_filled
        })

    except Exception as e: 
        print(f"Error: {e} ")
        if conn: conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn: postgres_pool.putconn(conn)

@app.route('/exchange/orders', methods=['GET'])
def get_user_orders():
    world = request.args.get('world')
    target_item = request.args.get('item')
    
    try:
        conn = postgres_pool.getconn()
        cursor = conn.cursor()
        
        # Get all active orders for this user involving the target item
        cursor.execute("""
            SELECT 
                item_to_buy, 
                item_to_sell, 
                amount_to_buy, 
                amount_to_sell,
                amount_filled,
                status
            FROM orders
            WHERE world = %s
            AND (item_to_buy = %s OR item_to_sell = %s)
            AND status != 'filled'
        """, (world, target_item, target_item))
        
        orders = cursor.fetchall()
        
        asks = []
        bids = []
        
        for order in orders:
            item_to_buy, item_to_sell, amt_buy, amt_sell, filled, status = order
            
            if item_to_sell == target_item:
                # This is an ASK (selling target item)
                asks.append({
                    "other_item": item_to_buy,
                    "amount_other": amt_buy,
                    "amount_target": amt_sell,
                    "price": round(amt_buy / amt_sell, 2),
                    "filled": filled,
                    "status": status
                })
            elif item_to_buy == target_item:
                # This is a BID (buying target item)
                bids.append({
                    "other_item": item_to_sell,
                    "amount_other": amt_sell,
                    "amount_target": amt_buy,
                    "price": round(amt_sell / amt_buy, 2),
                    "filled": filled,
                    "status": status
                })
        
        return jsonify({
            "asks": asks,
            "bids": bids
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cursor.close()
        postgres_pool.putconn(conn)

def update_order_status(order_id):
    try:
        conn = postgres_pool.getconn()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE orders SET
                status = CASE
                    WHEN amount_filled >= amount_to_buy THEN 'filled'
                    WHEN amount_filled > 0 THEN 'partial'
                    ELSE 'unfilled'
                END,
                updated_at = NOW()
            WHERE id = %s
        """, (order_id,))
        
        conn.commit()
    finally:
        cursor.close()
        postgres_pool.putconn(conn)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
