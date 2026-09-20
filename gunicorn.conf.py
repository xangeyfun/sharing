import multiprocessing

bind = "127.0.0.1:8003"
workers = 2
worker_class = "gthread"
threads = 4
timeout = 300
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
worker_tmp_dir = "/dev/shm"
accesslog = "-"
errorlog = "-"
loglevel = "warning"