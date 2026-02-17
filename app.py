"""
Bingwa Data Sales - Complete Backend with STK Push Integration
Optimized for Render.com deployment
FIXED: Using LipaNa Python package instead of direct API calls
"""
import os
import sqlite3
from datetime import datetime, timedelta
import logging
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from lipana import Lipana
import secrets

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, 
           static_folder='static',
           template_folder='templates')

# Enable CORS
CORS(app)

# Configuration for Render.com
class Config:
    # LipaNa.Dev API Configuration - Using the lipana package
    LIPANA_API_KEY = os.environ.get('LIPANA_API_KEY', 'lip_sk_live_a318ed18e46db96f461830a4c282ff3f55feeca84f9b6433c6ac2a47525c4b32')
    LIPANA_ENVIRONMENT = 'production'  # or 'sandbox' for testing
    
    # Business Configuration
    BUSINESS_SHORTCODE = os.environ.get('LIPANA_BUSINESS_SHORTCODE', '4864614')
    BUSINESS_NAME = "BINGWA DATA SALES"
    
    # Secret Key from environment or generate
    SECRET_KEY = os.environ.get('SECRET_KEY', 'd15a3f8c9e2b7a1d4f6c8a9b3e5d7f2a1c4e8b9d3f6a2c5e8b1d4f7a9c3e6b2d8')
    
    # Determine callback URL based on environment
    if 'RENDER' in os.environ:
        # Running on Render.com
        RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'bingwa-al7a.onrender.com')
        if RENDER_EXTERNAL_HOSTNAME:
            LIPANA_CALLBACK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}/api/payment-callback"
        else:
            # Fallback for Render
            LIPANA_CALLBACK_URL = "https://bingwa-al7a.onrender.com/api/payment-callback"
    else:
        # Local development
        LIPANA_CALLBACK_URL = "http://localhost:5000/api/payment-callback"
    
    # Database configuration for Render
    if 'RENDER' in os.environ:
        DATABASE_PATH = os.path.join(os.getcwd(), 'bingwa.db')
    else:
        os.makedirs(app.instance_path, exist_ok=True)
        DATABASE_PATH = os.path.join(app.instance_path, 'bingwa.db')
    
    # Data Packages
    DATA_PACKAGES = [
        {"id": 1, "size": "1.25 GB", "price": 55, "validity": "midnight", "description": "Valid till midnight"},
        {"id": 2, "size": "250 MB", "price": 20, "validity": "24hrs", "description": "Valid 24 hours"},
        {"id": 3, "size": "1.5 GB", "price": 49, "validity": "3hrs", "description": "Valid 3 hours"},
        {"id": 4, "size": "1 GB", "price": 19, "validity": "1hr", "description": "Valid 1 hour"},
        {"id": 5, "size": "1 GB", "price": 99, "validity": "24hrs", "description": "Valid 24 hours"},
    ]

app.config.from_object(Config)
app.secret_key = app.config['SECRET_KEY']

# Initialize LipaNa client
lipana_client = Lipana(
    api_key=app.config['LIPANA_API_KEY'],
    environment=app.config['LIPANA_ENVIRONMENT']
)

