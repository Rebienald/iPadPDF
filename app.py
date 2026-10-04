#!/usr/bin/env python3
"""
iPadPDF - Ultra-Lightweight Pure Monochrome (Grayscale) Single Page App
Optimized for iPad 1st Gen (iOS 5, 256MB RAM):
- Pure black, white, and gray minimalist interface (zero colors)
- Pages rendered in 8-bit grayscale JPEG (saves ~40% file size and RAM)
- Single Page Application (SPA): no page reloads, no broken offline refreshes
- Swipe left/right & edge-tap navigation
- LocalStorage state persistence
"""

import os
import sys
import time
import socket
import sqlite3
import subprocess
import shutil
import json
import gzip
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

@app.after_request
def compress_response(response):
    accept_encoding = request.headers.get('Accept-Encoding', '')
    if 'gzip' not in accept_encoding.lower():
        return response
    if response.status_code < 200 or response.status_code >= 300:
        return response
    if 'Content-Encoding' in response.headers:
        return response
    content_type = response.headers.get('Content-Type', '')
    if any(t in content_type for t in ['text/html', 'application/json', 'text/css', 'application/javascript']):
        data = response.get_data()
        if len(data) > 200:
            compressed = gzip.compress(data, compresslevel=6)
            response.set_data(compressed)
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Content-Length'] = len(compressed)
    return response

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

