from flask import Flask, render_template, request, jsonify, send_from_directory
import sqlite3
import os
from werkzeug.utils import secure_filename
from google.cloud import vision
import io
import re
from datetime import datetime

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['DATABASE'] = 'library.db'

# Create upload folder if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ==================== GOOGLE VISION API SETUP WITH API KEY ====================

# Your API key
VISION_API_KEY = "AIzaSyBjpGpW0HER87V_TiC8HAWNRUwCBRvhQ94"

# Initialize Vision API client with API key
try:
    client = vision.ImageAnnotatorClient(
        client_options={"api_key": VISION_API_KEY}
    )
    print("✓ Vision API client initialized successfully with API key")
except Exception as e:
    print(f"✗ Warning: Could not initialize Vision API client: {e}")
    client = None

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# ==================== DATABASE FUNCTIONS ====================

def get_db_connection():
    """Create a database connection"""
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Initialize the database with required tables"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create STUDENTS table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            adm_no TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            stream TEXT
        )
    ''')
    
    # Create BOOKS table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bookname TEXT,
            bookcode TEXT UNIQUE NOT NULL
        )
    ''')
    
    # Create ISSUED table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS issued (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_name TEXT NOT NULL,
            adm_no TEXT NOT NULL,
            bookname TEXT,
            stream TEXT,
            date_issued DATE DEFAULT CURRENT_DATE,
            FOREIGN KEY (adm_no) REFERENCES students(adm_no)
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✓ Database initialized successfully!")

def add_student(adm_no, name, stream):
    """Add a new student to the database"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT OR IGNORE INTO students (adm_no, name, stream)
            VALUES (?, ?, ?)
        ''', (adm_no, name, stream))
        conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"Error adding student: {e}")
        return False
    finally:
        conn.close()

def add_book(bookname, bookcode):
    """Add a new book to the database"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT OR IGNORE INTO books (bookname, bookcode)
            VALUES (?, ?)
        ''', (bookname, bookcode))
        conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"Error adding book: {e}")
        return False
    finally:
        conn.close()

def issue_book(student_name, adm_no, bookname, stream, date_issued):
    """Issue a book to a student"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO issued (student_name, adm_no, bookname, stream, date_issued)
            VALUES (?, ?, ?, ?, ?)
        ''', (student_name, adm_no, bookname, stream, date_issued))
        conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"Error issuing book: {e}")
        return False
    finally:
        conn.close()

def get_all_students():
    """Retrieve all students"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM students ORDER BY adm_no')
    students = cursor.fetchall()
    conn.close()
    return students

def get_all_books():
    """Retrieve all books"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM books ORDER BY bookcode')
    books = cursor.fetchall()
    conn.close()
    return books

def get_all_issued():
    """Retrieve all issued books"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT i.*, s.stream 
        FROM issued i 
        LEFT JOIN students s ON i.adm_no = s.adm_no 
        ORDER BY i.date_issued DESC
    ''')
    issued = cursor.fetchall()
    conn.close()
    return issued

def get_statistics():
    """Get library statistics"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM students')
    total_students = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM books')
    total_books = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM issued')
    total_issued = cursor.fetchone()[0]
    
    conn.close()
    return total_students, total_books, total_issued

# ==================== VISION API FUNCTIONS ====================

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_bookcode(text):
    """Extract book code from text (like 143/22, 20/326, RSS/143/22)"""
    # Pattern for book codes (numbers/number format)
    patterns = [
        r'(\d{1,4}/\d{1,4})',  # Matches 143/22, 20/326
        r'RSS?/?(\d{1,4}/\d{1,4})',  # Matches RSS/143/22, RSS143/22
        r'(\d{1,4}/\d{1,4})/\w+',  # Matches 143/22/R
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1) if pattern == patterns[0] else match.group(1)
    return None

