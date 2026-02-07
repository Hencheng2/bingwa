"""
Bingwa Data Sales - Complete Backend with STK Push Integration
Optimized for Render.com deployment
"""
import os
import sqlite3
from datetime import datetime, timedelta
import logging
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import requests
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
    # LipaNa.Dev API Configuration
    LIPANA_API_KEY = os.environ.get('LIPANA_API_KEY', 'lip_sk_live_a318ed18e46db96f461830a4c282ff3f55feeca84f9b6433c6ac2a47525c4b32')
    LIPANA_BASE_URL = "https://api.lipana.dev/v1"
    
    # Business Configuration
    BUSINESS_SHORTCODE = os.environ.get('LIPANA_BUSINESS_SHORTCODE', '4864614')
    BUSINESS_NAME = "BINGWA DATA SALES"
    
    # Secret Key from environment or generate
    SECRET_KEY = os.environ.get('SECRET_KEY', 'd15a3f8c9e2b7a1d4f6c8a9b3e5d7f2a1c4e8b9d3f6a2c5e8b1d4f7a9c3e6b2d8')
    
    # Determine callback URL based on environment
    if 'RENDER' in os.environ:
        # Running on Render.com
        RENDER_EXTERNAL_HOSTNAME = os.environ.get('https://bingwa-al7a.onrender.com/')
        if RENDER_EXTERNAL_HOSTNAME:
            LIPANA_CALLBACK_URL = f"https://bingwa-al7a.onrender.com/api/payment-callback"
        else:
            # Fallback for Render
            LIPANA_CALLBACK_URL = "https://bingwa-al7a.onrender.com/api/payment-callback"
    else:
        # Local development
        LIPANA_CALLBACK_URL = "http://localhost:5000/api/payment-callback"
    
    # Database configuration for Render - FIXED
    if 'RENDER' in os.environ:
        # Use current directory for database
        DATABASE_PATH = os.path.join(os.getcwd(), 'bingwa.db')
    else:
        # Local development
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
            checkout_request_id TEXT,
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
    
    # Create indexes for better performance
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_phone ON transactions(phone_number)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_transactions_checkout ON transactions(checkout_request_id)')
    
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
    """Validate Kenyan phone number format for LipaNa.Dev"""
    # Remove any non-digit characters
    phone = ''.join(filter(str.isdigit, phone))
    
    # For LipaNa.Dev, try international format first
    if len(phone) == 12 and phone.startswith('254'):
        return phone
    elif len(phone) == 10 and phone.startswith('07'):
        return '254' + phone[1:]
    elif len(phone) == 9 and phone.startswith('7'):
        return '254' + phone
    elif len(phone) >= 9 and len(phone) <= 12:
        return phone
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
    """Initiate STK Push payment using actual LipaNa.Dev API"""
    try:
        data = request.json
        phone = data.get('phone')
        package_id = data.get('package_id')
        recipient_phone = data.get('recipient_phone', phone)
        
        # Validate input
        if not phone or not package_id:
            return jsonify({
                'success': False,
                'message': 'Phone number and package selection are required'
            }), 400
        
        # Validate phone numbers
        formatted_phone = validate_phone_number(phone)
        formatted_recipient = validate_phone_number(recipient_phone)
        
        if not formatted_phone:
            return jsonify({
                'success': False,
                'message': 'Invalid phone number format. Use 07XXXXXXXX or 2547XXXXXXXX'
            }), 400
        
        # Get package details
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM packages WHERE id = ?', (package_id,))
        package = cursor.fetchone()
        
        if not package:
            conn.close()
            return jsonify({
                'success': False,
                'message': 'Invalid package selected'
            }), 400
        
        # Check if user has purchased today
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT COUNT(*) as count FROM transactions 
            WHERE phone_number = ? AND date(created_at) = ? AND status = 'completed'
        ''', (formatted_phone, today))
        
        daily_count = cursor.fetchone()['count']
        if daily_count >= 1:
            conn.close()
            return jsonify({
                'success': False,
                'message': 'You can only purchase once per day per line'
            }), 400
        
        # Generate transaction ID
        transaction_id = generate_transaction_id()
        
        # Create transaction record
        cursor.execute('''
            INSERT INTO transactions (
                transaction_id, phone_number, recipient_number, 
                package_id, amount, status
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            transaction_id, formatted_phone, formatted_recipient,
            package_id, package['price'], 'pending'
        ))
        
        transaction_db_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Log audit
        log_audit('payment_initiated', f'Transaction: {transaction_id}, Phone: {formatted_phone}')
        
        # Initiate STK Push via REAL LipaNa.Dev API
        lipana_response = initiate_lipana_stk_push(
            phone=formatted_phone,
            amount=package['price'],
            transaction_id=transaction_id,
            description=f"{package['size']} Data Bundle - Bingwa Sokoni"
        )
        
        if lipana_response.get('success'):
            # Update transaction with checkout request ID
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE transactions 
                SET checkout_request_id = ?
                WHERE id = ?
            ''', (lipana_response.get('checkout_request_id'), transaction_db_id))
            conn.commit()
            conn.close()
            
            return jsonify({
                'success': True,
                'message': 'Payment request sent to your phone. Please check and enter your PIN.',
                'transaction_id': transaction_id,
                'checkout_request_id': lipana_response.get('checkout_request_id'),
                'data': {
                    'size': package['size'],
                    'price': package['price'],
                    'validity': package['validity']
                }
            })
        else:
            # Update transaction status to failed
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE transactions 
                SET status = 'failed', result_description = ?
                WHERE id = ?
            ''', (lipana_response.get('message', 'STK Push failed'), transaction_db_id))
            conn.commit()
            conn.close()
            
            return jsonify({
                'success': False,
                'message': lipana_response.get('message', 'Failed to initiate payment')
            }), 500
            
    except Exception as e:
        logger.error(f"Error initiating payment: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Internal server error: {str(e)}'
        }), 500