def render_page_to_jpeg(pdf_path, page_num, output_jpg, dpi=160, quality=88):
    output_jpg = Path(output_jpg)
    if output_jpg.exists():
        return True

    # Method 1: Native pdftoppm in ultra-clear grayscale at 160 DPI
    if shutil.which('pdftoppm'):
        prefix = output_jpg.with_suffix('')
        cmd = [
            'pdftoppm',
            '-gray',
            '-jpeg',
            '-jpegopt', f'quality={quality}',
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

    # Method 2: Fallback to portable pypdfium2 in crisp grayscale
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        if page_num < 1 or page_num > len(doc):
            doc.close()
            return False
        page = doc[page_num - 1]
        scale = dpi / 72.0
        pil_img = page.render(scale=scale).to_pil().convert('L')
        pil_img.save(str(output_jpg), 'JPEG', quality=quality)
        doc.close()
        return True
    except Exception as e:
        app.logger.error(f'Failed rendering page {page_num}: {e}')
        return False

# Pure monochrome minimalist single page template
SPA_HTML = '''<!DOCTYPE html>
<html manifest="/offline.appcache">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=3.0, user-scalable=yes">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black">
    <link rel="apple-touch-icon" href="/icon.png">
    <title>PDF</title>
    <style>
    * {
        -webkit-box-sizing: border-box;
        box-sizing: border-box;
        border-radius: 0 !important;
    }
    body, html {
        margin: 0;
        padding: 0;
        width: 100%;
        min-height: 100%;
        font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
        background-color: #ffffff;
        color: #000000;
    }

    /* Monochrome Library View */
    #libraryView {
        background-color: #ffffff;
        color: #000000;
        padding: 14px;
        font-size: 15px;
        line-height: 1.4;
    }
    .box {
        background: #ffffff;
        border: 1px solid #000000;
        padding: 12px;
        margin-bottom: 12px;
    }
    .box-title {
        font-size: 16px;
        font-weight: bold;
        margin: 0 0 8px 0;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .btn {
        display: inline-block;
        background: #000000;
        color: #ffffff !important;
        border: 1px solid #000000;
        padding: 8px 14px;
        font-size: 14px;
        font-weight: bold;
        cursor: pointer;
        text-align: center;
        text-decoration: none;
        -webkit-appearance: none;
    }
    .btn-light {
        background: #ffffff;
        color: #000000 !important;
        border: 1px solid #000000;
    }
    .book-table {
        width: 100%;
        border-collapse: collapse;
        border: 1px solid #000000;
        margin-top: 8px;
    }
    .book-table th, .book-table td {
        padding: 10px 8px;
        border: 1px solid #cccccc;
        text-align: left;
        vertical-align: middle;
    }
    .book-table th {
        background: #f0f0f0;
        border-bottom: 2px solid #000000;
        font-size: 13px;
        text-transform: uppercase;
    }
    input[type="file"] {
        font-size: 15px;
        padding: 6px 0;
        margin-bottom: 10px;
        display: block;
    }

    /* Monochrome Fullscreen Reader View */
    #readerView {
        display: none;
        background-color: #000000;
        color: #ffffff;
        min-height: 100%;
        width: 100%;
        overflow-x: hidden;
    }
    #topBar {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        height: 44px;
        background: #000000;
        border-bottom: 1px solid #444444;
        padding: 5px 10px;
        z-index: 999;
        font-size: 14px;
        box-sizing: border-box;
    }
    .nav-btn {
        color: #ffffff !important;
        text-decoration: none;
        font-weight: bold;
        float: left;
        line-height: 32px;
        font-size: 14px;
        padding: 0 12px;
        background: #222222;
        border: 1px solid #666666;
        border-radius: 4px;
        margin-right: 10px;
        cursor: pointer;
        display: inline-block;
    }
    .page-badge {
        float: right;
        color: #ffffff;
        font-size: 14px;
        line-height: 32px;
        font-weight: bold;
        letter-spacing: 1px;
    }
    #pageArea {
        padding-top: 48px;
        text-align: center;
        width: 100%;
        min-height: 100%;
        background-color: #000000;
    }
    #pageImg {
        display: block;
        margin: 0 auto;
        max-width: 100%;
        height: auto;
        background: #ffffff;
        border: 1px solid #333333;
    }
    #tapHint {
        font-size: 11px;
        color: #666666;
        text-align: center;
        padding: 6px 0;
        text-transform: uppercase;
    }
    #cacheStatus {
        display: none;
        background: #000000;
        border-bottom: 1px solid #ffffff;
        color: #ffffff;
        text-align: center;
        font-size: 13px;
        padding: 6px 10px;
        position: fixed;
        top: 38px;
        left: 0;
        right: 0;
        z-index: 998;
    }
    </style>
</head>
<body>

    <!-- ==================== 1. MONOCHROME LIBRARY VIEW ==================== -->
    <div id="libraryView">
        <div class="box">
            <div class="box-title">iPad PDF Library</div>
            <div>Address: <strong>http://{{ local_ip }}:{{ port }}</strong></div>
        </div>

        <div class="box">
            <div class="box-title">Offline Cache</div>
            <div style="margin-bottom: 10px; font-size: 13px; color: #444444;">
                Download all documents into iPad storage for offline reading:
            </div>
            <button onclick="startOfflineCacheAll()" id="libCacheBtn" class="btn" style="padding: 10px 18px;">Save All Books for Offline</button>
            <div id="libCacheStatus" style="margin-top: 8px; font-size: 13px; font-weight: bold;"></div>
        </div>

        <div class="box">
            <div class="box-title">Upload Document</div>
            <form action="/upload" method="post" enctype="multipart/form-data">
                <input type="file" name="pdf_file" accept=".pdf,application/pdf" required>
                <input type="submit" value="Upload" class="btn">
            </form>
        </div>

        <div class="box">
            <div class="box-title">Documents ({{ books|length }})</div>
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
                            <a href="javascript:void(0)" onclick="openBook({{ b.id }})" style="color: #000000; font-weight: bold; text-decoration: underline;">{{ b.title }}</a>
                            <div style="font-size: 12px; color: #555555;" id="progress_{{ b.id }}">Page {{ b.current_page }} of {{ b.page_count }}</div>
                        </td>
                        <td>{{ b.page_count }}</td>
                        <td style="white-space: nowrap;">
                            <button onclick="openBook({{ b.id }})" class="btn" style="padding: 6px 12px;">Open</button>
                            <form action="/delete/{{ b.id }}" method="post" style="display: inline; margin-left: 4px;" onsubmit="return confirm('Delete this PDF?');">
                                <input type="submit" value="Del" class="btn btn-light" style="padding: 6px 8px;">
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p style="color: #555555; margin: 0;">No documents uploaded.</p>
            {% endif %}
        </div>
    </div>


    <!-- ==================== 2. MONOCHROME READER VIEW ==================== -->
    <div id="readerView">
        <div id="topBar">
            <a href="javascript:void(0)" onclick="closeBook()" class="nav-btn">&larr; Library</a>
            <select id="readerBookSelect" onchange="openBook(this.value)" style="background: #222222; color: #ffffff; border: 1px solid #666666; border-radius: 4px; padding: 4px 8px; font-size: 14px; height: 32px; max-width: 185px; float: left; margin-top: 0px; -webkit-appearance: menulist;">
                {% for b in books %}
                <option value="{{ b.id }}">{{ b.title }} ({{ b.page_count }}p)</option>
                {% endfor %}
            </select>
            <a href="javascript:void(0)" onclick="startOfflineCacheAll()" class="nav-btn" id="readerCacheBtn" style="margin-left: 10px; font-size: 13px; font-weight: normal; color: #cccccc !important;">Save All</a>
            <span class="page-badge" id="pageDisplay">1 / 1</span>
        </div>
        <div id="cacheStatus"></div>

        <div id="pageArea">
            <img id="pageImg" src="" alt="PDF Page">
            <div id="tapHint">Swipe left: Next &bull; Swipe right: Prev</div>
        </div>
    </div>


    <!-- ==================== 3. SCRIPT (ES5 Monochrome Offline SPA) ==================== -->
    <script>
    var allBooks = {{ books_json|safe }};
    var currentBook = null;
    var currentPage = 1;

    function getBook(id) {
        for (var i = 0; i < allBooks.length; i++) {
            if (allBooks[i].id == id) return allBooks[i];
        }
        return null;
    }

    function openBook(id, page) {
        var b = getBook(id);
        if (!b) return;
        currentBook = b;

        if (page) {
            currentPage = page;
        } else {
            try {
                var saved = localStorage.getItem('page_' + b.id);
                currentPage = saved ? parseInt(saved, 10) : (b.current_page || 1);
            } catch(e) {
                currentPage = b.current_page || 1;
            }
        }
        currentPage = Math.max(1, Math.min(currentPage, b.page_count));

        try {
            localStorage.setItem('ipad_active_book', b.id);
            localStorage.setItem('page_' + b.id, currentPage);
        } catch(e) {}

        document.getElementById('libraryView').style.display = 'none';
        document.getElementById('readerView').style.display = 'block';
        document.getElementById('readerBookSelect').value = b.id;

        renderPage();
    }

    var activePreload = null;

    function closeBook() {
        try {
            localStorage.removeItem('ipad_active_book');
        } catch(e) {}
        if (activePreload) {
            activePreload.onload = null;
            activePreload.onerror = null;
            activePreload.src = '';
            activePreload = null;
        }
        document.getElementById('readerView').style.display = 'none';
        document.getElementById('libraryView').style.display = 'block';
        window.scrollTo(0, 0);
    }

    function renderPage() {
        if (!currentBook) return;
        var img = document.getElementById('pageImg');
        img.src = '/page/' + currentBook.id + '/' + currentPage + '?v=3';

        document.getElementById('pageDisplay').innerText = currentPage + ' / ' + currentBook.page_count;
        window.scrollTo(0, 0);

        try {
            localStorage.setItem('page_' + currentBook.id, currentPage);
        } catch(e) {}

        var progEl = document.getElementById('progress_' + currentBook.id);
        if (progEl) {
            progEl.innerText = 'Page ' + currentPage + ' of ' + currentBook.page_count;
        }

        var ping = new Image();
        ping.src = '/bookmark/' + currentBook.id + '/' + currentPage;

        if (activePreload) {
            activePreload.onload = null;
            activePreload.onerror = null;
            activePreload.src = '';
            activePreload = null;
        }

        if (currentPage < currentBook.page_count) {
            activePreload = new Image();
            activePreload.src = '/page/' + currentBook.id + '/' + (currentPage + 1) + '?v=3';
        }
    }

    function nextPage() {
        if (currentBook && currentPage < currentBook.page_count) {
            currentPage++;
            renderPage();
        }
    }

    function prevPage() {
        if (currentBook && currentPage > 1) {
            currentPage--;
            renderPage();
        }
    }

    function isInteractive(el) {
        while (el && el !== document.body && el !== document) {
            if (el.id === 'topBar' || el.tagName === 'A' || el.tagName === 'SELECT' || el.tagName === 'BUTTON' || el.tagName === 'INPUT' || el.tagName === 'OPTION') {
                return true;
            }
            el = el.parentNode;
        }
        return false;
    }

    var startX = 0, startY = 0, startTime = 0;
    window.addEventListener('touchstart', function(e) {
        if (e.touches && e.touches.length === 1) {
            startX = e.touches[0].clientX;
            startY = e.touches[0].clientY;
            startTime = new Date().getTime();
        }
    }, false);

    window.addEventListener('touchend', function(e) {
        if (document.getElementById('readerView').style.display === 'none') return;
        if (!e.changedTouches || e.changedTouches.length !== 1) return;

        if (startY <= 55 || isInteractive(e.target)) return;

        var endX = e.changedTouches[0].clientX;
        var endY = e.changedTouches[0].clientY;
        if (endY <= 55) return;

        var diffX = endX - startX;
        var diffY = endY - startY;
        var absX = Math.abs(diffX);
        var absY = Math.abs(diffY);
        var duration = new Date().getTime() - startTime;

        if (absX >= 35 && absX > absY && duration < 900) {
            if (e.cancelable) e.preventDefault();
            if (diffX < 0) nextPage();
            else prevPage();
            return;
        }

        if (absX < 15 && absY < 15 && duration < 350) {
            var width = window.innerWidth || 1024;
            if (endX > width * 0.75) {
                if (e.cancelable) e.preventDefault();
                nextPage();
            } else if (endX < width * 0.25) {
                if (e.cancelable) e.preventDefault();
                prevPage();
            }
        }
    }, false);

    document.addEventListener('keydown', function(e) {
        if (document.getElementById('readerView').style.display === 'none') return;
        if (e.keyCode === 37) prevPage();
        else if (e.keyCode === 39 || e.keyCode === 32) nextPage();
    }, false);

    var isCaching = false;
    function startOfflineCacheAll() {
        if (isCaching) return;
        isCaching = true;

        var statusTop = document.getElementById('cacheStatus');
        var statusLib = document.getElementById('libCacheStatus');
        statusTop.style.display = 'block';

        var queue = [];
        for (var i = 0; i < allBooks.length; i++) {
            var b = allBooks[i];
            for (var p = 1; p <= b.page_count; p++) {
                queue.push({ id: b.id, page: p, title: b.title, total: b.page_count });
            }
        }

        var totalItems = queue.length;
        var queueIdx = 0;

        function processQueue() {
            if (queueIdx >= totalItems) {
                var doneMsg = 'Saved all ' + allBooks.length + ' books (' + totalItems + ' pages) in grayscale. Ready offline.';
                statusTop.innerText = doneMsg;
                if (statusLib) statusLib.innerText = doneMsg;
                setTimeout(function() {
                    statusTop.style.display = 'none';
                }, 7000);
                isCaching = false;
                return;
            }

            var item = queue[queueIdx];
            var msg = 'Caching ' + item.title + ' (' + item.page + '/' + item.total + ') • ' + (queueIdx + 1) + '/' + totalItems;
            statusTop.innerText = msg;
            if (statusLib) statusLib.innerText = msg;

            var temp = new Image();
            temp.onload = temp.onerror = function() {
                temp.onload = null;
                temp.onerror = null;
                temp = null;
                queueIdx++;
                setTimeout(processQueue, 35);
            };
            temp.src = '/page/' + item.id + '/' + item.page + '?v=3';
        }

        processQueue();
    }

    window.addEventListener('load', function() {
        try {
            var savedBook = localStorage.getItem('ipad_active_book');
            if (savedBook && getBook(savedBook)) {
                openBook(savedBook);
            }
        } catch(e) {}
    }, false);
    </script>

</body>
</html>'''

@app.route('/')
def index():
    conn = get_db()
    books_rows = conn.execute('SELECT * FROM books ORDER BY created_at DESC').fetchall()
    conn.close()

    books_data = [
        {
            'id': b['id'],
            'title': b['title'],
            'page_count': b['page_count'],
            'current_page': b['current_page']
        }
        for b in books_rows
    ]
    books_json = json.dumps(books_data)
    local_ip = get_local_ip()
    port = os.environ.get('PORT', 5000)

    return render_template_string(
        SPA_HTML,
        books=books_data,
        books_json=books_json,
        local_ip=local_ip,
        port=port
    )

@app.route('/read/<int:book_id>')
def read_book_redirect(book_id):
    return redirect(url_for('index'))

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

    # Pre-render page 1 in grayscale
    render_page_to_jpeg(save_path, 1, CACHE_DIR / f"{book_id}_p1.jpg")
    if page_count > 1:
        render_page_to_jpeg(save_path, 2, CACHE_DIR / f"{book_id}_p2.jpg")

    return redirect(url_for('index'))

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
        
        for img in CACHE_DIR.glob(f"{book_id}_p*.jpg"):
            try:
                img.unlink()
            except Exception:
                pass

        with conn:
            conn.execute('DELETE FROM books WHERE id = ?', (book_id,))
    conn.close()
    return redirect(url_for('index'))

@app.route('/offline.appcache')
def offline_manifest():
    conn = get_db()
    books = conn.execute('SELECT id, page_count FROM books').fetchall()
    conn.close()

    version = int(time.time() // 60)
    lines = [
        'CACHE MANIFEST',
        f'# Version {version}',
        'CACHE:',
        '/',
        '/icon.png'
    ]
    for b in books:
        for p in range(1, b["page_count"] + 1):
            lines.append(f'/page/{b["id"]}/{p}?v=3')
    lines.append('NETWORK:')
    lines.append('*')

    response = app.response_class('\n'.join(lines), mimetype='text/cache-manifest')
    response.headers['Cache-Control'] = 'no-cache'
    return response

@app.route('/icon.png')
@app.route('/apple-touch-icon.png')
def app_icon():
    icon_path = BASE_DIR / 'static' / 'icon.png'
    if icon_path.exists():
        return send_file(icon_path, mimetype='image/png')
    abort(404)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"iPadPDF Server running on port {port}")
    print(f"Access on iPad at: http://{get_local_ip()}:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