# Database setup
def get_db():
    """Get database connection"""
    conn = sqlite3.connect(app.config['DATABASE_PATH'])
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database with tables"""
    logger.info(f"Initializing database at: {app.config['DATABASE_PATH']}")
    conn = get_db()
    cursor = conn.cursor()
    
    # Transactions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id TEXT UNIQUE,
            phone_number TEXT NOT NULL,
            recipient_number TEXT NOT NULL,
            package_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            lipana_transaction_id TEXT,
            mpesa_receipt_number TEXT,
            result_description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    ''')
    
    # Packages table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            size TEXT NOT NULL,
            price REAL NOT NULL,
            validity TEXT NOT NULL,
            description TEXT,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Insert default packages if not exist
    for package in app.config['DATA_PACKAGES']:
        cursor.execute('''
            INSERT OR IGNORE INTO packages (id, size, price, validity, description)
            VALUES (?, ?, ?, ?, ?)
        ''', (package['id'], package['size'], package['price'], 
              package['validity'], package['description']))
    
    # Audit log table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            details TEXT,
            ip_address TEXT,
            user_agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_phone ON transactions(phone_number)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_lipana ON transactions(lipana_transaction_id)')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

# Initialize database on startup
init_db()

# Helper functions
def log_audit(action, details=None):
    """Log actions to audit table"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO audit_log (action, details, ip_address, user_agent)
        VALUES (?, ?, ?, ?)
    ''', (action, details, request.remote_addr, request.user_agent.string))
    conn.commit()
    conn.close()

def generate_transaction_id():
    """Generate unique transaction ID"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    random_str = secrets.token_hex(3).upper()
    return f"BINGWA-{timestamp}-{random_str}"

def validate_phone_number(phone):
    """Validate Kenyan phone number format"""
    phone = ''.join(filter(str.isdigit, phone))
    
    # LipaNa package expects format like '+254712345678'
    if len(phone) == 12 and phone.startswith('254'):
        return f"+{phone}"
    elif len(phone) == 10 and (phone.startswith('07') or phone.startswith('01')):
        return f"+254{phone[1:]}"
    elif len(phone) == 9 and (phone.startswith('7') or phone.startswith('1')):
        return f"+254{phone}"
    else:
        return None

# Routes
@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index.html')

@app.route('/api/packages')
def get_packages():
    """Get available data packages"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM packages WHERE is_active = 1 ORDER BY price')
    packages = cursor.fetchall()
    conn.close()
    
    packages_list = []
    for pkg in packages:
        packages_list.append({
            'id': pkg['id'],
            'size': pkg['size'],
            'price': pkg['price'],
            'validity': pkg['validity'],
            'description': pkg['description']
        })
    
    return jsonify({
        'success': True,
        'packages': packages_list
    })

@app.route('/api/initiate-payment', methods=['POST'])
def initiate_payment():
    """Initiate STK Push payment using LipaNa Python package"""
    try:
        data = request.json
        phone = data.get('phone')
        package_id = data.get('package_id')
        recipient_phone = data.get('recipient_phone', phone)
        
        if not phone or not package_id:
            return jsonify({
                'success': False,
                'message': 'Phone number and package selection are required'
            }), 400
        
        formatted_phone = validate_phone_number(phone)
        formatted_recipient = validate_phone_number(recipient_phone) if recipient_phone else formatted_phone
        
        if not formatted_phone:
            return jsonify({
                'success': False,
                'message': 'Invalid phone number format. Use 07XXXXXXXX or 2547XXXXXXXX'
            }), 400
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM packages WHERE id = ? AND is_active = 1', (package_id,))
        package = cursor.fetchone()
        
        if not package:
            conn.close()
            return jsonify({
                'success': False,
                'message': 'Invalid package selected'
            }), 400
        
        # Check daily purchase limit
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT COUNT(*) as count FROM transactions 
            WHERE phone_number = ? AND date(created_at) = ? AND status = 'completed'
        ''', (formatted_phone.replace('+', ''), today))
        
        daily_count = cursor.fetchone()['count']
        if daily_count >= 1:
            conn.close()
            return jsonify({
                'success': False,
                'message': 'You can only purchase once per day per line'
            }), 400
        
        # Create transaction
        transaction_id = generate_transaction_id()
        
        cursor.execute('''
            INSERT INTO transactions (
                transaction_id, phone_number, recipient_number, 
                package_id, amount, status
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            transaction_id, formatted_phone.replace('+', ''), formatted_recipient.replace('+', ''),
            package_id, package['price'], 'pending'
        ))
        
        transaction_db_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        log_audit('payment_initiated', f'Transaction: {transaction_id}, Phone: {formatted_phone}')
        
        # Initiate STK Push using LipaNa package
        try:
            # Using the LipaNa package as per the sample code
            stk_response = lipana_client.transactions.initiate_stk_push(
                phone=formatted_phone,  # Should be in format '+254712345678'
                amount=int(package['price']),
                account_reference=transaction_id[:20],
                transaction_desc=f"{package['size']} - {package['validity']}"[:13]
            )
            
            logger.info(f"✅ LipaNa STK Response: {stk_response}")
            
            # Extract transaction ID from response
            lipana_transaction_id = stk_response.get('transactionId') or stk_response.get('id')
            
            if lipana_transaction_id:
                # Update transaction with LipaNa transaction ID
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE transactions 
                    SET lipana_transaction_id = ?
                    WHERE id = ?
                ''', (lipana_transaction_id, transaction_db_id))
                conn.commit()
                conn.close()
                
                return jsonify({
                    'success': True,
                    'message': 'Payment request sent to your phone. Please check and enter your PIN.',
                    'transaction_id': transaction_id,
                    'lipana_transaction_id': lipana_transaction_id,
                    'data': {
                        'size': package['size'],
                        'price': package['price'],
                        'validity': package['validity']
                    }
                })
            else:
                raise Exception("No transaction ID in response")
                
        except Exception as e:
            logger.error(f"❌ LipaNa STK Push failed: {str(e)}")
            
            # Update transaction status to failed
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE transactions 
                SET status = 'failed', result_description = ?
                WHERE id = ?
            ''', (str(e), transaction_db_id))
            conn.commit()
            conn.close()
            
            return jsonify({
                'success': False,
                'message': f'Failed to initiate payment: {str(e)}'
            }), 500
            
    except Exception as e:
        logger.error(f"Error initiating payment: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Internal server error: {str(e)}'
        }), 500

@app.route('/api/payment-callback', methods=['POST'])
def payment_callback():
    """Callback endpoint for LipaNa.Dev payment results"""
    try:
        data = request.json
        logger.info(f"💰 Payment callback received: {data}")
        
        # Extract callback data - adjust based on actual LipaNa callback format
        transaction_id = data.get('transactionId') or data.get('id')
        status = data.get('status', '').lower()
        mpesa_receipt = data.get('mpesaReceiptNumber') or data.get('receipt')
        result_desc = data.get('resultDesc') or data.get('message', '')
        
        if not transaction_id:
            logger.error("No transaction ID in callback")
            return jsonify({'success': False, 'message': 'No transaction ID'}), 400
        
        conn = get_db()
        cursor = conn.cursor()
        
        # Find transaction by lipana_transaction_id
        cursor.execute('SELECT * FROM transactions WHERE lipana_transaction_id = ?', (transaction_id,))
        transaction = cursor.fetchone()
        
        if not transaction:
            logger.error(f"Transaction not found for LipaNa ID: {transaction_id}")
            conn.close()
            return jsonify({'success': False, 'message': 'Transaction not found'}), 404
        
        # Determine status
        if status in ['success', 'completed', 'paid']:
            db_status = 'completed'
            logger.info(f"✅ Payment successful: {transaction['transaction_id']}")
        else:
            db_status = 'failed'
            logger.info(f"❌ Payment failed: {transaction['transaction_id']}")
        
        # Update transaction
        cursor.execute('''
            UPDATE transactions 
            SET status = ?, 
                mpesa_receipt_number = ?,
                result_description = ?,
                updated_at = CURRENT_TIMESTAMP,
                completed_at = CASE WHEN ? = 'completed' THEN CURRENT_TIMESTAMP ELSE NULL END
            WHERE id = ?
        ''', (db_status, mpesa_receipt, result_desc, db_status, transaction['id']))
        
        conn.commit()
        conn.close()
        
        log_audit('payment_callback', f'Transaction: {transaction["transaction_id"]}, Status: {db_status}')
        
        return jsonify({'success': True, 'message': 'Callback processed successfully'})
        
    except Exception as e:
        logger.error(f"Error processing callback: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/check-payment-status', methods=['POST'])
def check_payment_status():
    """Check payment status for a transaction"""
    try:
        data = request.json
        transaction_id = data.get('transaction_id')
        lipana_transaction_id = data.get('lipana_transaction_id')
        
        if not transaction_id and not lipana_transaction_id:
            return jsonify({'success': False, 'message': 'Transaction ID required'}), 400
        
        conn = get_db()
        cursor = conn.cursor()
        
        if transaction_id:
            cursor.execute('SELECT * FROM transactions WHERE transaction_id = ?', (transaction_id,))
        else:
            cursor.execute('SELECT * FROM transactions WHERE lipana_transaction_id = ?', (lipana_transaction_id,))
        
        transaction = cursor.fetchone()
        
        if not transaction:
            conn.close()
            return jsonify({'success': False, 'message': 'Transaction not found'}), 404
        
        cursor.execute('SELECT * FROM packages WHERE id = ?', (transaction['package_id'],))
        package = cursor.fetchone()
        conn.close()
        
        return jsonify({
            'success': True,
            'transaction': {
                'id': transaction['transaction_id'],
                'phone': transaction['phone_number'],
                'recipient': transaction['recipient_number'],
                'amount': transaction['amount'],
                'status': transaction['status'],
                'mpesa_receipt': transaction['mpesa_receipt_number'],
                'lipana_transaction_id': transaction['lipana_transaction_id'],
                'created_at': transaction['created_at'],
                'completed_at': transaction['completed_at']
            },
            'package': {
                'size': package['size'] if package else 'Unknown',
                'validity': package['validity'] if package else 'Unknown'
            }
        })
        
    except Exception as e:
        logger.error(f"Error checking status: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/api/manual-payment', methods=['POST'])
def manual_payment():
    """Record manual payment"""
    try:
        data = request.json
        phone = data.get('phone')
        package_id = data.get('package_id')
        mpesa_code = data.get('mpesa_code')
        
        if not phone or not package_id or not mpesa_code:
            return jsonify({'success': False, 'message': 'All fields are required'}), 400
        
        formatted_phone = validate_phone_number(phone)
        if not formatted_phone:
            return jsonify({'success': False, 'message': 'Invalid phone number'}), 400
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM packages WHERE id = ?', (package_id,))
        package = cursor.fetchone()
        
        if not package:
            conn.close()
            return jsonify({'success': False, 'message': 'Invalid package'}), 400
        
        transaction_id = generate_transaction_id()
        
        cursor.execute('''
            INSERT INTO transactions (
                transaction_id, phone_number, recipient_number, 
                package_id, amount, status, mpesa_receipt_number,
                result_description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            transaction_id, formatted_phone.replace('+', ''), formatted_phone.replace('+', ''),
            package_id, package['price'], 'pending_verification',
            mpesa_code, 'Manual payment - pending verification'
        ))
        
        conn.commit()
        conn.close()
        
        log_audit('manual_payment', f'Transaction: {transaction_id}, M-PESA: {mpesa_code}')
        
        return jsonify({
            'success': True,
            'message': 'Manual payment recorded. Data will be loaded after verification.',
            'transaction_id': transaction_id
        })
        
    except Exception as e:
        logger.error(f"Error recording manual payment: {str(e)}")
        return jsonify({'success': False, 'message': f'Error: {str(e)}'}), 500

