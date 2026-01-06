#!/usr/bin/env python3
"""
Test harness for popebot - runs on desktop Python instead of MicroPython.
Uses standard libraries instead of MicroPython-specific ones.
"""

import json
import time
from datetime import datetime, timezone

# Only import requests when needed (for network operations)
requests = None
def get_requests():
    global requests
    if requests is None:
        import requests as req
        requests = req
    return requests

class MockLED:
    """Mock LED for testing without hardware"""
    def __init__(self):
        self.state = False
    def on(self):
        self.state = True
        print("[LED] ON")
    def off(self):
        self.state = False
        print("[LED] OFF")
    def toggle(self):
        self.state = not self.state
        print(f"[LED] {'ON' if self.state else 'OFF'}")

class BlueskyPoetryBot:
    def __init__(self, config_file='config.json', couplets_file='couplets.txt', state_file='state.json'):
        self.config_file = config_file
        self.couplets_file = couplets_file
        self.state_file = state_file
        self.config = {}
        self.state = {'position': 0}
        self.access_jwt = None
        self.refresh_jwt = None
        self.led = MockLED()

        self.load_config()
        self.load_state()

    def load_config(self):
        try:
            with open(self.config_file, 'r') as f:
                self.config = json.load(f)
            self.config.setdefault('interval_minutes', 10)
            print("Configuration loaded successfully")
        except FileNotFoundError:
            print(f"Error: Could not load {self.config_file}")
            raise

    def load_state(self):
        try:
            with open(self.state_file, 'r') as f:
                self.state = json.load(f)
            print(f"Resuming from position {self.state['position']}")
        except FileNotFoundError:
            print("No state file, starting from beginning")
            self.state = {'position': 0}

    def save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f)
        except IOError:
            print("Error: Could not save state file")

    def get_next_couplet(self):
        try:
            with open(self.couplets_file, 'r') as f:
                f.seek(self.state['position'])
                lines = []
                while True:
                    line = f.readline()
                    if not line:
                        if lines:
                            self.state['position'] = f.tell()
                            return '\n'.join(lines)
                        return None
                    line = line.rstrip('\n\r')
                    if line == '---':
                        self.state['position'] = f.tell()
                        return '\n'.join(lines)
                    else:
                        lines.append(line)
        except FileNotFoundError:
            print(f"Error: Could not read {self.couplets_file}")
            return None

    def authenticate_bluesky(self):
        auth_url = "https://bsky.social/xrpc/com.atproto.server.createSession"
        auth_data = {
            "identifier": self.config['bluesky_handle'],
            "password": self.config['bluesky_password']
        }

        try:
            response = get_requests().post(auth_url, json=auth_data)
            if response.status_code == 200:
                auth_response = response.json()
                self.access_jwt = auth_response['accessJwt']
                self.refresh_jwt = auth_response['refreshJwt']
                print("Bluesky authentication successful")
                return True
            else:
                print(f"Authentication failed: {response.status_code}")
                print(response.text)
                return False
        except Exception as e:
            print(f"Authentication error: {e}")
            return False

    def refresh_session(self):
        if not self.refresh_jwt:
            return self.authenticate_bluesky()

        refresh_url = "https://bsky.social/xrpc/com.atproto.server.refreshSession"
        headers = {"Authorization": f"Bearer {self.refresh_jwt}"}

        try:
            response = get_requests().post(refresh_url, headers=headers)
            if response.status_code == 200:
                auth_response = response.json()
                self.access_jwt = auth_response['accessJwt']
                self.refresh_jwt = auth_response['refreshJwt']
                print("Session refreshed successfully")
                return True
            else:
                print(f"Refresh failed ({response.status_code}), re-authenticating...")
                return self.authenticate_bluesky()
        except Exception as e:
            print(f"Refresh error: {e}")
            return self.authenticate_bluesky()

    def create_post(self, text):
        if not self.access_jwt:
            print("Not authenticated with Bluesky")
            return False

        post_url = "https://bsky.social/xrpc/com.atproto.repo.createRecord"
        headers = {
            "Authorization": f"Bearer {self.access_jwt}",
            "Content-Type": "application/json"
        }

        iso_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        record = {
            "repo": self.config['bluesky_handle'],
            "collection": "app.bsky.feed.post",
            "record": {
                "$type": "app.bsky.feed.post",
                "text": text,
                "createdAt": iso_time
            }
        }

        try:
            response = get_requests().post(post_url, json=record, headers=headers)
            if response.status_code == 200:
                print(f"Posted: {text[:50]}...")
                return True
            elif response.status_code == 401:
                print("Token expired, refreshing...")
                if self.refresh_session():
                    return self.create_post(text)
                return False
            else:
                print(f"Post failed: {response.status_code}")
                print(response.text)
                return False
        except Exception as e:
            print(f"Post error: {e}")
            return False

    def blink_led(self, times=3):
        for _ in range(times):
            self.led.off()
            time.sleep(0.1)
            self.led.on()
            time.sleep(0.1)

    def post_next_couplet(self):
        couplet = self.get_next_couplet()
        if not couplet:
            print("No more couplets to post")
            return False

        if self.create_post(couplet):
            self.save_state()
            self.blink_led(2)
            return True
        else:
            self.blink_led(5)
            return False


def test_couplet_reading():
    """Test that couplets can be read correctly"""
    print("\n=== Testing Couplet Reading ===")
    bot = BlueskyPoetryBot()

    for i in range(3):
        couplet = bot.get_next_couplet()
        if couplet:
            print(f"\nCouplet {i+1}:")
            print(couplet)
            print("-" * 40)
        else:
            print("No more couplets")
            break

    # Reset state for next test
    bot.state = {'position': 0}
    print("\nCouplet reading test complete!")


def test_authentication():
    """Test Bluesky authentication"""
    print("\n=== Testing Authentication ===")
    bot = BlueskyPoetryBot()

    if bot.authenticate_bluesky():
        print("Authentication test PASSED")
        return bot
    else:
        print("Authentication test FAILED")
        print("Check your credentials in config.json")
        return None


def test_single_post(dry_run=True):
    """Test posting a single couplet"""
    print("\n=== Testing Single Post ===")
    bot = BlueskyPoetryBot()

    couplet = bot.get_next_couplet()
    if not couplet:
        print("No couplets available")
        return

    print(f"Would post:\n{couplet}\n")

    if dry_run:
        print("[DRY RUN] Skipping actual post")
        return

    if not bot.authenticate_bluesky():
        print("Authentication failed")
        return

    if bot.create_post(couplet):
        bot.save_state()
        print("Post successful!")
    else:
        print("Post failed!")


def main():
    import sys

    print("Popebot Test Harness")
    print("=" * 40)

    if len(sys.argv) < 2:
        print("\nUsage:")
        print("  python test_popebot.py read      - Test reading couplets")
        print("  python test_popebot.py auth      - Test Bluesky authentication")
        print("  python test_popebot.py dry       - Dry run (show what would post)")
        print("  python test_popebot.py post      - Actually post one couplet")
        print("  python test_popebot.py reset     - Reset to first couplet")
        return

    command = sys.argv[1].lower()

    if command == 'read':
        test_couplet_reading()
    elif command == 'auth':
        test_authentication()
    elif command == 'dry':
        test_single_post(dry_run=True)
    elif command == 'post':
        confirm = input("This will post to Bluesky. Continue? [y/N] ")
        if confirm.lower() == 'y':
            test_single_post(dry_run=False)
        else:
            print("Cancelled")
    elif command == 'reset':
        with open('state.json', 'w') as f:
            json.dump({'position': 0}, f)
        print("Reset to beginning")
    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
