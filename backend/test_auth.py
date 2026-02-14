import unittest
import json
from app import app, init_db, get_db_connection

class AuthTestCase(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        app.config['SECRET_KEY'] = 'test_key'
        self.app = app.test_client()
        
        # Reset DB
        init_db()
        
    def test_login_and_access(self):
        # 1. Login as admin
        rv = self.app.post('/login', json={
            'username': 'admin',
            'password': 'admin123'
        })
        self.assertEqual(rv.status_code, 200, msg=rv.data)
        data = json.loads(rv.data)
        self.assertIn('token', data)
        admin_token = data['token']
        
        # 2. Login as cashier
        rv = self.app.post('/login', json={
            'username': 'cashier',
            'password': 'cashier123'
        })
        self.assertEqual(rv.status_code, 200)
        data = json.loads(rv.data)
        cashier_token = data['token']
        
        # 3. Access products (Both can access)
        rv = self.app.get('/products', headers={'Authorization': f'Bearer {admin_token}'})
        self.assertEqual(rv.status_code, 200)
        
        rv = self.app.get('/products', headers={'Authorization': f'Bearer {cashier_token}'})
        self.assertEqual(rv.status_code, 200)
        
        # 4. Access daily sales (Only Admin)
        rv = self.app.get('/api/sales/daily', headers={'Authorization': f'Bearer {cashier_token}'})
        self.assertEqual(rv.status_code, 403) # Permission denied
        
        rv = self.app.get('/api/sales/daily', headers={'Authorization': f'Bearer {admin_token}'})
        self.assertEqual(rv.status_code, 200)
        
        # New reports endpoints restricted to admin
        rv = self.app.get('/api/reports/daily', headers={'Authorization': f'Bearer {cashier_token}'})
        self.assertEqual(rv.status_code, 403)
        rv = self.app.get('/api/reports/cashier', headers={'Authorization': f'Bearer {cashier_token}'})
        self.assertEqual(rv.status_code, 403)
        rv = self.app.get('/api/reports/daily', headers={'Authorization': f'Bearer {admin_token}'})
        self.assertEqual(rv.status_code, 200)
        rv = self.app.get('/api/reports/cashier', headers={'Authorization': f'Bearer {admin_token}'})
        self.assertEqual(rv.status_code, 200)
    
    def test_payment_validation(self):
        rv = self.app.post('/login', json={'username': 'cashier', 'password': 'cashier123'})
        self.assertEqual(rv.status_code, 200)
        cashier_token = json.loads(rv.data)['token']
        rv = self.app.get('/products', headers={'Authorization': f'Bearer {cashier_token}'})
        self.assertEqual(rv.status_code, 200)
        prod_data = json.loads(rv.data)['data']
        self.assertTrue(len(prod_data) > 0)
        pid = prod_data[0]['id']
        price = prod_data[0]['price']
        rv = self.app.post('/login', json={'username': 'admin', 'password': 'admin123'})
        self.assertEqual(rv.status_code, 200)
        admin_token = json.loads(rv.data)['token']
        rv = self.app.put(f'/api/products/{pid}/stock', json={'stock': 100}, headers={'Authorization': f'Bearer {admin_token}'})
        self.assertEqual(rv.status_code, 200)
        sale_bad = {
            'items': [{'productId': pid, 'quantity': 1, 'price': price}],
            'payment_method': 'card'
        }
        rv = self.app.post('/api/sales', json=sale_bad, headers={'Authorization': f'Bearer {cashier_token}'})
        self.assertEqual(rv.status_code, 400)
        # Optional reference for mpesa/bank is handled server-side; invalid methods return 400
        
    def test_unauthorized(self):
        rv = self.app.get('/products')
        self.assertEqual(rv.status_code, 401)
    
    def test_barcode_lookup(self):
        rv = self.app.post('/login', json={'username': 'admin', 'password': 'admin123'})
        self.assertEqual(rv.status_code, 200)
        token = json.loads(rv.data)['token']
        
        rv = self.app.get('/api/products/barcode/890100000001', headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(rv.status_code, 200)
        data = json.loads(rv.data)
        self.assertEqual(data['message'], 'success')
        self.assertTrue('data' in data)
        self.assertEqual(data['data']['barcode'], '890100000001')
        
        rv = self.app.get('/api/products/barcode/unknowncode', headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(rv.status_code, 404)

    def test_low_stock_products(self):
        rv = self.app.post('/login', json={'username': 'admin', 'password': 'admin123'})
        self.assertEqual(rv.status_code, 200)
        admin_token = json.loads(rv.data)['token']
        rv = self.app.get('/products', headers={'Authorization': f'Bearer {admin_token}'})
        self.assertEqual(rv.status_code, 200)
        products = json.loads(rv.data)['data']
        self.assertTrue(len(products) > 0)
        pid = products[0]['id']
        rv = self.app.put(f'/api/products/{pid}/stock', json={'stock': 3}, headers={'Authorization': f'Bearer {admin_token}'})
        self.assertEqual(rv.status_code, 200)
        conn = get_db_connection()
        conn.execute("UPDATE products SET low_stock_threshold = 5 WHERE id = ?", (pid,))
        conn.commit()
        conn.close()
        rv = self.app.get('/api/products/low-stock', headers={'Authorization': f'Bearer {admin_token}'})
        self.assertEqual(rv.status_code, 200)
        data = json.loads(rv.data)
        self.assertEqual(data['message'], 'success')
        ids = [p['id'] for p in data['data']]
        self.assertIn(pid, ids)
        rv = self.app.post('/login', json={'username': 'cashier', 'password': 'cashier123'})
        self.assertEqual(rv.status_code, 200)
        cashier_token = json.loads(rv.data)['token']
        rv = self.app.get('/api/products/low-stock', headers={'Authorization': f'Bearer {cashier_token}'})
        self.assertEqual(rv.status_code, 403)

    def test_refund_and_void(self):
        rv = self.app.post('/login', json={'username': 'admin', 'password': 'admin123'})
        self.assertEqual(rv.status_code, 200)
        admin_token = json.loads(rv.data)['token']
        rv = self.app.get('/products', headers={'Authorization': f'Bearer {admin_token}'})
        self.assertEqual(rv.status_code, 200)
        products = json.loads(rv.data)['data']
        pid = products[0]['id']
        price = products[0]['price']
        rv = self.app.put(f'/api/products/{pid}/stock', json={'stock': 10}, headers={'Authorization': f'Bearer {admin_token}'})
        self.assertEqual(rv.status_code, 200)
        rv = self.app.post('/login', json={'username': 'cashier', 'password': 'cashier123'})
        self.assertEqual(rv.status_code, 200)
        cashier_token = json.loads(rv.data)['token']
        rv = self.app.post('/api/sales', headers={'Authorization': f'Bearer {cashier_token}'}, json={
            'items': [{'productId': pid, 'quantity': 2, 'price': price}],
            'payment_method': 'cash'
        })
        self.assertEqual(rv.status_code, 200)
        sale_id = json.loads(rv.data)['saleId']
        conn = get_db_connection()
        stock_after_sale = conn.execute("SELECT stock FROM products WHERE id = ?", (pid,)).fetchone()['stock']
        conn.close()
        self.assertEqual(stock_after_sale, 8)
        rv = self.app.post(f'/api/sales/{sale_id}/refund', headers={'Authorization': f'Bearer {admin_token}'}, json={'reason': 'Customer returned'})
        self.assertEqual(rv.status_code, 200)
        conn = get_db_connection()
        stock_after_refund = conn.execute("SELECT stock FROM products WHERE id = ?", (pid,)).fetchone()['stock']
        status_refund = conn.execute("SELECT status FROM sales WHERE id = ?", (sale_id,)).fetchone()['status']
        audit_count = conn.execute("SELECT COUNT(*) as c FROM audit_log WHERE sale_id = ? AND action = 'refund'", (sale_id,)).fetchone()['c']
        conn.close()
        self.assertEqual(stock_after_refund, 10)
        self.assertEqual(status_refund, 'refunded')
        self.assertEqual(audit_count, 1)
        rv = self.app.post('/api/sales', headers={'Authorization': f'Bearer {cashier_token}'}, json={
            'items': [{'productId': pid, 'quantity': 1, 'price': price}],
            'payment_method': 'cash'
        })
        self.assertEqual(rv.status_code, 200)
        sale2_id = json.loads(rv.data)['saleId']
        rv = self.app.post(f'/api/sales/{sale2_id}/void', headers={'Authorization': f'Bearer {cashier_token}'}, json={'reason': 'Mistake'})
        self.assertEqual(rv.status_code, 403)
        rv = self.app.post(f'/api/sales/{sale2_id}/void', headers={'Authorization': f'Bearer {admin_token}'}, json={'reason': 'Mistake'})
        self.assertEqual(rv.status_code, 200)
        conn = get_db_connection()
        status_void = conn.execute("SELECT status FROM sales WHERE id = ?", (sale2_id,)).fetchone()['status']
        audit_void_count = conn.execute("SELECT COUNT(*) as c FROM audit_log WHERE sale_id = ? AND action = 'void'", (sale2_id,)).fetchone()['c']
        conn.execute("UPDATE sales SET date = DATE('now','-1 day'), status = 'completed' WHERE id = ?", (sale2_id,))
        conn.commit()
        conn.close()
        self.assertEqual(status_void, 'voided')
        self.assertEqual(audit_void_count, 1)
        rv = self.app.post(f'/api/sales/{sale2_id}/void', headers={'Authorization': f'Bearer {admin_token}'}, json={'reason': 'Late void'})
        self.assertEqual(rv.status_code, 400)

    def test_exports_permissions_and_csv(self):
        rv = self.app.post('/login', json={'username': 'cashier', 'password': 'cashier123'})
        self.assertEqual(rv.status_code, 200)
        cashier_token = json.loads(rv.data)['token']
        rv = self.app.get('/api/export/products.csv', headers={'Authorization': f'Bearer {cashier_token}'})
        self.assertEqual(rv.status_code, 403)
        rv = self.app.get('/api/export/sales.csv', headers={'Authorization': f'Bearer {cashier_token}'})
        self.assertEqual(rv.status_code, 403)
        rv = self.app.post('/login', json={'username': 'admin', 'password': 'admin123'})
        self.assertEqual(rv.status_code, 200)
        admin_token = json.loads(rv.data)['token']
        rv = self.app.get('/api/export/products.csv', headers={'Authorization': f'Bearer {admin_token}'})
        self.assertEqual(rv.status_code, 200)
        self.assertTrue(rv.data.startswith(b'id,name,category'))
        rv = self.app.get('/api/export/sales.csv?start=2000-01-01&end=2100-01-01', headers={'Authorization': f'Bearer {admin_token}'})
        self.assertEqual(rv.status_code, 200)
        self.assertTrue(rv.data.startswith(b'id,date,cashier'))
    def test_cashier_cannot_refund_and_reason_required(self):
        rv = self.app.post('/login', json={'username': 'admin', 'password': 'admin123'})
        self.assertEqual(rv.status_code, 200)
        admin_token = json.loads(rv.data)['token']
        rv = self.app.get('/products', headers={'Authorization': f'Bearer {admin_token}'})
        self.assertEqual(rv.status_code, 200)
        products = json.loads(rv.data)['data']
        pid = products[0]['id']
        price = products[0]['price']
        rv = self.app.post('/login', json={'username': 'cashier', 'password': 'cashier123'})
        self.assertEqual(rv.status_code, 200)
        cashier_token = json.loads(rv.data)['token']
        rv = self.app.post('/api/sales', headers={'Authorization': f'Bearer {cashier_token}'}, json={
            'items': [{'productId': pid, 'quantity': 1, 'price': price}],
            'payment_method': 'cash'
        })
        self.assertEqual(rv.status_code, 200)
        sale_id = json.loads(rv.data)['saleId']
        rv = self.app.post(f'/api/sales/{sale_id}/refund', headers={'Authorization': f'Bearer {cashier_token}'}, json={'reason': 'Testing'})
        self.assertEqual(rv.status_code, 403)
        rv = self.app.post(f'/api/sales/{sale_id}/refund', headers={'Authorization': f'Bearer {admin_token}'}, json={})
        self.assertEqual(rv.status_code, 400)
        rv = self.app.post(f'/api/sales/{sale_id}/void', headers={'Authorization': f'Bearer {admin_token}'}, json={})
        self.assertEqual(rv.status_code, 400)

if __name__ == '__main__':
    unittest.main()