def clean_name(name_text):
    """Clean and validate name"""
    # Remove numbers and special characters
    name = re.sub(r'[\d/\\]', ' ', name_text)
    # Remove extra spaces
    name = ' '.join(name.split())
    # Check if it's a valid name (at least 3 letters)
    if len(name) >= 3 and re.search(r'[A-Za-z]', name):
        return name
    return None

def parse_student_records(text):
    """Parse all student records from the extracted text"""
    lines = text.split('\n')
    records = []
    current_record = {}
    
    # Find the start of the table (after headers)
    start_idx = 0
    for i, line in enumerate(lines):
        if 'ADM.NO' in line.upper() or 'ADM NO' in line.upper():
            start_idx = i + 1
            break
    
    # Process each line
    i = start_idx
    while i < len(lines):
        line = lines[i].strip()
        
        # Skip empty lines and headers
        if not line or line.startswith('TITLE') or line.startswith('TEACHER'):
            i += 1
            continue
        
        # Look for patterns in the line
        # Check for admission number (2-4 digits)
        adm_match = re.search(r'\b(\d{2,4})\b', line)
        
        if adm_match:
            adm_no = adm_match.group(1)
            
            # Don't treat years as admission numbers
            if adm_no.startswith(('19', '20')) and len(adm_no) == 4:
                i += 1
                continue
            
            # Get the rest of the line after the admission number
            rest = line[line.find(adm_no) + len(adm_no):].strip()
            
            # Look for book code in this line or next lines
            bookcode = extract_bookcode(line)
            
            # If no bookcode found, check next few lines
            if not bookcode:
                for j in range(1, 4):
                    if i + j < len(lines):
                        bookcode = extract_bookcode(lines[i + j])
                        if bookcode:
                            break
            
            # Extract name (everything before the bookcode)
            name = None
            if bookcode:
                # Remove bookcode from the line to get name
                name_part = line.replace(adm_no, '').replace(bookcode, '').strip()
                # Also remove any RSS/ patterns
                name_part = re.sub(r'RSS?/?\d+/\d+', '', name_part, flags=re.IGNORECASE).strip()
                name = clean_name(name_part)
            
            # If we have all required data, create a record
            if adm_no and name and bookcode:
                record = {
                    'adm_no': adm_no,
                    'name': name,
                    'bookcode': bookcode,
                    'stream': 'Unknown'  # Will be updated from header
                }
                records.append(record)
                print(f"Found record: {record}")
        
        i += 1
    
    # Try to extract stream from header
    stream = 'Unknown'
    for line in lines:
        if 'CLASS:' in line:
            stream_match = re.search(r'CLASS:\s*([^\s]+)', line)
            if stream_match:
                stream = stream_match.group(1)
                break
    
    # Update stream for all records
    for record in records:
        record['stream'] = stream
    
    return records

def process_image(image_path):
    """Process image using Google Vision API to extract multiple records"""
    if not client:
        return {'error': 'Vision API client not initialized. Please check your API key.'}
    
    try:
        with io.open(image_path, 'rb') as image_file:
            content = image_file.read()
        
        image = vision.Image(content=content)
        
        # Perform text detection
        response = client.text_detection(image=image)
        texts = response.text_annotations
        
        if response.error.message:
            return {'error': response.error.message}
        
        if not texts:
            return {'error': 'No text detected in image'}
        
        # Get all text
        full_text = texts[0].description
        print("Full extracted text:", full_text)  # Debug print
        
        # Parse records from the text
        records = parse_student_records(full_text)
        
        return {
            'records': records,
            'total_records': len(records)
        }
            
    except Exception as e:
        print(f"Error processing image: {str(e)}")
        return {'error': str(e)}

# ==================== ROUTES ====================

