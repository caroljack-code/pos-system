import sqlite3
import json
import os
import shutil
import datetime
import jwt
from functools import wraps
from flask import Flask, jsonify, request, send_from_directory, make_response
import csv
import io
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import cloudinary
import cloudinary.uploader
from werkzeug.utils import secure_filename
import base64
try:
    import requests
except Exception:
    requests = None

# Determine paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if os.environ.get('VERCEL') or os.environ.get('AWS_LAMBDA_FUNCTION_NAME'):
    # Use /tmp for writable database in serverless environments
    DB_NAME = "/tmp/pos.db"
    # Copy initial DB if it exists
    original_db = os.path.join(BASE_DIR, "pos.db")
    if os.path.exists(original_db) and not os.path.exists(DB_NAME):
        try:
            shutil.copy2(original_db, DB_NAME)
        except Exception as e:
            print(f"Warning: Could not copy initial database: {e}")
else:
    DB_NAME = os.environ.get('DB_PATH') or os.path.join(BASE_DIR, "pos.db")

FRONTEND_DIR = os.path.join(BASE_DIR, '..')

if os.environ.get('VERCEL') or os.environ.get('AWS_LAMBDA_FUNCTION_NAME'):
    # In serverless, we can't write to the source directory.
    # We use /tmp for temporary uploads or disable local storage.
    PRODUCT_UPLOAD_DIR = os.path.join("/tmp", 'uploads', 'products')
    BRAND_UPLOAD_DIR = os.path.join("/tmp", 'uploads', 'branding')
else:
    PRODUCT_UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads', 'products')
    BRAND_UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads', 'branding')

