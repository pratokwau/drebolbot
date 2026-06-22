import sqlite3

class Database:
    def __init__(self, db_file):
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS admin_config 
                               (id INTEGER PRIMARY KEY, gk TEXT, ua TEXT)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS orders_data 
                               (order_id TEXT PRIMARY KEY, prime_cost REAL)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS profits 
                               (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                user_id INTEGER NOT NULL,
                                type TEXT,
                                buy_price REAL,
                                sell_price REAL,
                                profit REAL,
                                date TEXT)''')
        self.conn.commit()

    def get_config(self):
        self.cursor.execute("SELECT gk, ua FROM admin_config WHERE id = 1")
        res = self.cursor.fetchone()
        return res if res else (None, None)

    def update_config(self, gk=None, ua=None):
        current_gk, current_ua = self.get_config()
        new_gk = gk if gk is not None else current_gk
        new_ua = ua if ua is not None else current_ua
        self.cursor.execute("INSERT OR REPLACE INTO admin_config (id, gk, ua) VALUES (1, ?, ?)", (new_gk, new_ua))
        self.conn.commit()

    def get_prime_cost(self, order_id):
        self.cursor.execute("SELECT prime_cost FROM orders_data WHERE order_id = ?", (order_id,))
        res = self.cursor.fetchone()
        return res[0] if res else None

    def set_prime_cost(self, order_id, cost):
        self.cursor.execute("INSERT OR REPLACE INTO orders_data (order_id, prime_cost) VALUES (?, ?)", (order_id, cost))
        self.conn.commit()

    def load_profits(self, user_id):
        self.cursor.execute(
            "SELECT type, buy_price, sell_price, profit, date FROM profits WHERE user_id = ? ORDER BY id ASC",
            (user_id,)
        )
        rows = self.cursor.fetchall()
        return [
            {
                "type": row[0],
                "buy_price": row[1] or 0,
                "sell_price": row[2] or 0,
                "profit": row[3] or 0,
                "date": row[4] or "",
            }
            for row in rows
        ]

    def save_profits(self, user_id, profits):
        self.cursor.execute("DELETE FROM profits WHERE user_id = ?", (user_id,))
        self.cursor.executemany(
            "INSERT INTO profits (user_id, type, buy_price, sell_price, profit, date) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    user_id,
                    p.get("type", ""),
                    p.get("buy_price", 0),
                    p.get("sell_price", 0),
                    p.get("profit", 0),
                    p.get("date", ""),
                )
                for p in profits
            ]
        )
        self.conn.commit()

db = Database("funpay_admin.db")