@app.route('/')
def index():
    """Main page"""
    students = get_all_students()
    books = get_all_books()
    issued = get_all_issued()
    total_students, total_books, total_issued = get_statistics()
    
    # Convert sqlite3.Row objects to lists for template compatibility
    students_list = [[s['id'], s['adm_no'], s['name'], s['stream']] for s in students]
    books_list = [[b['id'], b['bookname'], b['bookcode']] for b in books]
    issued_list = [[i['id'], i['student_name'], i['adm_no'], i['bookname'], i['date_issued'], i['stream']] for i in issued]
    
    return render_template('index.html', 
                         students=students_list, 
                         books=books_list, 
                         issued=issued_list,
                         total_students=total_students,
                         total_books=total_books,
                         total_issued=total_issued)

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle image upload and processing for multiple records"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file and allowed_file(file.filename):
        # Generate unique filename
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(filepath)
        
        try:
            # Process image with Vision API
            result = process_image(filepath)
            
            if 'error' in result:
                return jsonify({'error': result['error']}), 500
            
            # Save all records to database
            saved_count = 0
            failed_count = 0
            
            if result.get('records'):
                for record in result['records']:
                    try:
                        # Add student
                        add_student(record['adm_no'], record['name'], record['stream'])
                        
                        # Add book
                        book_name = f"Book {record['bookcode']}"
                        add_book(book_name, record['bookcode'])
                        
                        # Issue book
                        issue_book(
                            record['name'],
                            record['adm_no'],
                            book_name,
                            record['stream'],
                            datetime.now().strftime('%Y-%m-%d')
                        )
                        
                        saved_count += 1
                        print(f"✓ Saved: {record}")
                    except Exception as e:
                        print(f"✗ Failed to save record {record}: {e}")
                        failed_count += 1
                
                return jsonify({
                    'success': True,
                    'message': f'Successfully processed {saved_count} records. Failed: {failed_count}',
                    'saved_count': saved_count
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'No valid records found in the image',
                    'full_text': result.get('full_text', '')[:500] + '...'
                }), 400
                
        except Exception as e:
            return jsonify({'error': f'Error processing file: {str(e)}'}), 500
        finally:
            # Clean up uploaded file
            try:
                os.remove(filepath)
            except:
                pass
    
    return jsonify({'error': 'Invalid file type'}), 400

# ==================== TEMPLATE ====================

# Create template directory if it doesn't exist
os.makedirs('templates', exist_ok=True)

