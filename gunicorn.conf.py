"""Gunicorn configuration for ProspectGrid API"""
import os
import multiprocessing

# Server socket
bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# Worker processes
workers = int(os.getenv('WEB_CONCURRENCY', '2'))
worker_class = 'gthread'  # Use threaded workers for background tasks
threads = 4  # 4 threads per worker

# Worker timeout (allow long-running background tasks)
timeout = 120  # 2 minutes
graceful_timeout = 30
keepalive = 5

# Logging
accesslog = '-'
errorlog = '-'
loglevel = 'info'

# Process naming
proc_name = 'prospectgrid'

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Worker lifecycle
max_requests = 1000  # Restart workers after 1000 requests (prevent memory leaks)
max_requests_jitter = 50
preload_app = False  # Don't preload (allows worker isolation)
