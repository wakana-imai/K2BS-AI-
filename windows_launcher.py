"""Start the evaluation app and open a browser after HTTP readiness."""
import os
import threading
import time
import urllib.request
import webbrowser

from app import create_app


def open_when_ready(port):
    # Loopback request only: do not send it through a configured Internet proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    address = f'http://127.0.0.1:{port}/login'
    for _ in range(60):
        try:
            with opener.open(address, timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(address)
                    return
        except OSError:
            pass
        time.sleep(0.5)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '5000'))
    app = create_app()
    threading.Thread(target=open_when_ready, args=(port,), daemon=True).start()
    app.run(host='127.0.0.1', port=port, debug=False)