def initiate_lipana_stk_push(phone, amount, transaction_id, description):
    """
    Initiate STK Push via ACTUAL LipaNa.Dev API
    """
    
    headers = {
        'Authorization': f'Bearer {app.config["LIPANA_API_KEY"]}',
        'Content-Type': 'application/json'
    }
    
    payload = {
        'phone': phone,
        'amount': amount,
        'description': description,
        'callback_url': app.config['LIPANA_CALLBACK_URL'],
        'reference': transaction_id,
        'business_shortcode': app.config['BUSINESS_SHORTCODE']
    }
    
    logger.info(f"Attempting STK Push to LipaNa.Dev")
    logger.info(f"Payload: {payload}")
    
    try:
        response = requests.post(
            f'{app.config["LIPANA_BASE_URL"]}/stk/push',
            headers=headers,
            json=payload,
            timeout=30
        )
        
        logger.info(f"LipaNa.Dev Response Status: {response.status_code}")
        logger.info(f"LipaNa.Dev Response: {response.text}")
        
        response_data = response.json()
        
        if response.status_code == 200 and response_data.get('success'):
            return {
                'success': True,
                'checkout_request_id': response_data.get('checkout_request_id'),
                'customer_message': response_data.get('customer_message', 'Request sent successfully')
            }
        else:
            error_msg = response_data.get('message', f'Payment request failed with status {response.status_code}')
            logger.error(f"LipaNa.Dev API error: {error_msg}")
            return {
                'success': False,
                'message': error_msg
            }
            
    except requests.exceptions.Timeout:
        logger.error("LipaNa.Dev API timeout")
        return {
            'success': False,
            'message': 'Payment service timeout. Please try again.'
        }
    except requests.exceptions.ConnectionError:
        logger.error("LipaNa.Dev API connection error")
        return {
            'success': False,
            'message': 'Cannot connect to payment service. Please try again later.'
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"LipaNa.Dev API request error: {str(e)}")
        return {
            'success': False,
            'message': 'Payment service temporarily unavailable. Please try manual payment.'
        }
    except Exception as e:
        logger.error(f"Unexpected error in STK Push: {str(e)}")
        return {
            'success': False,
            'message': 'An unexpected error occurred. Please try manual payment.'
        }