@app.route('/api/test-lipana', methods=['GET'])
def test_lipana():
    """Test LipaNa API connection using the package"""
    try:
        # Test with a minimal STK push (using test phone)
        test_phone = '+254708374149'  # LipaNa test number
        
        # Try to initiate a test STK push with amount 1 KES
        stk_response = lipana_client.transactions.initiate_stk_push(
            phone=test_phone,
            amount=1,
            account_reference='TEST',
            transaction_desc='API Test'
        )
        
        return jsonify({
            'success': True,
            'message': 'LipaNa package test completed',
            'response': stk_response,
            'config': {
                'environment': app.config['LIPANA_ENVIRONMENT'],
                'callback_url': app.config['LIPANA_CALLBACK_URL'],
                'business_shortcode': app.config['BUSINESS_SHORTCODE'],
                'api_key_configured': bool(app.config['LIPANA_API_KEY'])
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'LipaNa test failed: {str(e)}',
            'config': {
                'environment': app.config['LIPANA_ENVIRONMENT'],
                'callback_url': app.config['LIPANA_CALLBACK_URL']
            }
        }), 500

@app.route('/api/create-payment-link', methods=['POST'])
def create_payment_link():
    """Create a payment link (alternative to STK push)"""
    try:
        data = request.json
        package_id = data.get('package_id')
        
        if not package_id:
            return jsonify({'success': False, 'message': 'Package ID required'}), 400
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM packages WHERE id = ? AND is_active = 1', (package_id,))
        package = cursor.fetchone()
        conn.close()
        
        if not package:
            return jsonify({'success': False, 'message': 'Invalid package'}), 400
        
        # Create payment link using LipaNa
        payment_link = lipana_client.payment_links.create(
            title=f"Bingwa Data - {package['size']}",
            amount=int(package['price']),
            currency='KES'
        )
        
        return jsonify({
            'success': True,
            'payment_link': payment_link.get('url'),
            'data': {
                'size': package['size'],
                'price': package['price']
            }
        })
        
    except Exception as e:
        logger.error(f"Error creating payment link: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/debug')
def debug_info():
    """Debug endpoint"""
    db_status = "OK"
    try:
        conn = get_db()
        conn.close()
    except Exception as e:
        db_status = f"Error: {str(e)}"
    
    return jsonify({
        'success': True,
        'environment': {
            'on_render': 'RENDER' in os.environ,
            'render_external_hostname': os.environ.get('RENDER_EXTERNAL_HOSTNAME'),
            'callback_url': app.config['LIPANA_CALLBACK_URL'],
            'database_path': app.config['DATABASE_PATH'],
            'database_status': db_status,
            'business_shortcode': app.config['BUSINESS_SHORTCODE'],
            'api_key_configured': bool(app.config['LIPANA_API_KEY']),
            'api_key_length': len(app.config['LIPANA_API_KEY']) if app.config['LIPANA_API_KEY'] else 0,
            'lipana_environment': app.config['LIPANA_ENVIRONMENT']
        },
        'app': {
            'name': app.config['BUSINESS_NAME'],
            'packages_count': len(app.config['DATA_PACKAGES'])
        }
    })

@app.route('/api/stats')
def get_stats():
    """Get system statistics"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) as total FROM transactions')
    total_transactions = cursor.fetchone()['total']
    
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute('SELECT COUNT(*) as today_count FROM transactions WHERE date(created_at) = ?', (today,))
    today_transactions = cursor.fetchone()['today_count']
    
    cursor.execute('SELECT COUNT(*) as successful FROM transactions WHERE status = "completed"')
    successful_transactions = cursor.fetchone()['successful']
    
    cursor.execute('SELECT SUM(amount) as revenue FROM transactions WHERE status = "completed"')
    revenue_result = cursor.fetchone()['revenue']
    total_revenue = revenue_result if revenue_result else 0
    
    conn.close()
    
    return jsonify({
        'success': True,
        'stats': {
            'total_transactions': total_transactions,
            'today_transactions': today_transactions,
            'successful_transactions': successful_transactions,
            'total_revenue': total_revenue,
            'business_name': app.config['BUSINESS_NAME']
        }
    })

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Bingwa Data Sales',
        'timestamp': datetime.now().isoformat()
    })

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'success': False, 'message': 'Resource not found'}), 404

@app.errorhandler(500)
def server_error(error):
    return jsonify({'success': False, 'message': 'Internal server error'}), 500

if __name__ == '__main__':
    print("=" * 60)
    print("🚀 BINGWA DATA SALES - LIPANA PACKAGE VERSION")
    print("=" * 60)
    print(f"🏪 Business: {app.config['BUSINESS_NAME']}")
    print(f"💰 Till Number: {app.config['BUSINESS_SHORTCODE']}")
    print(f"📞 Callback URL: {app.config['LIPANA_CALLBACK_URL']}")
    print(f"🌐 LipaNa Environment: {app.config['LIPANA_ENVIRONMENT']}")
    print(f"🔑 API Key: {'✅ Configured' if app.config['LIPANA_API_KEY'] else '❌ Missing'}")
    print("=" * 60)
    print("📱 Test your setup: /api/test-lipana")
    print("🔍 Debug info: /api/debug")
    print("💳 Create payment link: /api/create-payment-link")
    print("=" * 60)
    
    port = int(os.environ.get('PORT', 5000))
    app.run(
        host='0.0.0.0',
        port=port,
        debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    )