app = Flask(__name__, static_url_path='', static_folder=FRONTEND_DIR)
app.config['SECRET_KEY'] = 'your_secret_key_change_this_in_production'
CORS(app)
cloudinary.config(cloudinary_url=os.environ.get('CLOUDINARY_URL', ''), secure=True)

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL
        )
    ''')

    # Create Products table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER NOT NULL,
            category TEXT,
            barcode TEXT,
            low_stock_threshold INTEGER,
            image_url TEXT,
            min_price REAL
        )
    ''')
    cols = [row['name'] for row in cursor.execute("PRAGMA table_info(products)").fetchall()]
    if 'barcode' not in cols:
        cursor.execute("ALTER TABLE products ADD COLUMN barcode TEXT")
    if 'low_stock_threshold' not in cols:
        cursor.execute("ALTER TABLE products ADD COLUMN low_stock_threshold INTEGER")
    if 'image_url' not in cols:
        cursor.execute("ALTER TABLE products ADD COLUMN image_url TEXT")
    if 'min_price' not in cols:
        cursor.execute("ALTER TABLE products ADD COLUMN min_price REAL")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode) WHERE barcode IS NOT NULL")
    seed_barcodes = {
        'Samsung 43\" Smart TV': '890100000001',
        'Blender 500W': '890100000002',
        'Double Bedsheet Set': '890100000003',
        'Non-stick Cookware Set': '890100000004',
        'Bluetooth Speaker': '890100000005',
        'Electric Kettle': '890100000006',
        'King Size Duvet': '890100000007',
        'Iron Box': '890100000008'
    }
    for name, code in seed_barcodes.items():
        cursor.execute("UPDATE products SET barcode = ? WHERE name = ? AND (barcode IS NULL OR barcode = '')", (code, name))
    
    # Create Sales table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            total REAL NOT NULL,
            subtotal REAL,
            vat REAL,
            cashier TEXT,
            payment_method TEXT,
            payment_reference TEXT,
            date TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'completed'
        )
    ''')
    sales_cols = [row['name'] for row in cursor.execute("PRAGMA table_info(sales)").fetchall()]
    if 'subtotal' not in sales_cols:
        cursor.execute("ALTER TABLE sales ADD COLUMN subtotal REAL")
    if 'vat' not in sales_cols:
        cursor.execute("ALTER TABLE sales ADD COLUMN vat REAL")
    if 'cashier' not in sales_cols:
        cursor.execute("ALTER TABLE sales ADD COLUMN cashier TEXT")
    if 'payment_method' not in sales_cols:
        cursor.execute("ALTER TABLE sales ADD COLUMN payment_method TEXT")
    if 'payment_reference' not in sales_cols:
        cursor.execute("ALTER TABLE sales ADD COLUMN payment_reference TEXT")
    if 'status' not in sales_cols:
        cursor.execute("ALTER TABLE sales ADD COLUMN status TEXT DEFAULT 'completed'")

    # Create Sale Items table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sale_items (
            sale_id INTEGER,
            product_id INTEGER,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            FOREIGN KEY(sale_id) REFERENCES sales(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER,
            action TEXT,
            reason TEXT,
            actor TEXT,
            date TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Seed products if empty
    cursor.execute("SELECT count(*) as count FROM products")
    if cursor.fetchone()['count'] == 0:
        products = [
            ('Samsung 43" Smart TV', 35000, 10, 'Electronics', '890100000001'),
            ('Blender 500W', 4500, 20, 'Kitchenware', '890100000002'),
            ('Double Bedsheet Set', 2500, 30, 'Beddings', '890100000003'),
            ('Non-stick Cookware Set', 8000, 15, 'Kitchenware', '890100000004'),
            ('Bluetooth Speaker', 3000, 25, 'Electronics', '890100000005'),
            ('Electric Kettle', 1500, 40, 'Kitchenware', '890100000006'),
            ('King Size Duvet', 5000, 12, 'Beddings', '890100000007'),
            ('Iron Box', 1200, 50, 'Electronics', '890100000008')
        ]
        cursor.executemany("INSERT INTO products (name, price, stock, category, barcode) VALUES (?, ?, ?, ?, ?)", products)
        print("Seeded initial products")
    cursor.execute("UPDATE products SET low_stock_threshold = COALESCE(low_stock_threshold, 5)")
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS banks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    ''')
    bcount = cursor.execute("SELECT COUNT(*) as c FROM banks").fetchone()['c']
    if bcount == 0:
        cursor.executemany("INSERT INTO banks (name) VALUES (?)", [
            ('KCB Bank',),
            ('Co-op Bank',),
            ('Equity Bank',)
        ])
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    ''')
    ccount = cursor.execute("SELECT COUNT(*) as c FROM categories").fetchone()['c']
    if ccount == 0:
        cursor.executemany("INSERT INTO categories (name) VALUES (?)", [
            ('Home Appliances',),
            ('Electronics',),
            ('Beddings',),
            ('Household Items',)
        ])
    
    # Seed default users: superadmin, admin, cashier
    # Super Admin
    existing_super = cursor.execute("SELECT 1 FROM users WHERE username = ?", ('superadmin',)).fetchone()
    if not existing_super:
        super_pw = generate_password_hash('super123')
        cursor.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                       ('superadmin', super_pw, 'super_admin'))
    # Admin
    existing_admin = cursor.execute("SELECT role FROM users WHERE username = ?", ('admin',)).fetchone()
    if not existing_admin:
        admin_pw = generate_password_hash('admin123')
        cursor.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                       ('admin', admin_pw, 'admin'))
    else:
        if existing_admin['role'] == 'super_admin':
            cursor.execute("UPDATE users SET role = 'admin' WHERE username = 'admin'")
    # Cashier
    existing_cashier = cursor.execute("SELECT 1 FROM users WHERE username = ?", ('cashier',)).fetchone()
    if not existing_cashier:
        cashier_pw = generate_password_hash('cashier123')
        cursor.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                       ('cashier', cashier_pw, 'cashier'))
    print("Default users ensured (superadmin/super123, admin/admin123, cashier/cashier123)")
    
    conn.commit()
    conn.close()

# Initialize DB
init_db()

# --- Auth Helpers ---

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(" ")[1]
        
        if not token:
            return jsonify({'message': 'Token is missing!'}), 401
        
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            conn = get_db_connection()
            user = conn.execute('SELECT * FROM users WHERE id = ?', (data['user_id'],)).fetchone()
            conn.close()
            if not user:
                 return jsonify({'message': 'User not found!'}), 401
            request.current_user = user
        except Exception as e:
            return jsonify({'message': 'Token is invalid!', 'error': str(e)}), 401
            
        return f(*args, **kwargs)
    return decorated

def role_required(allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not hasattr(request, 'current_user'):
                 return jsonify({'message': 'User not authenticated'}), 401
            
            user_role = request.current_user['role']
            
            # Super admin has all access
            if user_role == 'super_admin':
                return f(*args, **kwargs)
            
            if user_role not in allowed_roles:
                return jsonify({'message': 'Permission denied'}), 403
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def role_required_strict(allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not hasattr(request, 'current_user'):
                return jsonify({'message': 'User not authenticated'}), 401
            user_role = request.current_user['role']
            if user_role not in allowed_roles:
                return jsonify({'message': 'Permission denied'}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# --- Routes ---

# Login Route
@app.route('/uploads/<path:filename>')
def serve_uploads(filename):
    # Try /tmp first (for new uploads on Vercel)
    tmp_path = os.path.join('/tmp', 'uploads', filename)
    if os.path.exists(tmp_path):
        return send_from_directory(os.path.dirname(tmp_path), os.path.basename(tmp_path))
    
    # Try repo path (for pre-existing files)
    repo_path = os.path.join(BASE_DIR, 'uploads', filename)
    if os.path.exists(repo_path):
        return send_from_directory(os.path.dirname(repo_path), os.path.basename(repo_path))
        
    return make_response("File not found", 404)

@app.route('/login', methods=['POST'])
@app.route('/api/login', methods=['POST'])
def login():
    auth = request.get_json()
    
    if not auth or not auth.get('username') or not auth.get('password'):
        return jsonify({'message': 'Could not verify', 'WWW-Authenticate': 'Basic realm="Login required!"'}), 401
    
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (auth.get('username'),)).fetchone()
    conn.close()
    
    if not user:
        return jsonify({'message': 'User not found'}), 401
        
    if check_password_hash(user['password_hash'], auth.get('password')):
        token = jwt.encode({
            'user_id': user['id'],
            'role': user['role'],
            'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        }, app.config['SECRET_KEY'], algorithm="HS256")
        if isinstance(token, bytes):
            token = token.decode('utf-8')
        
        return jsonify({
            'message': 'success',
            'token': token,
            'role': user['role'],
            'username': user['username']
        })
        
    return jsonify({'message': 'Could not verify', 'WWW-Authenticate': 'Basic realm="Login required!"'}), 401

# Serve Frontend
@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory(FRONTEND_DIR, path)

@app.route('/api/ping', methods=['GET'])
def ping():
    return jsonify({"message": "pong"})

def get_mpesa_config():
    cfg = {
        "env": os.environ.get("MPESA_ENV", "sandbox"),
        "consumer_key": os.environ.get("MPESA_CONSUMER_KEY"),
        "consumer_secret": os.environ.get("MPESA_CONSUMER_SECRET"),
        "shortcode": os.environ.get("MPESA_SHORTCODE"),
        "passkey": os.environ.get("MPESA_PASSKEY"),
        "callback": os.environ.get("MPESA_CALLBACK_URL", "https://example.com/callback")
    }
    return cfg

def mpesa_access_token(cfg):
    if not requests:
        raise Exception("requests not installed")
    if not (cfg["consumer_key"] and cfg["consumer_secret"]):
        raise Exception("M-Pesa credentials not configured")
    base = "https://sandbox.safaricom.co.ke" if cfg["env"] == "sandbox" else "https://api.safaricom.co.ke"
    url = base + "/oauth/v1/generate?grant_type=client_credentials"
    r = requests.get(url, auth=(cfg["consumer_key"], cfg["consumer_secret"]), timeout=15)
    if r.status_code != 200:
        raise Exception("Failed to get access token")
    return r.json()["access_token"]

def mpesa_stkpush_request(cfg, token, amount, phone, account_ref="POS", trans_desc="Payment"):
    if not requests:
        raise Exception("requests not installed")
    if not (cfg["shortcode"] and cfg["passkey"]):
        raise Exception("M-Pesa shortcode/passkey not configured")
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode((cfg["shortcode"] + cfg["passkey"] + timestamp).encode()).decode()
    base = "https://sandbox.safaricom.co.ke" if cfg["env"] == "sandbox" else "https://api.safaricom.co.ke"
    url = base + "/mpesa/stkpush/v1/processrequest"
    payload = {
        "BusinessShortCode": cfg["shortcode"],
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": phone,
        "PartyB": cfg["shortcode"],
        "PhoneNumber": phone,
        "CallBackURL": cfg["callback"],
        "AccountReference": account_ref,
        "TransactionDesc": trans_desc
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code != 200:
        raise Exception(r.text)
    return r.json()

def mpesa_query_request(cfg, token, checkout_id):
    if not requests:
        raise Exception("requests not installed")
    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode((cfg["shortcode"] + cfg["passkey"] + timestamp).encode()).decode()
    base = "https://sandbox.safaricom.co.ke" if cfg["env"] == "sandbox" else "https://api.safaricom.co.ke"
    url = base + "/mpesa/stkpushquery/v1/query"
    payload = {
        "BusinessShortCode": cfg["shortcode"],
        "Password": password,
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_id
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code != 200:
        raise Exception(r.text)
    return r.json()

@app.route('/api/pay/mpesa/stkpush', methods=['POST'])
@token_required
@role_required(['cashier', 'admin'])
def mpesa_stkpush():
    data = request.get_json() or {}
    amount = float(data.get('amount') or 0)
    phone = str(data.get('phone') or '').strip()
    if amount <= 0 or not phone:
        return jsonify({"error": "amount and phone required"}), 400
    cfg = get_mpesa_config()
    # Dev fallback: if not configured, simulate success
    if not (cfg["consumer_key"] and cfg["consumer_secret"] and cfg["shortcode"] and cfg["passkey"]):
        return jsonify({
            "message": "simulated",
            "MerchantRequestID": "SIMULATED_MERCHANT",
            "CheckoutRequestID": "SIMULATED_CHECKOUT",
            "CustomerMessage": "Simulated prompt sent"
        })
    try:
        token = mpesa_access_token(cfg)
        res = mpesa_stkpush_request(cfg, token, amount, phone, account_ref="PIMUT POS", trans_desc="Sale Payment")
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/pay/mpesa/query', methods=['GET'])
@token_required
@role_required(['cashier', 'admin'])
def mpesa_query():
    checkout_id = request.args.get('CheckoutRequestID') or ''
    if not checkout_id:
        return jsonify({"error": "CheckoutRequestID required"}), 400
    cfg = get_mpesa_config()
    if checkout_id.startswith("SIMULATED"):
        return jsonify({"ResultCode": "0", "ResultDesc": "Success", "MpesaReceiptNumber": "SIM123456"})
    try:
        token = mpesa_access_token(cfg)
        res = mpesa_query_request(cfg, token, checkout_id)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/banks', methods=['GET'])
@token_required
def list_banks():
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT id, name FROM banks ORDER BY name").fetchall()
        return jsonify({"message": "success", "data": [dict(ix) for ix in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()

@app.route('/api/banks', methods=['POST'])
@token_required
@role_required_strict(['admin'])
def add_bank():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO banks (name) VALUES (?)", (name,))
        conn.commit()
        return jsonify({"message": "success"})
    except sqlite3.IntegrityError:
        return jsonify({"error": "bank already exists"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
@app.route('/api/categories', methods=['GET'])
@token_required
def list_categories():
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
        return jsonify({"message": "success", "data": [dict(ix) for ix in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()

@app.route('/api/categories', methods=['POST'])
@token_required
@role_required(['admin'])
def add_category():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
        conn.commit()
        return jsonify({"message": "success"})
    except sqlite3.IntegrityError:
        return jsonify({"error": "category already exists"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()

# GET /products
@app.route('/products', methods=['GET'])
@app.route('/api/products', methods=['GET']) # Alias for frontend compatibility
@token_required
@role_required(['admin', 'cashier', 'assistant'])
def get_products():
    conn = get_db_connection()
    products = conn.execute('SELECT * FROM products').fetchall()
    conn.close()
    data = []
    for ix in products:
        d = dict(ix)
        thr = d.get('low_stock_threshold')
        d['low_stock'] = thr is not None and d.get('stock', 0) <= int(thr)
        data.append(d)
    return jsonify({"message": "success", "data": data})

@app.route('/products/barcode/<barcode>', methods=['GET'])
@app.route('/api/products/barcode/<barcode>', methods=['GET'])
@token_required
@role_required(['admin', 'cashier', 'assistant'])
def get_product_by_barcode(barcode):
    conn = get_db_connection()
    product = conn.execute('SELECT * FROM products WHERE barcode = ?', (barcode,)).fetchone()
    conn.close()
    if product:
        return jsonify({"message": "success", "data": dict(product)})
    return jsonify({"error": "Product not found"}), 404

@app.route('/uploads/products/<path:filename>')
def serve_product_upload(filename):
    return send_from_directory(PRODUCT_UPLOAD_DIR, filename)

@app.route('/uploads/branding/<path:filename>')
def serve_brand_upload(filename):
    return send_from_directory(BRAND_UPLOAD_DIR, filename)

@app.route('/api/products', methods=['POST'])
@token_required
@role_required(['admin', 'assistant'])
def create_product():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    category = (data.get('category') or '').strip()
    price = data.get('price')
    stock = data.get('stock')
    barcode = (data.get('barcode') or '').strip() or None
    low_stock_threshold = data.get('low_stock_threshold')
    image_url = (data.get('image_url') or '').strip() or None
    min_price = data.get('min_price')
    
    if not name:
        return jsonify({"error": "Name required"}), 400
    try:
        price = float(price)
        stock = int(stock)
        if min_price is not None:
            min_price = float(min_price)
    except Exception:
        return jsonify({"error": "Invalid price or stock"}), 400
    if category == '':
        category = 'General'
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        if min_price is not None:
            cursor.execute(
                "INSERT INTO products (name, price, stock, category, barcode, low_stock_threshold, image_url, min_price) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, price, stock, category, barcode, low_stock_threshold, image_url, min_price)
            )
        else:
            cursor.execute(
                "INSERT INTO products (name, price, stock, category, barcode, low_stock_threshold, image_url) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, price, stock, category, barcode, low_stock_threshold, image_url)
            )
        new_id = cursor.lastrowid
        conn.commit()
        return jsonify({"message": "success", "id": new_id})
    except sqlite3.IntegrityError as e:
        err = str(e)
        if 'idx_products_barcode' in err or 'UNIQUE' in err:
            return jsonify({"error": "Barcode already exists"}), 400
        return jsonify({"error": err}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
@app.route('/api/users', methods=['GET'])
@token_required
@role_required(['admin'])
def list_users():
    role = request.args.get('role')
    conn = get_db_connection()
    try:
        if role:
            rows = conn.execute("SELECT id, username, role FROM users WHERE role = ?", (role,)).fetchall()
        else:
            rows = conn.execute("SELECT id, username, role FROM users").fetchall()
        return jsonify({"message": "success", "data": [dict(ix) for ix in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
@app.route('/api/users', methods=['POST'])
@token_required
@role_required(['admin'])
def create_user():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    role = (data.get('role') or 'cashier').strip()
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    allowed_roles = ['cashier', 'admin', 'assistant']
    if role == 'super_admin':
        # Allow creating super_admin from admin or existing super_admin
        if request.current_user['role'] not in ['admin', 'super_admin']:
            role = 'cashier'
    elif role not in allowed_roles:
        role = 'cashier'
    conn = get_db_connection()
    try:
        hashed = generate_password_hash(password)
        conn.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)", (username, hashed, role))
        conn.commit()
        new_id = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()['id']
        return jsonify({"message": "success", "id": new_id})
    except sqlite3.IntegrityError:
        return jsonify({"error": "username already exists"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
@app.route('/api/me/password', methods=['POST'])
@token_required
def change_my_password():
    data = request.get_json() or {}
    old_pw = (data.get('old_password') or '').strip()
    new_pw = (data.get('new_password') or '').strip()
    if not old_pw or not new_pw:
        return jsonify({"error": "old_password and new_password required"}), 400
    conn = get_db_connection()
    try:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (request.current_user['id'],)).fetchone()
        if not user or not check_password_hash(user['password_hash'], old_pw):
            return jsonify({"error": "invalid old password"}), 400
        hashed = generate_password_hash(new_pw)
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed, user['id']))
        conn.commit()
        return jsonify({"message": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
@app.route('/api/users/<int:user_id>/password', methods=['PUT'])
@token_required
@role_required(['admin'])
def admin_set_password(user_id):
    data = request.get_json() or {}
    new_pw = (data.get('new_password') or '').strip()
    if not new_pw:
        return jsonify({"error": "new_password required"}), 400
    conn = get_db_connection()
    try:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return jsonify({"error": "user not found"}), 404
        hashed = generate_password_hash(new_pw)
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hashed, user_id))
        conn.commit()
        return jsonify({"message": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()

@app.route('/api/products/<int:id>/image/upload', methods=['POST'])
@token_required
@role_required(['admin', 'assistant'])
def upload_product_image(id):
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "file required"}), 400
    name = secure_filename(file.filename or '')
    ext = os.path.splitext(name)[1].lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        return jsonify({"error": "invalid file type"}), 400
    os.makedirs(PRODUCT_UPLOAD_DIR, exist_ok=True)
    fname = f"product_{id}_{int(datetime.datetime.utcnow().timestamp())}{ext}"
    path = os.path.join(PRODUCT_UPLOAD_DIR, fname)
    file.save(path)
    url = f"/uploads/products/{fname}"
    conn = get_db_connection()
    try:
        conn.execute("UPDATE products SET image_url = ? WHERE id = ?", (url, id))
        conn.commit()
        return jsonify({"message": "success", "image_url": url})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()

@app.route('/api/branding/logo', methods=['POST'])
@token_required
@role_required(['admin'])
def upload_brand_logo():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "file required"}), 400
    name = secure_filename(file.filename or '')
    ext = os.path.splitext(name)[1].lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        return jsonify({"error": "invalid file type"}), 400
    os.makedirs(BRAND_UPLOAD_DIR, exist_ok=True)
    fname = f"logo{ext}"
    path = os.path.join(BRAND_UPLOAD_DIR, fname)
    file.save(path)
    url = f"/uploads/branding/{fname}"
    return jsonify({"message": "success", "image_url": url})

@app.route('/api/branding/logo', methods=['GET'])
def get_brand_logo():
    try:
        exts = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
        for ext in exts:
            candidate = os.path.join(BRAND_UPLOAD_DIR, f"logo{ext}")
            if os.path.exists(candidate):
                return jsonify({"message": "success", "image_url": f"/uploads/branding/logo{ext}"})
        return jsonify({"error": "not_found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
# POST /sale
@app.route('/sale', methods=['POST'])
@app.route('/api/sales', methods=['POST']) # Alias for frontend compatibility
@token_required
@role_required(['cashier', 'admin'])
def create_sale():
    data = request.get_json()
    items = data.get('items') # List of {productId, quantity, price}
    payment_method = data.get('payment_method') or data.get('paymentMethod')
    payment_reference = data.get('payment_reference') or data.get('paymentReference')
    
    if not items:
        return jsonify({"error": "No items in sale"}), 400
    allowed_methods = {'cash', 'mpesa', 'bank', 'card', 'cheque', 'credit'}
    if payment_method not in allowed_methods:
        return jsonify({"error": "Invalid payment method"}), 400
    if payment_method in {'mpesa', 'bank', 'card', 'cheque', 'credit'} and payment_reference is not None:
        payment_reference = str(payment_reference).strip() or None

    conn = get_db_connection()
    try:
        conn.execute("BEGIN TRANSACTION")
        
        subtotal = 0
        for item in items:
            subtotal += float(item['price']) * int(item['quantity'])
        vat = round(subtotal * 0.16)
        total = subtotal + vat
        cashier = request.current_user['username']

        cursor = conn.cursor()
        cols = [row['name'] for row in conn.execute("PRAGMA table_info(sales)").fetchall()]
        if 'items' in cols:
            cursor.execute(
                "INSERT INTO sales (total, subtotal, vat, cashier, payment_method, payment_reference, items) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (total, subtotal, vat, cashier, payment_method, payment_reference, json.dumps(items))
            )
        else:
            cursor.execute(
                "INSERT INTO sales (total, subtotal, vat, cashier, payment_method, payment_reference) VALUES (?, ?, ?, ?, ?, ?)",
                (total, subtotal, vat, cashier, payment_method, payment_reference)
            )
        sale_id = cursor.lastrowid
        
        for item in items:
            product_id = item['productId']
            quantity = item['quantity']
            price = item['price']
            
            cur = conn.execute("SELECT stock, min_price FROM products WHERE id = ?", (product_id,)).fetchone()
            if not cur:
                raise Exception("Product not found")
            if cur['min_price'] is not None and float(price) < float(cur['min_price']):
                raise Exception("Price below minimum allowed")
            if cur['stock'] < quantity:
                raise Exception("Insufficient stock")
            
            conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (quantity, product_id))
            
            conn.execute("INSERT INTO sale_items (sale_id, product_id, quantity, price) VALUES (?, ?, ?, ?)", 
                         (sale_id, product_id, quantity, price))
        
        conn.commit()
        return jsonify({"message": "success", "saleId": sale_id})
        
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()

@app.route('/api/sales/<int:sale_id>/refund', methods=['POST'])
@token_required
@role_required(['admin'])
def refund_sale(sale_id):
    data = request.get_json() or {}
    reason = (data.get('reason') or '').strip()
    if not reason:
        return jsonify({"error": "Reason required"}), 400
    conn = get_db_connection()
    try:
        conn.execute("BEGIN TRANSACTION")
        sale = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        if not sale:
            raise Exception("Sale not found")
        if sale['status'] != 'completed':
            raise Exception("Sale not refundable")
        items = conn.execute("SELECT product_id, quantity FROM sale_items WHERE sale_id = ?", (sale_id,)).fetchall()
        for it in items:
            conn.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (it['quantity'], it['product_id']))
        conn.execute("UPDATE sales SET status = 'refunded' WHERE id = ?", (sale_id,))
        actor = request.current_user['username']
        conn.execute("INSERT INTO audit_log (sale_id, action, reason, actor) VALUES (?, ?, ?, ?)",
                     (sale_id, 'refund', reason, actor))
        conn.commit()
        return jsonify({"message": "success"})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()

@app.route('/api/sales/<int:sale_id>/void', methods=['POST'])
@token_required
@role_required(['admin'])
def void_sale(sale_id):
    data = request.get_json() or {}
    reason = (data.get('reason') or '').strip()
    if not reason:
        return jsonify({"error": "Reason required"}), 400
    conn = get_db_connection()
    try:
        conn.execute("BEGIN TRANSACTION")
        sale = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        if not sale:
            raise Exception("Sale not found")
        if sale['status'] != 'completed':
            raise Exception("Sale not voidable")
        sale_date = conn.execute("SELECT DATE(date) as d FROM sales WHERE id = ?", (sale_id,)).fetchone()['d']
        today = conn.execute("SELECT DATE('now') as d").fetchone()['d']
        if sale_date != today:
            raise Exception("Void only allowed same day")
        items = conn.execute("SELECT product_id, quantity FROM sale_items WHERE sale_id = ?", (sale_id,)).fetchall()
        for it in items:
            conn.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (it['quantity'], it['product_id']))
        conn.execute("UPDATE sales SET status = 'voided' WHERE id = ?", (sale_id,))
        actor = request.current_user['username']
        conn.execute("INSERT INTO audit_log (sale_id, action, reason, actor) VALUES (?, ?, ?, ?)",
                     (sale_id, 'void', reason, actor))
        conn.commit()
        return jsonify({"message": "success"})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()

# PUT /products/<id>/stock
@app.route('/products/<int:id>/stock', methods=['PUT'])
@app.route('/api/products/<int:id>/stock', methods=['PUT']) # Alias
@token_required
@role_required(['admin'])
def update_stock(id):
    data = request.get_json()
    new_stock = data.get('stock')
    
    if new_stock is None:
        return jsonify({"error": "Stock value required"}), 400
        
    conn = get_db_connection()
    try:
        conn.execute("UPDATE products SET stock = ? WHERE id = ?", (new_stock, id))
        conn.commit()
        return jsonify({"message": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()

@app.route('/api/products/<int:id>/threshold', methods=['PUT'])
@token_required
@role_required(['admin'])
def update_low_stock_threshold(id):
    data = request.get_json() or {}
    thr = data.get('low_stock_threshold')
    if thr is None:
        return jsonify({"error": "low_stock_threshold required"}), 400
    try:
        thr_i = int(thr)
    except Exception:
        return jsonify({"error": "invalid threshold"}), 400
    conn = get_db_connection()
    try:
        conn.execute("UPDATE products SET low_stock_threshold = ? WHERE id = ?", (thr_i, id))
        conn.commit()
        return jsonify({"message": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
@app.route('/api/products/<int:id>/min_price', methods=['PUT'])
@token_required
@role_required(['admin'])
def update_min_price(id):
    data = request.get_json() or {}
    mp = data.get('min_price')
    if mp is None:
        return jsonify({"error": "min_price required"}), 400
    try:
        val = float(mp)
        if val < 0:
            return jsonify({"error": "invalid min_price"}), 400
    except Exception:
        return jsonify({"error": "invalid min_price"}), 400
    conn = get_db_connection()
    try:
        conn.execute("UPDATE products SET min_price = ? WHERE id = ?", (val, id))
        conn.commit()
        return jsonify({"message": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()
@app.route('/api/products/low-stock', methods=['GET'])
@token_required
@role_required(['admin'])
def get_low_stock_products():
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM products WHERE low_stock_threshold IS NOT NULL AND stock <= low_stock_threshold"
        ).fetchall()
        data = []
        for ix in rows:
            d = dict(ix)
            d['low_stock'] = True
            data.append(d)
        return jsonify({"message": "success", "data": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/products/<int:id>/image', methods=['POST'])
@token_required
@role_required(['admin', 'assistant'])
def set_product_image(id):
    data = request.get_json() or {}
    image_url = (data.get('image_url') or '').strip()
    if not image_url:
        return jsonify({"error": "image_url required"}), 400
    try:
        secure_url = None
        if os.environ.get('CLOUDINARY_URL'):
            upload_result = cloudinary.uploader.upload(image_url, folder="pimut/products", public_id=f"product_{id}", overwrite=True)
            secure_url = upload_result.get('secure_url') or upload_result.get('url')
            if not secure_url:
                return jsonify({"error": "Upload failed"}), 400
        else:
            secure_url = image_url
        conn = get_db_connection()
        conn.execute("UPDATE products SET image_url = ? WHERE id = ?", (secure_url, id))
        conn.commit()
        conn.close()
        return jsonify({"message": "success", "image_url": secure_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/products/<int:id>/image', methods=['DELETE'])
@token_required
@role_required(['admin', 'assistant'])
def remove_product_image(id):
    try:
        cloudinary.uploader.destroy(f"pimut/products/product_{id}", invalidate=True)
    except Exception:
        pass
    conn = get_db_connection()
    try:
        conn.execute("UPDATE products SET image_url = NULL WHERE id = ?", (id,))
        conn.commit()
        return jsonify({"message": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()

# Daily Report (Extra, for frontend compatibility)
@app.route('/api/sales/daily', methods=['GET'])
@token_required
@role_required(['admin', 'assistant'])
def get_daily_sales():
    conn = get_db_connection()
    try:
        query = """
            SELECT DATE(date) as sale_date,
                   COUNT(id) as total_sales,
                   SUM(subtotal) as subtotal_sum,
                   SUM(vat) as vat_sum,
                   SUM(total) as total_revenue
            FROM sales
            GROUP BY DATE(date)
            ORDER BY sale_date DESC
        """
        report = conn.execute(query).fetchall()
        return jsonify({"message": "success", "data": [dict(ix) for ix in report]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/reports/daily', methods=['GET'])
@token_required
@role_required(['admin', 'assistant'])
def report_daily():
    start = request.args.get('start')
    end = request.args.get('end')
    conn = get_db_connection()
    try:
        base = """
            SELECT DATE(date) as sale_date,
                   COUNT(id) as total_sales,
                   SUM(subtotal) as subtotal_sum,
                   SUM(vat) as vat_sum,
                   SUM(total) as total_sum
            FROM sales
        """
        if start and end:
            base += " WHERE DATE(date) BETWEEN ? AND ?"
            base += " GROUP BY DATE(date) ORDER BY sale_date DESC"
            rows = conn.execute(base, (start, end)).fetchall()
        else:
            base += " GROUP BY DATE(date) ORDER BY sale_date DESC"
            rows = conn.execute(base).fetchall()
        return jsonify({"message": "success", "data": [dict(ix) for ix in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/reports/cashier', methods=['GET'])
@token_required
@role_required(['admin'])
def report_by_cashier():
    start = request.args.get('start')
    end = request.args.get('end')
    conn = get_db_connection()
    try:
        base = """
            SELECT cashier,
                   COUNT(id) as total_sales,
                   SUM(subtotal) as subtotal_sum,
                   SUM(vat) as vat_sum,
                   SUM(total) as total_sum
            FROM sales
        """
        if start and end:
            base += " WHERE DATE(date) BETWEEN ? AND ?"
            base += " GROUP BY cashier ORDER BY cashier"
            rows = conn.execute(base, (start, end)).fetchall()
        else:
            base += " GROUP BY cashier ORDER BY cashier"
            rows = conn.execute(base).fetchall()
        return jsonify({"message": "success", "data": [dict(ix) for ix in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/reports/payment_methods', methods=['GET'])
@token_required
@role_required(['admin'])
def report_payment_methods():
    period = (request.args.get('period') or 'monthly').lower()
    start = request.args.get('start')
    end = request.args.get('end')
    if period not in ('weekly', 'monthly', 'annual'):
        return jsonify({"error": "Invalid period"}), 400
    if period == 'weekly':
        label = "strftime('%Y-W%W', date)"
    elif period == 'monthly':
        label = "strftime('%Y-%m', date)"
    else:
        label = "strftime('%Y', date)"
    conn = get_db_connection()
    try:
        base = f"""
            SELECT {label} as period_label,
                   payment_method,
                   COUNT(id) as total_sales,
                   SUM(subtotal) as subtotal_sum,
                   SUM(vat) as vat_sum,
                   SUM(total) as total_sum
            FROM sales
        """
        params = ()
        if start and end:
            base += " WHERE DATE(date) BETWEEN ? AND ?"
            params = (start, end)
        base += " GROUP BY period_label, payment_method ORDER BY period_label DESC, payment_method"
        rows = conn.execute(base, params).fetchall()
        return jsonify({"message": "success", "data": [dict(ix) for ix in rows]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/export/sales.csv', methods=['GET'])
@token_required
@role_required(['admin'])
def export_sales_csv():
    start = request.args.get('start')
    end = request.args.get('end')
    conn = get_db_connection()
    try:
        base = """
            SELECT id, date, cashier, payment_method, payment_reference, subtotal, vat, total, status
            FROM sales
        """
        params = ()
        if start and end:
            base += " WHERE DATE(date) BETWEEN ? AND ?"
            params = (start, end)
        base += " ORDER BY date DESC"
        rows = conn.execute(base, params).fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['id','date','cashier','payment_method','payment_reference','subtotal','vat','total','status'])
        for r in rows:
            writer.writerow([r['id'], r['date'], r['cashier'], r['payment_method'], r['payment_reference'], r['subtotal'], r['vat'], r['total'], r['status']])
        resp = make_response(output.getvalue())
        resp.headers['Content-Type'] = 'text/csv'
        resp.headers['Content-Disposition'] = 'attachment; filename="sales_export.csv"'
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/export/products.csv', methods=['GET'])
@token_required
@role_required(['admin'])
def export_products_csv():
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT id, name, category, price, stock, barcode, low_stock_threshold, min_price FROM products ORDER BY name").fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['id','name','category','price','stock','barcode','low_stock_threshold','min_price'])
        for r in rows:
            writer.writerow([r['id'], r['name'], r['category'], r['price'], r['stock'], r['barcode'], r['low_stock_threshold'], r['min_price']])
        resp = make_response(output.getvalue())
        resp.headers['Content-Type'] = 'text/csv'
        resp.headers['Content-Disposition'] = 'attachment; filename="products_export.csv"'
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()
if __name__ == '__main__':
    host = os.environ.get('POS_BIND_HOST', '0.0.0.0')
    port = int(os.environ.get('POS_PORT', '5000'))
    app.run(host=host, port=port)
