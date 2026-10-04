#!/usr/bin/env python3
"""
iPadPDF - Ultra-Lightweight PDF Reader & Library for iPad 1st Gen (iOS 5)
Server-side rasterization to lightweight JPEGs for 256MB RAM devices.
"""

import os
import sys
import time
import socket
import sqlite3
import subprocess
import shutil
from pathlib import Path
from flask import Flask, request, redirect, url_for, send_file, render_template_string, abort
import pypdfium2 as pdfium

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / 'uploads'
CACHE_DIR = BASE_DIR / 'cache'
DATA_DIR = BASE_DIR / 'data'
DB_PATH = DATA_DIR / 'library.db'

for d in (UPLOADS_DIR, CACHE_DIR, DATA_DIR):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 250 * 1024 * 1024  # 250 MB max PDF upload

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    with conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                filename TEXT NOT NULL,
                page_count INTEGER NOT NULL DEFAULT 1,
                filesize INTEGER NOT NULL DEFAULT 0,
                current_page INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    conn.close()

init_db()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

def count_pages(pdf_path):
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        count = len(doc)
        doc.close()
        return max(1, count)
    except Exception as e:
        app.logger.error(f'Error counting pages: {e}')
        return 1

def render_page_to_jpeg(pdf_path, page_num, output_jpg, dpi=140, quality=80):
    output_jpg = Path(output_jpg)
    if output_jpg.exists():
        return True

    # Method 1: Try native pdftoppm (fastest C++ implementation)
    if shutil.which('pdftoppm'):
        prefix = output_jpg.with_suffix('')
        cmd = [
            'pdftoppm',
            '-jpeg',
            '-r', str(dpi),
            '-f', str(page_num),
            '-l', str(page_num),
            '-singlefile',
            str(pdf_path),
            str(prefix)
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0 and output_jpg.exists():
            return True

    # Method 2: Fallback to portable pypdfium2
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        if page_num < 1 or page_num > len(doc):
            doc.close()
            return False
        page = doc[page_num - 1]
        scale = dpi / 72.0
        pil_img = page.render(scale=scale).to_pil()
        pil_img.save(str(output_jpg), 'JPEG', quality=quality)
        doc.close()
        return True
    except Exception as e:
        app.logger.error(f'Failed rendering page {page_num}: {e}')
        return False

# Base CSS designed for iOS 5 Mobile Safari and ultra-minimalist speed
COMMON_CSS = '''
body {
    background-color: #f7f7f7;
    color: #222222;
    font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
    margin: 0;
    padding: 12px;
    font-size: 16px;
    line-height: 1.4;
}
a {
    color: #0066cc;
    text-decoration: none;
}
.header {
    background: #ffffff;
    border: 1px solid #d0d0d0;
    padding: 14px 16px;
    margin-bottom: 14px;
    border-radius: 4px;
}
.header h1 {
    margin: 0 0 6px 0;
    font-size: 22px;
}
.ip-banner {
    background: #eef7ff;
    border: 1px solid #b8daff;
    padding: 10px 14px;
    margin-bottom: 14px;
    border-radius: 4px;
    font-size: 15px;
}
.ip-banner strong {
    color: #004085;
}
.card {
    background: #ffffff;
    border: 1px solid #d0d0d0;
    padding: 14px;
    margin-bottom: 14px;
    border-radius: 4px;
}
.btn {
    display: inline-block;
    background: #007aff;
    color: #ffffff !important;
    padding: 10px 16px;
    border: 1px solid #0056b3;
    border-radius: 4px;
    font-size: 16px;
    font-weight: bold;
    cursor: pointer;
    text-align: center;
    -webkit-appearance: none;
}
.btn-secondary {
    background: #6c757d;
    border-color: #545b62;
}
.btn-danger {
    background: #dc3545;
    border-color: #bd2130;
    padding: 6px 10px;
    font-size: 13px;
}
.btn-nav {
    padding: 14px 20px;
    font-size: 18px;
    min-width: 90px;
}
.book-table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 10px;
}
.book-table th, .book-table td {
    padding: 10px 8px;
    border-bottom: 1px solid #e0e0e0;
    text-align: left;
    vertical-align: middle;
}
.book-table th {
    background: #f0f0f0;
    font-size: 14px;
}
.nav-bar {
    background: #ffffff;
    border: 1px solid #d0d0d0;
    padding: 10px 14px;
    margin-bottom: 10px;
    border-radius: 4px;
    text-align: center;
}
.page-container {
    text-align: center;
    background: #e8e8e8;
    padding: 8px 0;
    border: 1px solid #cccccc;
    margin: 10px 0;
    border-radius: 4px;
}
.page-img {
    max-width: 100%;
    height: auto;
    background: #ffffff;
    box-shadow: 0 1px 4px rgba(0,0,0,0.25);
    display: block;
    margin: 0 auto;
}
input[type="number"], input[type="text"], input[type="file"] {
    font-size: 16px;
    padding: 8px;
    border: 1px solid #cccccc;
    border-radius: 4px;
    -webkit-appearance: none;
}
'''

INDEX_HTML = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=2.0, user-scalable=yes">
    <title>iPad PDF Library</title>
    <style>''' + COMMON_CSS + '''</style>
</head>
<body>

    <div class="header">
        <h1>iPad PDF Library</h1>
        <div>Fast, ultra-lightweight PDF reader for Gen 1 iPad & any device.</div>
    </div>

    <div class="ip-banner">
        <strong>iPad Access Address:</strong> Open Safari on your iPad and go to:<br>
        <span style="font-size: 18px; font-weight: bold; color: #0056b3;">http://{{ local_ip }}:{{ port }}</span>
    </div>

    <div class="card">
        <h2 style="margin-top: 0; font-size: 18px;">Upload New PDF</h2>
        <form action="/upload" method="post" enctype="multipart/form-data">
            <input type="file" name="pdf_file" accept=".pdf,application/pdf" required style="margin-bottom: 10px; display: block;">
            <input type="submit" value="Upload PDF" class="btn">
        </form>
    </div>

    <div class="card">
        <h2 style="margin-top: 0; font-size: 18px;">Library ({{ books|length }} documents)</h2>
        {% if books %}
        <table class="book-table">
            <thead>
                <tr>
                    <th>Title</th>
                    <th>Pages</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                {% for b in books %}
                <tr>
                    <td>
                        <strong><a href="/read/{{ b.id }}?page={{ b.current_page }}">{{ b.title }}</a></strong>
                        <div style="font-size: 12px; color: #666;">Last read: Page {{ b.current_page }} of {{ b.page_count }}</div>
                    </td>
                    <td>{{ b.page_count }}</td>
                    <td>
                        <a href="/read/{{ b.id }}?page={{ b.current_page }}" class="btn" style="padding: 6px 12px; font-size: 14px;">Read</a>
                        <form action="/delete/{{ b.id }}" method="post" style="display: inline;" onsubmit="return confirm('Delete this PDF?');">
                            <input type="submit" value="Delete" class="btn btn-danger">
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <p style="color: #666;">No PDFs uploaded yet. Upload one above from your phone or laptop!</p>
        {% endif %}
    </div>

</body>
</html>'''

READER_HTML = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=3.0, user-scalable=yes">
    <title>{{ book.title }} - Page {{ current_page }}/{{ book.page_count }}</title>
    <style>''' + COMMON_CSS + '''
    .zoom-100 { max-width: 100%; }
    .zoom-125 { width: 125%; max-width: none; }
    .zoom-150 { width: 150%; max-width: none; }
    </style>
</head>
<body>

    <div class="nav-bar">
        <div style="float: left;">
            <a href="/" class="btn btn-secondary" style="padding: 8px 12px; font-size: 14px;">&larr; Library</a>
        </div>
        <div style="float: right;">
            <span style="font-size: 13px; color: #555;">Zoom:</span>
            <a href="/read/{{ book.id }}?page={{ current_page }}&zoom=100" style="padding: 4px 6px;">1x</a>
            <a href="/read/{{ book.id }}?page={{ current_page }}&zoom=125" style="padding: 4px 6px;">1.2x</a>
            <a href="/read/{{ book.id }}?page={{ current_page }}&zoom=150" style="padding: 4px 6px;">1.5x</a>
        </div>
        <div style="clear: both; margin-bottom: 8px;"></div>
        
        <strong style="font-size: 17px;">{{ book.title }}</strong>
        <div style="margin: 8px 0;">
            {% if prev_page %}
            <a href="/read/{{ book.id }}?page={{ prev_page }}&zoom={{ zoom }}" class="btn btn-nav">&larr; Prev</a>
            {% else %}
            <span class="btn btn-nav btn-secondary" style="opacity: 0.5;">&larr; Prev</span>
            {% endif %}

            <span style="font-size: 18px; font-weight: bold; margin: 0 10px;">{{ current_page }} / {{ book.page_count }}</span>

            {% if next_page %}
            <a href="/read/{{ book.id }}?page={{ next_page }}&zoom={{ zoom }}" class="btn btn-nav">Next &rarr;</a>
            {% else %}
            <span class="btn btn-nav btn-secondary" style="opacity: 0.5;">Next &rarr;</span>
            {% endif %}
        </div>

        <form action="/read/{{ book.id }}" method="get" style="margin-top: 6px;">
            <input type="hidden" name="zoom" value="{{ zoom }}">
            Jump to page: 
            <input type="number" name="page" min="1" max="{{ book.page_count }}" value="{{ current_page }}" style="width: 60px; text-align: center;">
            <input type="submit" value="Go" class="btn" style="padding: 6px 12px; font-size: 14px;">
        </form>
    </div>

    <!-- The Page Image Container -->
    <div class="page-container">
        {% if next_page %}
        <a href="/read/{{ book.id }}?page={{ next_page }}&zoom={{ zoom }}" title="Tap to flip to next page">
            <img src="/page/{{ book.id }}/{{ current_page }}" class="page-img zoom-{{ zoom }}" alt="Page {{ current_page }}">
        </a>
        {% else %}
        <img src="/page/{{ book.id }}/{{ current_page }}" class="page-img zoom-{{ zoom }}" alt="Page {{ current_page }}">
        {% endif %}
    </div>

    <!-- Bottom Navigation Bar (No need to scroll back up on iPad!) -->
    <div class="nav-bar">
        {% if prev_page %}
        <a href="/read/{{ book.id }}?page={{ prev_page }}&zoom={{ zoom }}" class="btn btn-nav">&larr; Prev</a>
        {% endif %}
        
        <span style="font-size: 18px; font-weight: bold; margin: 0 10px;">Page {{ current_page }} of {{ book.page_count }}</span>
        
        {% if next_page %}
        <a href="/read/{{ book.id }}?page={{ next_page }}&zoom={{ zoom }}" class="btn btn-nav">Next &rarr;</a>
        {% endif %}
        <div style="margin-top: 10px;">
            <a href="/" class="btn btn-secondary" style="padding: 8px 14px;">&larr; Back to Library</a>
        </div>
    </div>

    <!-- Optional Keyboard Shortcuts for iPad external keyboard / PC -->
    <script>
    document.addEventListener('keydown', function(e) {
        if (e.keyCode === 37) { // Left arrow
            {% if prev_page %} window.location.href = "/read/{{ book.id }}?page={{ prev_page }}&zoom={{ zoom }}"; {% endif %}
        } else if (e.keyCode === 39 || e.keyCode === 32) { // Right arrow or space
            {% if next_page %} window.location.href = "/read/{{ book.id }}?page={{ next_page }}&zoom={{ zoom }}"; {% endif %}
        }
    });
    </script>

</body>
</html>'''

@app.route('/')
def index():
    conn = get_db()
    books = conn.execute('SELECT * FROM books ORDER BY created_at DESC').fetchall()
    conn.close()
    local_ip = get_local_ip()
    port = os.environ.get('PORT', 5000)
    return render_template_string(INDEX_HTML, books=books, local_ip=local_ip, port=port)

@app.route('/upload', methods=['POST'])
def upload():
    if 'pdf_file' not in request.files:
        return redirect(url_for('index'))
    file = request.files['pdf_file']
    if not file or not file.filename:
        return redirect(url_for('index'))
    
    orig_name = file.filename
    clean_title = Path(orig_name).stem.replace('_', ' ').replace('-', ' ').strip()
    timestamp = int(time.time())
    safe_filename = f"{timestamp}_{orig_name.replace(' ', '_')}"
    save_path = UPLOADS_DIR / safe_filename

    file.save(str(save_path))
    filesize = save_path.stat().st_size
    page_count = count_pages(save_path)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO books (title, filename, page_count, filesize, current_page)
        VALUES (?, ?, ?, ?, 1)
    ''', (clean_title, safe_filename, page_count, filesize))
    book_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Pre-render page 1 so it's ready instantly
    render_page_to_jpeg(save_path, 1, CACHE_DIR / f"{book_id}_p1.jpg")

    return redirect(url_for('read_book', book_id=book_id, page=1))

@app.route('/read/<int:book_id>')
def read_book(book_id):
    conn = get_db()
    book = conn.execute('SELECT * FROM books WHERE id = ?', (book_id,)).fetchone()
    if not book:
        conn.close()
        abort(404)
    
    try:
        current_page = int(request.args.get('page', book['current_page']))
    except ValueError:
        current_page = 1
    
    current_page = max(1, min(current_page, book['page_count']))
    zoom = request.args.get('zoom', '100')
    if zoom not in ('100', '125', '150'):
        zoom = '100'

    # Update progress in db
    with conn:
        conn.execute('UPDATE books SET current_page = ? WHERE id = ?', (current_page, book_id))
    conn.close()

    prev_page = current_page - 1 if current_page > 1 else None
    next_page = current_page + 1 if current_page < book['page_count'] else None

    # Pre-render next page asynchronously or on the fly so it's warm in cache
    pdf_path = UPLOADS_DIR / book['filename']
    if next_page:
        render_page_to_jpeg(pdf_path, next_page, CACHE_DIR / f"{book_id}_p{next_page}.jpg")

    return render_template_string(
        READER_HTML,
        book=book,
        current_page=current_page,
        prev_page=prev_page,
        next_page=next_page,
        zoom=zoom
    )

@app.route('/page/<int:book_id>/<int:page_num>')
def get_page(book_id, page_num):
    conn = get_db()
    book = conn.execute('SELECT * FROM books WHERE id = ?', (book_id,)).fetchone()
    conn.close()
    if not book:
        abort(404)

    cache_file = CACHE_DIR / f"{book_id}_p{page_num}.jpg"
    if not cache_file.exists():
        pdf_path = UPLOADS_DIR / book['filename']
        if not pdf_path.exists():
            abort(404)
        success = render_page_to_jpeg(pdf_path, page_num, cache_file)
        if not success:
            abort(500)

    # Return with cache headers for instant iPad back/forward navigation
    response = send_file(cache_file, mimetype='image/jpeg')
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response

@app.route('/delete/<int:book_id>', methods=['POST'])
def delete_book(book_id):
    conn = get_db()
    book = conn.execute('SELECT * FROM books WHERE id = ?', (book_id,)).fetchone()
    if book:
        pdf_path = UPLOADS_DIR / book['filename']
        if pdf_path.exists():
            try:
                pdf_path.unlink()
            except Exception:
                pass
        
        # Clean up cached images for this book
        for img in CACHE_DIR.glob(f"{book_id}_p*.jpg"):
            try:
                img.unlink()
            except Exception:
                pass

        with conn:
            conn.execute('DELETE FROM books WHERE id = ?', (book_id,))
    conn.close()
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"iPadPDF Server running on port {port}")
    print(f"Access on iPad at: http://{get_local_ip()}:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
