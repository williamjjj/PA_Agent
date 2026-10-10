"""Send MT5 closed bars from a Windows terminal to your own web account.

Run: python -m pa_agent.bridge --url https://your-app.vercel.app --username alice
No model key is needed on the terminal. Login credentials remain in memory.
"""
import argparse
import getpass
import time
from urllib.parse import urlsplit

import httpx

from pa_agent.data.mt5 import MT5Source
from pa_agent.data.snapshot import take_snapshot_from_bars


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--timeframe", default="15m", choices=["1m", "5m", "15m", "30m", "1h", "4h", "1d"])
    parser.add_argument("--count", type=int, default=100, choices=range(50, 501), metavar="50..500")
    args = parser.parse_args()
    url = args.url.rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
        parser.error("--url must use HTTPS (HTTP is allowed only for local development)")
    password = getpass.getpass("PA Agent account password: ")
    source = MT5Source()
    with httpx.Client(base_url=url, headers={"x-pa-request": "1"}, timeout=30, follow_redirects=False) as client:
        def login():
            response = client.post("/api/session", json={"username": args.username, "password": password})
            response.raise_for_status()
        login()
        try:
            source.connect()
            source.subscribe(args.symbol, args.timeframe)
            while True:
                try:
                    frame = take_snapshot_from_bars(source.latest_snapshot(args.count+60), args.count, args.symbol, args.timeframe)
                    rows = ["time,open,high,low,close,volume"]
                    rows += [f"{int(b.ts_open/1000)},{b.open},{b.high},{b.low},{b.close},{b.volume}" for b in reversed(frame.bars)]
                    data = {"csv": "\n".join(rows), "symbol": args.symbol, "timeframe": args.timeframe, "source": "csv"}
                    response = client.put("/api/bridge", json=data)
                    if response.status_code == 401:
                        login()
                        response = client.put("/api/bridge", json=data)
                    response.raise_for_status()
                    print(time.strftime("%H:%M:%S"), "Uploaded", args.count, "closed bars.")
                except (httpx.HTTPError, ValueError):
                    print("Update failed; verify the web account, symbol, timeframe and connection.")
                time.sleep(60)
        except KeyboardInterrupt:
            print("Bridge stopped.")
        finally:
            source.disconnect()


if __name__ == "__main__":
    main()
