#!/usr/bin/env python3
"""
iPadPDF - Ultra-Lightweight PDF Reader & Library for iPad 1st Gen (iOS 5)
Server-side rasterization to lightweight JPEGs for 256MB RAM devices.
Supports instant touch swipe navigation (swipe left/right) with zero clutter.
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

def render_page_to_jpeg(pdf_path, page_num, output_jpg, dpi=130, quality=78):
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

INDEX_HTML = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=2.0, user-scalable=yes">
    <title>iPad PDF Library</title>
    <style>
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
        padding: 10px 18px;
        border: 1px solid #0056b3;
        border-radius: 4px;
        font-size: 16px;
        font-weight: bold;
        cursor: pointer;
        text-align: center;
        -webkit-appearance: none;
    }
    .btn-danger {
        background: #dc3545;
        border-color: #bd2130;
        padding: 6px 12px;
        font-size: 13px;
    }
    .book-table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 10px;
    }
    .book-table th, .book-table td {
        padding: 12px 8px;
        border-bottom: 1px solid #e0e0e0;
        text-align: left;
        vertical-align: middle;
    }
    .book-table th {
        background: #f0f0f0;
        font-size: 14px;
    }
    input[type="file"] {
        font-size: 16px;
        padding: 8px;
        margin-bottom: 12px;
        display: block;
    }
    </style>
</head>
<body>

    <div class="header">
        <h1>iPad PDF Library</h1>
        <div>Minimalist PDF Reader for Gen 1 iPad. Swipe left/right to turn pages.</div>
    </div>

    <div class="ip-banner">
        <strong>Open Safari on your iPad and go to:</strong><br>
        <span style="font-size: 20px; font-weight: bold; color: #0056b3;">http://{{ local_ip }}:{{ port }}</span>
    </div>

    <div class="card">
        <h2 style="margin-top: 0; font-size: 18px;">Upload New PDF</h2>
        <form action="/upload" method="post" enctype="multipart/form-data">
            <input type="file" name="pdf_file" accept=".pdf,application/pdf" required>
            <input type="submit" value="Upload PDF" class="btn">
        </form>
    </div>

    <div class="card">
        <h2 style="margin-top: 0; font-size: 18px;">My Documents ({{ books|length }})</h2>
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
                        <strong><a href="/read/{{ b.id }}">{{ b.title }}</a></strong>
                        <div style="font-size: 13px; color: #666;">Reading: Page {{ b.current_page }} of {{ b.page_count }}</div>
                    </td>
                    <td>{{ b.page_count }}</td>
                    <td>
                        <a href="/read/{{ b.id }}" class="btn" style="padding: 8px 16px;">Open</a>
                        <form action="/delete/{{ b.id }}" method="post" style="display: inline; margin-left: 6px;" onsubmit="return confirm('Delete this PDF?');">
                            <input type="submit" value="Delete" class="btn btn-danger">
                        </form>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <p style="color: #666;">No PDFs uploaded yet. Upload one above from any phone or laptop!</p>
        {% endif %}
    </div>

</body>
</html>'''

SWIPE_READER_HTML = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=3.0, user-scalable=yes">
    <title>{{ book.title }}</title>
    <style>
    * {
        -webkit-box-sizing: border-box;
        box-sizing: border-box;
    }
    body, html {
        margin: 0;
        padding: 0;
        width: 100%;
        min-height: 100%;
        background-color: #1e1e1e;
        color: #ffffff;
        font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
        overflow-x: hidden;
    }
    #topBar {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        height: 38px;
        background: rgba(0, 0, 0, 0.85);
        border-bottom: 1px solid #333333;
        padding: 6px 14px;
        z-index: 999;
        font-size: 15px;
    }
    .nav-link {
        color: #5ac8fa !important;
        text-decoration: none;
        font-weight: bold;
        float: left;
        line-height: 26px;
        font-size: 15px;
    }
    .page-badge {
        float: right;
        color: #cccccc;
        font-size: 14px;
        line-height: 26px;
    }
    .book-title {
        text-align: center;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 55%;
        margin: 0 auto;
        color: #ffffff;
        font-size: 14px;
        line-height: 26px;
    }
    #pageArea {
        padding-top: 38px;
        text-align: center;
        width: 100%;
        min-height: 100%;
    }
    #pageImg {
        display: block;
        margin: 0 auto;
        max-width: 100%;
        height: auto;
        background: #ffffff;
        box-shadow: 0 4px 14px rgba(0,0,0,0.5);
    }
    #tapHint {
        font-size: 12px;
        color: #777777;
        text-align: center;
        padding: 8px 0;
    }
    </style>
</head>
<body>

    <!-- Minimal top header -->
    <div id="topBar">
        <a href="/" class="nav-link">&larr; Library</a>
        <a href="javascript:void(0)" onclick="startOfflineCache()" class="nav-link" id="cacheBtn" style="margin-left: 14px; font-weight: normal; color: #ffcc00 !important;">Save Offline</a>
        <span class="page-badge" id="pageDisplay">{{ current_page }} / {{ book.page_count }}</span>
        <div class="book-title">{{ book.title }}</div>
    </div>
    <div id="cacheStatus" style="display: none; background: #2a2a2a; border-bottom: 1px solid #444; color: #ffcc00; text-align: center; font-size: 13px; padding: 6px 10px; position: fixed; top: 38px; left: 0; right: 0; z-index: 998;"></div>

    <!-- The PDF Page Container -->
    <div id="pageArea">
        <img id="pageImg" src="/page/{{ book.id }}/{{ current_page }}" alt="PDF Page">
        <div id="tapHint">Swipe left for Next &bull; Swipe right for Prev</div>
    </div>

    <!-- Ultra-lightweight ES5 Touch/Swipe & Offline handler for iOS 5 Safari -->
    <script>
    var bookId = {{ book.id }};
    var currentPage = {{ current_page }};
    var totalPages = {{ book.page_count }};

    var startX = 0;
    var startY = 0;
    var startTime = 0;

    var isCaching = false;
    function startOfflineCache() {
        if (isCaching) return;
        isCaching = true;
        var btn = document.getElementById('cacheBtn');
        var status = document.getElementById('cacheStatus');
        status.style.display = 'block';
        status.style.color = '#ffcc00';
        status.innerText = 'Starting offline save...';
        btn.style.opacity = '0.5';

        var currentIdx = 1;
        function cacheNext() {
            if (currentIdx > totalPages) {
                status.style.color = '#34c759';
                status.innerText = 'Saved! You can now turn off Wi-Fi and swipe through the entire book.';
                btn.innerText = 'Offline Ready';
                btn.style.color = '#34c759 !important';
                btn.style.opacity = '1.0';
                setTimeout(function() {
                    status.style.display = 'none';
                }, 5000);
                isCaching = false;
                return;
            }

            status.innerText = 'Saving for offline: Page ' + currentIdx + ' of ' + totalPages + '...';
            var temp = new Image();
            temp.onload = temp.onerror = function() {
                temp.onload = null;
                temp.onerror = null;
                temp = null;
                currentIdx++;
                setTimeout(cacheNext, 50);
            };
            temp.src = '/page/' + bookId + '/' + currentIdx;
        }

        cacheNext();
    }

    function preload(p) {
        if (p >= 1 && p <= totalPages) {
            var img = new Image();
            img.src = '/page/' + bookId + '/' + p;
        }
    }

    function goToPage(p) {
        if (p < 1 || p > totalPages) {
            return;
        }
        currentPage = p;

        // Instant image swap without reloading webpage
        var img = document.getElementById('pageImg');
        if (img) {
            img.src = '/page/' + bookId + '/' + p;
        }

        // Update page indicator badge
        var display = document.getElementById('pageDisplay');
        if (display) {
            display.innerText = p + ' / ' + totalPages;
        }

        // Scroll back to top smoothly for next page
        window.scrollTo(0, 0);

        // Preload next and previous pages for instant response
        preload(p + 1);
        preload(p - 1);

        // Update server reading progress silently
        var ping = new Image();
        ping.src = '/bookmark/' + bookId + '/' + p;

        // Update browser URL silently if history API supported
        if (window.history && window.history.replaceState) {
            window.history.replaceState(null, '', '/read/' + bookId + '?page=' + p);
        }
    }

    // Touch event listeners for iOS 5 Safari
    window.addEventListener('touchstart', function(e) {
        if (e.touches && e.touches.length === 1) {
            startX = e.touches[0].clientX;
            startY = e.touches[0].clientY;
            startTime = new Date().getTime();
        }
    }, false);

    window.addEventListener('touchend', function(e) {
        if (!e.changedTouches || e.changedTouches.length !== 1) return;

        var endX = e.changedTouches[0].clientX;
        var endY = e.changedTouches[0].clientY;
        var diffX = endX - startX;
        var diffY = endY - startY;
        var absX = Math.abs(diffX);
        var absY = Math.abs(diffY);
        var duration = new Date().getTime() - startTime;

        // 1. Horizontal swipe gesture
        if (absX >= 35 && absX > absY && duration < 900) {
            if (diffX < 0) {
                // Swipe Left -> Next Page
                goToPage(currentPage + 1);
            } else {
                // Swipe Right -> Previous Page
                goToPage(currentPage - 1);
            }
            return;
        }

        // 2. Light tap on left/right edges (tap right 30% = next, tap left 30% = prev)
        if (absX < 15 && absY < 15 && duration < 350) {
            var width = window.innerWidth || document.documentElement.clientWidth || 1024;
            if (endX > width * 0.70) {
                goToPage(currentPage + 1);
            } else if (endX < width * 0.30) {
                goToPage(currentPage - 1);
            }
        }
    }, false);

    // Keyboard support for external iPad keyboard or PC testing
    document.addEventListener('keydown', function(e) {
        if (e.keyCode === 37) { // Left arrow
            goToPage(currentPage - 1);
        } else if (e.keyCode === 39 || e.keyCode === 32) { // Right arrow or space
            goToPage(currentPage + 1);
        }
    }, false);

    // Initial preloads
    preload(currentPage + 1);
    preload(currentPage + 2);
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

    # Pre-render page 1 and page 2 so they are ready instantly
    render_page_to_jpeg(save_path, 1, CACHE_DIR / f"{book_id}_p1.jpg")
    if page_count > 1:
        render_page_to_jpeg(save_path, 2, CACHE_DIR / f"{book_id}_p2.jpg")

    return redirect(url_for('read_book', book_id=book_id))

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

    # Update progress in db
    with conn:
        conn.execute('UPDATE books SET current_page = ? WHERE id = ?', (current_page, book_id))
    conn.close()

    # Pre-render current, next, and previous pages
    pdf_path = UPLOADS_DIR / book['filename']
    render_page_to_jpeg(pdf_path, current_page, CACHE_DIR / f"{book_id}_p{current_page}.jpg")
    if current_page < book['page_count']:
        render_page_to_jpeg(pdf_path, current_page + 1, CACHE_DIR / f"{book_id}_p{current_page + 1}.jpg")
    if current_page > 1:
        render_page_to_jpeg(pdf_path, current_page - 1, CACHE_DIR / f"{book_id}_p{current_page - 1}.jpg")

    return render_template_string(
        SWIPE_READER_HTML,
        book=book,
        current_page=current_page
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

    # Return with permanent cache headers for offline iPad reading
    response = send_file(cache_file, mimetype='image/jpeg')
    response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response

@app.route('/bookmark/<int:book_id>/<int:page_num>')
def bookmark(book_id, page_num):
    conn = get_db()
    with conn:
        conn.execute('UPDATE books SET current_page = ? WHERE id = ?', (page_num, book_id))
    conn.close()
    return ('', 204)

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