@app.route('/api/payment-callback', methods=['POST'])
def payment_callback():
    """
    Callback endpoint for LipaNa.Dev to send payment results
    """
    try:
        data = request.json
        logger.info(f"Payment callback received: {data}")
        
        # Parse callback data from LipaNa.Dev
        result_code = data.get('ResultCode', '1')
        result_desc = data.get('ResultDesc', '')
        checkout_request_id = data.get('CheckoutRequestID', '')
        mpesa_receipt = data.get('MpesaReceiptNumber', '')
        phone = data.get('PhoneNumber', '')
        amount = data.get('Amount', 0)
        reference = data.get('reference', '')
        
        # Find transaction
        conn = get_db()
        cursor = conn.cursor()
        
        if checkout_request_id:
            cursor.execute('SELECT * FROM transactions WHERE checkout_request_id = ?', (checkout_request_id,))
        else:
            cursor.execute('SELECT * FROM transactions WHERE transaction_id = ?', (reference,))
        
        transaction = cursor.fetchone()
        
        if not transaction:
            logger.error(f"Transaction not found. Checkout ID: {checkout_request_id}, Reference: {reference}")
            conn.close()
            return jsonify({'success': False, 'message': 'Transaction not found'}), 404
        
        # Update transaction based on result
        if result_code == '0':
            # Payment successful
            status = 'completed'
            result_description = 'Payment completed successfully'
            
            logger.info(f"Payment successful for transaction {transaction['transaction_id']}")
            logger.info(f"Data should be loaded to: {transaction['recipient_number']}")
            
            # TODO: Integrate with your data loading system here
            # allocate_data_bundle(transaction['recipient_number'], transaction['amount'], transaction['transaction_id'])
            
        else:
            # Payment failed
            status = 'failed'
            result_description = result_desc
        
        # Update transaction in database
        cursor.execute('''
            UPDATE transactions 
            SET status = ?, 
                mpesa_receipt_number = ?,
                result_description = ?,
                updated_at = CURRENT_TIMESTAMP,
                completed_at = CASE WHEN ? = 'completed' THEN CURRENT_TIMESTAMP ELSE NULL END
            WHERE id = ?
        ''', (status, mpesa_receipt, result_description, status, transaction['id']))
        
        conn.commit()
        conn.close()
        
        # Log audit
        log_audit('payment_callback', f'Transaction: {transaction["transaction_id"]}, Status: {status}')
        
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
        checkout_request_id = data.get('checkout_request_id')
        
        if not transaction_id and not checkout_request_id:
            return jsonify({
                'success': False,
                'message': 'Transaction ID or Checkout Request ID required'
            }), 400
        
        conn = get_db()
        cursor = conn.cursor()
        
        if transaction_id:
            cursor.execute('SELECT * FROM transactions WHERE transaction_id = ?', (transaction_id,))
        else:
            cursor.execute('SELECT * FROM transactions WHERE checkout_request_id = ?', (checkout_request_id,))
        
        transaction = cursor.fetchone()
        conn.close()
        
        if not transaction:
            return jsonify({
                'success': False,
                'message': 'Transaction not found'
            }), 404
        
        # Get package details
        conn = get_db()
        cursor = conn.cursor()
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
                'created_at': transaction['created_at'],
                'completed_at': transaction['completed_at']
            },
            'package': {
                'size': package['size'] if package else 'Unknown',
                'validity': package['validity'] if package else 'Unknown'
            }
        })
        
    except Exception as e:
        logger.error(f"Error checking payment status: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500

@app.route('/api/manual-payment', methods=['POST'])
def manual_payment():
    """Record manual payment (when STK Push fails)"""
    try:
        data = request.json
        phone = data.get('phone')
        package_id = data.get('package_id')
        recipient_phone = data.get('recipient_phone', phone)
        mpesa_code = data.get('mpesa_code')
        
        # Validate
        if not phone or not package_id or not mpesa_code:
            return jsonify({
                'success': False,
                'message': 'Phone, package, and M-PESA code are required'
            }), 400
        
        formatted_phone = validate_phone_number(phone)
        if not formatted_phone:
            return jsonify({
                'success': False,
                'message': 'Invalid phone number format'
            }), 400
        
        # Get package
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM packages WHERE id = ?', (package_id,))
        package = cursor.fetchone()
        
        if not package:
            conn.close()
            return jsonify({
                'success': False,
                'message': 'Invalid package'
            }), 400
        
        # Check daily limit
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT COUNT(*) as count FROM transactions 
            WHERE phone_number = ? AND date(created_at) = ? AND status = 'completed'
        ''', (formatted_phone, today))
        
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
                package_id, amount, status, mpesa_receipt_number,
                result_description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            transaction_id, formatted_phone, recipient_phone,
            package_id, package['price'], 'pending_verification',
            mpesa_code, 'Manual payment - pending verification'
        ))
        
        conn.commit()
        conn.close()
        
        # Log audit
        log_audit('manual_payment', f'Transaction: {transaction_id}, M-PESA: {mpesa_code}')
        
        return jsonify({
            'success': True,
            'message': 'Manual payment recorded. Data will be loaded after verification.',
            'transaction_id': transaction_id,
            'instructions': 'Our team will verify your payment and load the data within 15 minutes. For immediate assistance, call 0718 257 264'
        })
        
    except Exception as e:
        logger.error(f"Error recording manual payment: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500

@app.route('/api/debug')
def debug_info():
    """Debug endpoint to check configuration"""
    return jsonify({
        'success': True,
        'environment': {
            'on_render': 'RENDER' in os.environ,
            'render_external_hostname': os.environ.get('RENDER_EXTERNAL_HOSTNAME'),
            'callback_url': app.config['LIPANA_CALLBACK_URL'],
            'database_path': app.config['DATABASE_PATH'],
            'business_shortcode': app.config['BUSINESS_SHORTCODE'],
            'api_key_configured': bool(app.config['LIPANA_API_KEY']),
            'api_key_length': len(app.config['LIPANA_API_KEY']) if app.config['LIPANA_API_KEY'] else 0
        },
        'app': {
            'name': app.config['BUSINESS_NAME'],
            'packages_count': len(app.config['DATA_PACKAGES'])
        }
    })

@app.route('/api/test-lipana', methods=['GET'])
def test_lipana():
    """Test endpoint to verify LipaNa.Dev connection"""
    try:
        headers = {
            'Authorization': f'Bearer {app.config["LIPANA_API_KEY"]}',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(
            f'{app.config["LIPANA_BASE_URL"]}/status',
            headers=headers,
            timeout=10
        )
        
        return jsonify({
            'success': True,
            'lipana_status': response.status_code,
            'response': response.text,
            'config': {
                'callback_url': app.config['LIPANA_CALLBACK_URL'],
                'business_shortcode': app.config['BUSINESS_SHORTCODE'],
                'api_key_configured': bool(app.config['LIPANA_API_KEY'])
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'LipaNa.Dev connection failed: {str(e)}',
            'config': {
                'callback_url': app.config['LIPANA_CALLBACK_URL'],
                'business_shortcode': app.config['BUSINESS_SHORTCODE']
            }
        }), 500

@app.route('/api/stats')
def get_stats():
    """Get system statistics"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Total transactions
    cursor.execute('SELECT COUNT(*) as total FROM transactions')
    total_transactions = cursor.fetchone()['total']
    
    # Today's transactions
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute('SELECT COUNT(*) as today_count FROM transactions WHERE date(created_at) = ?', (today,))
    today_transactions = cursor.fetchone()['today_count']
    
    # Successful transactions
    cursor.execute('SELECT COUNT(*) as successful FROM transactions WHERE status = "completed"')
    successful_transactions = cursor.fetchone()['successful']
    
    # Total revenue
    cursor.execute('SELECT SUM(amount) as revenue FROM transactions WHERE status = "completed"')
    revenue_result = cursor.fetchone()['revenue']
    total_revenue = revenue_result if revenue_result else 0
    
    # Pending transactions
    cursor.execute('SELECT COUNT(*) as pending FROM transactions WHERE status = "pending"')
    pending_transactions = cursor.fetchone()['pending']
    
    conn.close()
    
    return jsonify({
        'success': True,
        'stats': {
            'total_transactions': total_transactions,
            'today_transactions': today_transactions,
            'successful_transactions': successful_transactions,
            'pending_transactions': pending_transactions,
            'total_revenue': total_revenue,
            'business_name': app.config['BUSINESS_NAME'],
            'callback_url': app.config['LIPANA_CALLBACK_URL'],
            'on_render': 'RENDER' in os.environ
        }
    })

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'success': False,
        'message': 'Resource not found'
    }), 404

@app.errorhandler(500)
def server_error(error):
    return jsonify({
        'success': False,
        'message': 'Internal server error'
    }), 500

# Health check endpoint for Render
@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'service': 'Bingwa Data Sales',
        'timestamp': datetime.now().isoformat()
    })

if __name__ == '__main__':
    # Display startup information
    print("=" * 60)
    print("BINGWA DATA SALES SYSTEM")
    print("=" * 60)
    print(f"Business: {app.config['BUSINESS_NAME']}")
    print(f"Till Number: {app.config['BUSINESS_SHORTCODE']}")
    print(f"Callback URL: {app.config['LIPANA_CALLBACK_URL']}")
    print(f"Database: {app.config['DATABASE_PATH']}")
    print(f"On Render.com: {'RENDER' in os.environ}")
    print("=" * 60)
    
    # Run the app
    port = int(os.environ.get('PORT', 5000))
    app.run(
        host='0.0.0.0',
        port=port,
        debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    )