# Write the HTML template with UTF-8 encoding
html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rabai Secondary School - Library Management System</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }
        
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        
        .header h2 {
            font-size: 1.5em;
            opacity: 0.9;
            font-weight: 300;
        }
        
        .stats-container {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
            padding: 40px;
            background: #f8f9fa;
        }
        
        .stat-card {
            background: white;
            padding: 30px;
            border-radius: 15px;
            text-align: center;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            transition: transform 0.3s;
        }
        
        .stat-card:hover {
            transform: translateY(-5px);
        }
        
        .stat-card h3 {
            color: #667eea;
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        
        .stat-card p {
            color: #666;
            font-size: 1.2em;
        }
        
        .upload-section {
            padding: 40px;
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
        }
        
        .upload-container {
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            text-align: center;
        }
        
        .upload-container h3 {
            color: #333;
            margin-bottom: 20px;
            font-size: 1.8em;
        }
        
        .file-input-wrapper {
            position: relative;
            margin: 20px 0;
        }
        
        .file-input {
            position: absolute;
            width: 0.1px;
            height: 0.1px;
            opacity: 0;
            overflow: hidden;
            z-index: -1;
        }
        
        .file-label {
            display: inline-block;
            padding: 15px 40px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 50px;
            cursor: pointer;
            font-size: 1.2em;
            transition: transform 0.3s;
            box-shadow: 0 10px 20px rgba(102, 126, 234, 0.4);
        }
        
        .file-label:hover {
            transform: scale(1.05);
        }
        
        .process-btn {
            padding: 15px 50px;
            background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            color: white;
            border: none;
            border-radius: 50px;
            font-size: 1.2em;
            cursor: pointer;
            transition: transform 0.3s;
            box-shadow: 0 10px 20px rgba(245, 87, 108, 0.4);
            margin-top: 20px;
        }
        
        .process-btn:hover {
            transform: scale(1.05);
        }
        
        .process-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        #fileName {
            margin: 15px 0;
            color: #666;
            font-style: italic;
        }
        
        .result-section {
            padding: 0 40px 40px 40px;
        }
        
        .result-container {
            background: white;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        
        .result-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .result-header h3 {
            font-size: 1.5em;
        }
        
        .refresh-btn {
            padding: 10px 20px;
            background: rgba(255,255,255,0.2);
            color: white;
            border: 2px solid white;
            border-radius: 25px;
            cursor: pointer;
            transition: background 0.3s;
        }
        
        .refresh-btn:hover {
            background: rgba(255,255,255,0.3);
        }
        
        .tabs {
            display: flex;
            background: #f8f9fa;
            border-bottom: 1px solid #dee2e6;
        }
        
        .tab {
            padding: 15px 30px;
            cursor: pointer;
            border: none;
            background: none;
            font-size: 1.1em;
            color: #666;
            transition: all 0.3s;
            position: relative;
        }
        
        .tab.active {
            color: #667eea;
            font-weight: 600;
        }
        
        .tab.active::after {
            content: '';
            position: absolute;
            bottom: -1px;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }
        
        .tab-content {
            display: none;
            padding: 30px;
            overflow-x: auto;
        }
        
        .tab-content.active {
            display: block;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }
        
        th {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px;
            text-align: left;
            font-weight: 500;
        }
        
        td {
            padding: 12px 15px;
            border-bottom: 1px solid #dee2e6;
            color: #666;
        }
        
        tr:hover {
            background: #f8f9fa;
        }
        
        .status-message {
            padding: 15px 30px;
            margin: 20px 40px;
            border-radius: 10px;
            display: none;
        }
        
        .status-success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        
        .status-error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid rgba(255,255,255,.3);
            border-radius: 50%;
            border-top-color: white;
            animation: spin 1s ease-in-out infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📚 RABAI SECONDARY SCHOOL</h1>
            <h2>Library Management System</h2>
            <p style="margin-top: 10px; opacity: 0.9;">Teacher: Mr. Khalid Ahmad</p>
        </div>
        
        <div class="stats-container">
            <div class="stat-card">
                <h3>{{ total_students }}</h3>
                <p>Total Students</p>
            </div>
            <div class="stat-card">
                <h3>{{ total_books }}</h3>
                <p>Total Books</p>
            </div>
            <div class="stat-card">
                <h3>{{ total_issued }}</h3>
                <p>Books Issued</p>
            </div>
        </div>
        
        <div class="upload-section">
            <div class="upload-container">
                <h3>📸 Upload Student Form</h3>
                <p style="color: #666; margin-bottom: 20px;">Upload an image of the student book issue form to automatically process ALL records and update the database</p>
                
                <div class="file-input-wrapper">
                    <input type="file" id="fileInput" class="file-input" accept="image/*">
                    <label for="fileInput" class="file-label">Choose Image</label>
                </div>
                
                <div id="fileName"></div>
                
                <button id="processBtn" class="process-btn" onclick="processImage()" disabled>
                    <span id="btnText">Process with Vision API</span>
                    <span id="loadingSpinner" class="loading" style="display: none;"></span>
                </button>
            </div>
        </div>
        
        <div id="statusMessage" class="status-message"></div>
        
        <div class="result-section">
            <div class="result-container">
                <div class="result-header">
                    <h3>📊 Library Records</h3>
                    <button class="refresh-btn" onclick="refreshData()">🔄 Refresh</button>
                </div>
                
                <div class="tabs">
                    <button class="tab active" onclick="showTab('students')">Students</button>
                    <button class="tab" onclick="showTab('books')">Books</button>
                    <button class="tab" onclick="showTab('issued')">Issued Books</button>
                </div>
                
                <div id="studentsTab" class="tab-content active">
                    <h3 style="color: #333; margin-bottom: 20px;">Student Records</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>ADM No</th>
                                <th>Name</th>
                                <th>Stream</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for student in students %}
                            <tr>
                                <td>{{ student[1] }}</td>
                                <td>{{ student[2] }}</td>
                                <td>{{ student[3] }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                
                <div id="booksTab" class="tab-content">
                    <h3 style="color: #333; margin-bottom: 20px;">Book Records</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Book Name</th>
                                <th>Book Code</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for book in books %}
                            <tr>
                                <td>{{ book[1] }}</td>
                                <td>{{ book[2] }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                
                <div id="issuedTab" class="tab-content">
                    <h3 style="color: #333; margin-bottom: 20px;">Issued Books</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Student Name</th>
                                <th>ADM No</th>
                                <th>Book Name</th>
                                <th>Stream</th>
                                <th>Date Issued</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for issue in issued %}
                            <tr>
                                <td>{{ issue[1] }}</td>
                                <td>{{ issue[2] }}</td>
                                <td>{{ issue[3] }}</td>
                                <td>{{ issue[5] }}</td>
                                <td>{{ issue[4] }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        const fileInput = document.getElementById('fileInput');
        const fileName = document.getElementById('fileName');
        const processBtn = document.getElementById('processBtn');
        const btnText = document.getElementById('btnText');
        const loadingSpinner = document.getElementById('loadingSpinner');
        const statusMessage = document.getElementById('statusMessage');
        
        fileInput.addEventListener('change', function(e) {
            if (this.files && this.files[0]) {
                fileName.textContent = `Selected: ${this.files[0].name}`;
                processBtn.disabled = false;
            } else {
                fileName.textContent = '';
                processBtn.disabled = true;
            }
        });
        
        function showTab(tabName) {
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.classList.remove('active');
            });
            
            document.querySelectorAll('.tab').forEach(btn => {
                btn.classList.remove('active');
            });
            
            document.getElementById(tabName + 'Tab').classList.add('active');
            event.target.classList.add('active');
        }
        
        function showMessage(message, isSuccess) {
            statusMessage.textContent = message;
            statusMessage.className = 'status-message ' + (isSuccess ? 'status-success' : 'status-error');
            statusMessage.style.display = 'block';
            
            setTimeout(() => {
                statusMessage.style.display = 'none';
            }, 5000);
        }
        
        function processImage() {
            const file = fileInput.files[0];
            if (!file) {
                showMessage('Please select an image first', false);
                return;
            }
            
            const formData = new FormData();
            formData.append('file', file);
            
            processBtn.disabled = true;
            btnText.style.display = 'none';
            loadingSpinner.style.display = 'inline-block';
            
            fetch('/upload', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showMessage(`✓ ${data.message}`, true);
                    setTimeout(() => {
                        location.reload();
                    }, 2000);
                } else {
                    showMessage('✗ ' + (data.message || 'Failed to process image'), false);
                }
            })
            .catch(error => {
                console.error('Error:', error);
                showMessage('✗ Error processing image. Please try again.', false);
            })
            .finally(() => {
                processBtn.disabled = false;
                btnText.style.display = 'inline';
                loadingSpinner.style.display = 'none';
            });
        }
        
        function refreshData() {
            location.reload();
        }
    </script>
</body>
</html>
'''

# Write the HTML template with UTF-8 encoding
with open('templates/index.html', 'w', encoding='utf-8') as f:
    f.write(html_content)
print("✓ HTML template created successfully")

# Initialize database when app starts
with app.app_context():
    init_database()

if __name__ == '__main__':
    print("=" * 50)
    print("Rabai Secondary School - Library Management System")
    print("=" * 50)
    print("\n✓ Starting server...")
    print("✓ Multi-record processing enabled")
    print("✓ Using Google Vision API with provided API key")
    print("\n🌐 Access the application at: http://localhost:5000")
    print("\nPress Ctrl+C to stop the server")
    print("=" * 50)
    
    app.run(debug=True, host='0.0.0.0', port=5000)
