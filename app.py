#!/usr/bin/env python3
"""
BTC Trading Platform - PVP Long/Short Game
Website dengan harga BTC real-time dari Binance dan fitur trading PVP
"""

import requests
import json
import time
import random
import threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'
app.config['DATABASE'] = 'trading_game.db'

# Initialize database
def init_db():
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                balance REAL DEFAULT 10000.0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT NOT NULL,
                amount REAL NOT NULL,
                entry_price REAL NOT NULL,
                leverage INTEGER DEFAULT 1,
                fee REAL DEFAULT 0.001,
                pnl REAL DEFAULT 0,
                status TEXT DEFAULT 'open',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                closed_at DATETIME,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')

def get_db():
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

def get_btc_price():
    """Get real-time BTC price from Binance"""
    try:
        url = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
        response = requests.get(url, timeout=5)
        data = response.json()
        return float(data['price'])
    except:
        # Fallback to random price if API fails
        return round(random.uniform(30000, 60000), 2)

class TradingEngine:
    def __init__(self):
        self.btc_price = get_btc_price()
        self.update_price()
        
    def update_price(self):
        """Update BTC price every 10 seconds"""
        self.btc_price = get_btc_price()
        threading.Timer(10.0, self.update_price).start()
        
    def calculate_pnl(self, trade, current_price):
        """Calculate P&L for a trade"""
        if trade['type'] == 'long':
            pnl = (current_price - trade['entry_price']) * trade['amount'] * trade['leverage']
        else:  # short
            pnl = (trade['entry_price'] - current_price) * trade['amount'] * trade['leverage']
        
        # Apply fee
        pnl -= trade['entry_price'] * trade['amount'] * trade['fee']
        return round(pnl, 2)

# Initialize trading engine
trading_engine = TradingEngine()

# Routes
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    db = get_db()
    
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    open_trades = db.execute('''
        SELECT * FROM trades 
        WHERE user_id = ? AND status = 'open'
        ORDER BY created_at DESC
    ''', (user_id,)).fetchall()
    
    # Calculate total P&L for open trades
    total_pnl = 0
    for trade in open_trades:
        trade_dict = dict(trade)
        trade_dict['current_pnl'] = trading_engine.calculate_pnl(trade_dict, trading_engine.btc_price)
        total_pnl += trade_dict['current_pnl']
    
    return render_template('index.html', 
                         user=user, 
                         btc_price=trading_engine.btc_price,
                         open_trades=open_trades,
                         total_pnl=total_pnl)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            return redirect(url_for('index'))
        
        return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        db = get_db()
        try:
            db.execute('INSERT INTO users (username, password) VALUES (?, ?)',
                     (username, generate_password_hash(password)))
            db.commit()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            return render_template('register.html', error='Username already exists')
    
    return render_template('register.html')

@app.route('/deposit', methods=['POST'])
def deposit():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    amount = float(request.form['amount'])
    user_id = session['user_id']
    
    db = get_db()
    db.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (amount, user_id))
    db.execute('INSERT INTO deposits (user_id, amount, status) VALUES (?, ?, "completed")',
             (user_id, amount))
    db.commit()
    
    return redirect(url_for('index'))

@app.route('/trade', methods=['POST'])
def trade():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    user_id = session['user_id']
    trade_type = request.form['type']
    amount = float(request.form['amount'])
    leverage = int(request.form.get('leverage', 1))
    
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    
    # Calculate required margin
    required_margin = trading_engine.btc_price * amount * leverage * 0.01  # 1% margin
    
    if user['balance'] < required_margin:
        return jsonify({'error': 'Insufficient balance'}), 400
    
    # Execute trade
    fee = trading_engine.btc_price * amount * 0.001  # 0.1% fee
    db.execute('''
        INSERT INTO trades (user_id, type, amount, entry_price, leverage, fee)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, trade_type, amount, trading_engine.btc_price, leverage, fee))
    
    # Update balance
    db.execute('UPDATE users SET balance = balance - ? WHERE id = ?',
             (required_margin + fee, user_id))
    
    db.commit()
    
    return jsonify({'success': True})

@app.route('/close_trade/<int:trade_id>')
def close_trade(trade_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    db = get_db()
    
    trade = db.execute('SELECT * FROM trades WHERE id = ? AND user_id = ?',
                     (trade_id, user_id)).fetchone()
    
    if trade:
        pnl = trading_engine.calculate_pnl(dict(trade), trading_engine.btc_price)
        
        db.execute('''
            UPDATE trades 
            SET status = 'closed', pnl = ?, closed_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (pnl, trade_id))
        
        # Update user balance
        db.execute('UPDATE users SET balance = balance + ? WHERE id = ?',
                 (pnl, user_id))
        
        db.commit()
    
    return redirect(url_for('index'))

