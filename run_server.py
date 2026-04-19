#!/usr/bin/env python3
import os
os.environ['PHONEIDE_PORT'] = '1239'
from server import app
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=1239, debug=False, threaded=True, use_reloader=False)
