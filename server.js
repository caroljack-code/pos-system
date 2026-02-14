const express = require('express');
const sqlite3 = require('sqlite3').verbose();
const bodyParser = require('body-parser');
const cors = require('cors');
const path = require('path');

const app = express();
const port = 3000;

// Middleware
app.use(cors());
app.use(bodyParser.json());
app.use(express.static('.')); // Serve static files from current directory

// Database setup
const db = new sqlite3.Database('./pos.db', (err) => {
    if (err) {
        console.error('Error opening database', err.message);
    } else {
        console.log('Connected to the SQLite database.');
        initDb();
    }
});

function initDb() {
    db.serialize(() => {
        // Create Products table
        db.run(`CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER NOT NULL,
            category TEXT
        )`);

        // Create Sales table
        db.run(`CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT DEFAULT CURRENT_TIMESTAMP,
            total REAL NOT NULL,
            items TEXT NOT NULL -- JSON string of items
        )`);

        // Seed products if empty
        db.get("SELECT count(*) as count FROM products", (err, row) => {
            if (row.count === 0) {
                const stmt = db.prepare("INSERT INTO products (name, price, stock, category) VALUES (?, ?, ?, ?)");
                const products = [
                    ['Samsung 43" Smart TV', 35000, 10, 'Electronics'],
                    ['Blender 500W', 4500, 20, 'Kitchenware'],
                    ['Double Bedsheet Set', 2500, 30, 'Beddings'],
                    ['Non-stick Cookware Set', 8000, 15, 'Kitchenware'],
                    ['Bluetooth Speaker', 3000, 25, 'Electronics'],
                    ['Electric Kettle', 1500, 40, 'Kitchenware'],
                    ['King Size Duvet', 5000, 12, 'Beddings'],
                    ['Iron Box', 1200, 50, 'Electronics']
                ];
                products.forEach(p => stmt.run(p));
                stmt.finalize();
                console.log('Seeded initial products');
            }
        });
    });
}

// Routes

// Get all products
app.get('/api/products', (req, res) => {
    db.all("SELECT * FROM products", [], (err, rows) => {
        if (err) {
            res.status(400).json({ "error": err.message });
            return;
        }
        res.json({
            "message": "success",
            "data": rows
        });
    });
});

// Create a sale
app.post('/api/sales', (req, res) => {
    const { total, items } = req.body;
    // items should be an array of { productId, quantity, price, name }
    
    // Start transaction to update stock and save sale
    db.serialize(() => {
        db.run("BEGIN TRANSACTION");

        const stmt = db.prepare("INSERT INTO sales (total, items) VALUES (?, ?)");
        stmt.run([total, JSON.stringify(items)], function(err) {
            if (err) {
                console.error(err);
                db.run("ROLLBACK");
                res.status(400).json({ "error": err.message });
                return;
            }
            const saleId = this.lastID;

            // Update stock
            let errorOccurred = false;
            const updateStockStmt = db.prepare("UPDATE products SET stock = stock - ? WHERE id = ?");
            
            items.forEach(item => {
                updateStockStmt.run([item.quantity, item.productId], (err) => {
                    if (err) errorOccurred = true;
                });
            });
            updateStockStmt.finalize();

            if (errorOccurred) {
                db.run("ROLLBACK");
                res.status(400).json({ "error": "Failed to update stock" });
            } else {
                db.run("COMMIT");
                res.json({
                    "message": "success",
                    "saleId": saleId
                });
            }
        });
    });
});

// Get daily sales report
app.get('/api/sales/daily', (req, res) => {
    const sql = `
        SELECT 
            strftime('%Y-%m-%d', date) as sale_date, 
            COUNT(*) as total_sales, 
            SUM(total) as total_revenue 
        FROM sales 
        GROUP BY sale_date 
        ORDER BY sale_date DESC
    `;
    
    db.all(sql, [], (err, rows) => {
        if (err) {
            res.status(400).json({ "error": err.message });
            return;
        }
        res.json({
            "message": "success",
            "data": rows
        });
    });
});

app.listen(port, () => {
    console.log(`Server running on http://localhost:${port}`);
});