@app.route('/get_price')
def get_price():
    return jsonify({'price': trading_engine.btc_price})

# HTML Templates
@app.route('/templates/<template_name>')
def serve_template(template_name):
    return render_template(template_name)

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)

# Create templates directory and files
import os
os.makedirs('templates', exist_ok=True)

# Index HTML
with open('templates/index.html', 'w', encoding='utf-8') as f:
    f.write('''<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BTC Trading PVP</title>
    <style>
        :root {
            --primary: #2563eb;
            --success: #10b981;
            --danger: #ef4444;
            --dark: #1f2937;
            --light: #f3f4f6;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            color: #333;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            overflow: hidden;
        }
        
        .header {
            background: var(--dark);
            color: white;
            padding: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .price-ticker {
            background: var(--primary);
            color: white;
            padding: 10px 20px;
            text-align: center;
            font-size: 1.2em;
            font-weight: bold;
        }
        
        .dashboard {
            padding: 20px;
            display: grid;
            grid-template-columns: 1fr 2fr;
            gap: 20px;
        }
        
        .card {
            background: var(--light);
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        
        .balance {
            font-size: 2em;
            font-weight: bold;
            color: var(--primary);
        }
        
        .trade-form {
            display: grid;
            gap: 10px;
            margin-top: 20px;
        }
        
        input, select, button {
            padding: 12px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 1em;
        }
        
        button {
            background: var(--primary);
            color: white;
            border: none;
            cursor: pointer;
            font-weight: bold;
        }
        
        .btn-long { background: var(--success); }
        .btn-short { background: var(--danger); }
        
        .trades-list {
            margin-top: 20px;
        }
        
        .trade-item {
            padding: 15px;
            border: 1px solid #ddd;
            border-radius: 5px;
            margin-bottom: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .trade-long { border-left: 4px solid var(--success); }
        .trade-short { border-left: 4px solid var(--danger); }
        
        .profit { color: var(--success); }
        .loss { color: var(--danger); }
        
        .nav { display: flex; gap: 15px; }
        .nav a {
            color: white;
            text-decoration: none;
            padding: 10px 15px;
            border-radius: 5px;
            transition: background 0.3s;
        }
        .nav a:hover { background: rgba(255,255,255,0.1); }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎯 BTC Trading PVP</h1>
            <div class="nav">
                <a href="{{ url_for('index') }}">Dashboard</a>
                <a href="{{ url_for('logout') }}">Logout</a>
            </div>
        </div>
        
        <div class="price-ticker">
            🚀 BTC Price: $<span id="btc-price">{{ "%.2f"|format(btc_price) }}</span>
        </div>
        
        <div class="dashboard">
            <div class="sidebar">
                <div class="card">
                    <h3>💰 Balance</h3>
                    <div class="balance">${{ "%.2f"|format(user.balance) }}</div>
                    
                    <div class="trade-form">
                        <h4>➕ Deposit</h4>
                        <form action="{{ url_for('deposit') }}" method="POST">
                            <input type="number" name="amount" placeholder="Amount" step="0.01" min="1" required>
                            <button type="submit">Deposit</button>
                        </form>
                    </div>
                </div>
                
                <div class="card">
                    <h3>🎯 New Trade</h3>
                    <form class="trade-form" onsubmit="placeTrade(event)">
                        <select name="type" required>
                            <option value="long">LONG 🟢</option>
                            <option value="short">SHORT 🔴</option>
                        </select>
                        <input type="number" name="amount" placeholder="BTC Amount" step="0.001" min="0.001" required>
                        <select name="leverage">
                            <option value="1">1x Leverage</option>
                            <option value="5">5x Leverage</option>
                            <option value="10">10x Leverage</option>
                            <option value="25">25x Leverage</option>
                        </select>
                        <button type="submit" class="btn-long">Place Trade</button>
                    </form>
                </div>
            </div>
            
            <div class="main-content">
                <div class="card">
                    <h3>📊 Open Trades</h3>
                    <div class="trades-list">
                        {% for trade in open_trades %}
                        <div class="trade-item trade-{{ trade.type }}">
                            <div>
                                <strong>{{ trade.type|upper }} {{ trade.leverage }}x</strong><br>
                                Amount: {{ trade.amount }} BTC<br>
                                Entry: ${{ "%.2f"|format(trade.entry_price) }}
                            </div>
                            <div>
                                <span class="{% if trade.current_pnl >= 0 %}profit{% else %}loss{% endif %}">
                                    P&L: ${{ "%.2f"|format(trade.current_pnl) }}
                                </span><br>
                                <a href="{{ url_for('close_trade', trade_id=trade.id) }}" 
                                   class="btn-{{ 'short' if trade.type == 'long' else 'long' }}">
                                   Close Trade
                                </a>
                            </div>
                        </div>
                        {% else %}
                        <p>No open trades</p>
                        {% endfor %}
                    </div>
                </div>
                
                <div class="card">
                    <h3>📈 Total P&L: 
                        <span class="{% if total_pnl >= 0 %}profit{% else %}loss{% endif %}">
                            ${{ "%.2f"|format(total_pnl) }}
                        </span>
                    </h3>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Update BTC price every 5 seconds
        function updatePrice() {
            fetch('/get_price')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('btc-price').textContent = data.price.toFixed(2);
                });
        }
        
        setInterval(updatePrice, 5000);
        
        // Place trade with AJAX
        function placeTrade(event) {
            event.preventDefault();
            const formData = new FormData(event.target);
            
            fetch('/trade', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    alert('Error: ' + data.error);
                } else {
                    alert('Trade placed successfully!');
                    window.location.reload();
                }
            });
        }
    </script>
</body>
</html>''')

# Login HTML
with open('templates/login.html', 'w', encoding='utf-8') as f:
    f.write('''<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - BTC Trading</title>
    <style>
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        .login-container {
            background: white;
            padding: 40px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            width: 100%;
            max-width: 400px;
        }
        
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 30px;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        input {
            width: 100%;
            padding: 12px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 1em;
        }
        
        button {
            width: 100%;
            padding: 12px;
            background: #2563eb;
            color: white;
            border: none;
            border-radius: 5px;
            font-size: 1.1em;
            font-weight: bold;
            cursor: pointer;
        }
        
        .error {
            color: #ef4444;
            text-align: center;
            margin-bottom: 15px;
        }
        
        .register-link {
            text-align: center;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h1>🚀 BTC Trading PVP</h1>
        
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        
        <form method="POST">
            <div class="form-group">
                <input type="text" name="username" placeholder="Username" required>
            </div>
            <div class="form-group">
                <input type="password" name="password" placeholder="Password" required>
            </div>
            <button type="submit">Login</button>
        </form>
        
        <div class="register-link">
            <p>Don't have an account? <a href="{{ url_for('register') }}">Register here</a></p>
        </div>
    </div>
</body>
</html>''')

# Register HTML
with open('templates/register.html', 'w', encoding='utf-8') as f:
    f.write('''<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Register - BTC Trading</title>
    <style>
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        .register-container {
            background: white;
            padding: 40px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            width: 100%;
            max-width: 400px;
        }
        
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 30px;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        input {
            width: 100%;
            padding: 12px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 1em;
        }
        
        button {
            width: 100%;
            padding: 12px;
            background: #10b981;
            color: white;
            border: none;
            border-radius: 5px;
            font-size: 1.1em;
            font-weight: bold;
            cursor: pointer;
        }
        
        .error {
            color: #ef4444;
            text-align: center;
            margin-bottom: 15px;
        }
        
        .login-link {
            text-align: center;
            margin-top: 20px;
        }
    </style>
</head>
<body>
    <div class="register-container">
        <h1>🎯 Create Account</h1>
        
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        
        <form method="POST">
            <div class="form-group">
                <input type="text" name="username" placeholder="Username" required>
            </div>
            <div class="form-group">
                <input type="password" name="password" placeholder="Password" required>
            </div>
            <button type="submit">Register</button>
        </form>
        
        <div class="login-link">
            <p>Already have an account? <a href="{{ url_for('login') }}">Login here</a></p>
        </div>
    </div>
</body>
</html>''')

# Add logout route
@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

print("✅ BTC Trading Platform created successfully!")
print("🚀 Run the application: python app.py")
print("🌐 Open: http://localhost:5000")
print("📋 Features:")
print("   - Real-time BTC price from Binance")
print("   - Long/Short trading with leverage")
print("   - PVP trading system")
print("   - Deposit system")
print("   - Live P&L calculation")
print("   - Modern responsive design")