import urequests as requests
import ujson as json
import time
import ntptime
import network
import gc
from machine import Pin

class BlueskyPoetryBot:
    def __init__(self, config_file='config.json', couplets_file='couplets.txt', state_file='state.json'):
        self.config_file = config_file
        self.couplets_file = couplets_file
        self.state_file = state_file
        self.config = {}
        self.state = {'position': 0}
        self.access_jwt = None
        self.refresh_jwt = None
        self.did = None

        # Status LED
        self.led = Pin("LED", Pin.OUT)
        self.led.off()

        # Load configuration and state
        self.load_config()
        self.load_state()

    def load_config(self):
        """Load configuration from JSON file"""
        try:
            with open(self.config_file, 'r') as f:
                self.config = json.load(f)
            # Set defaults
            self.config.setdefault('interval_minutes', 10)
            print("Configuration loaded successfully")
        except OSError:
            print(f"Error: Could not load {self.config_file}")
            raise

    def load_state(self):
        """Load bot state (current position in couplets file)"""
        try:
            with open(self.state_file, 'r') as f:
                self.state = json.load(f)
            print(f"Resuming from position {self.state['position']}")
        except OSError:
            print("No state file, starting from beginning")
            self.state = {'position': 0}

    def save_state(self):
        """Save current position to state file"""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f)
        except OSError:
            print("Error: Could not save state file")

    def get_next_couplet(self):
        """Read the next couplet from file without loading everything into memory"""
        try:
            with open(self.couplets_file, 'r') as f:
                f.seek(self.state['position'])

                lines = []
                while True:
                    line = f.readline()
                    if not line:
                        # End of file
                        if lines:
                            self.state['position'] = f.tell()
                            return '\n'.join(lines)
                        return None

                    line = line.rstrip('\n\r')
                    if line == '---':
                        # Delimiter found, return collected lines
                        self.state['position'] = f.tell()
                        return '\n'.join(lines)
                    else:
                        lines.append(line)

        except OSError:
            print(f"Error: Could not read {self.couplets_file}")
            return None

    def connect_wifi(self):
        """Connect to WiFi network"""
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)

        if not wlan.isconnected():
            print(f"Connecting to WiFi: {self.config['wifi_ssid']}")
            wlan.connect(self.config['wifi_ssid'], self.config['wifi_password'])

            # Wait for connection with timeout
            timeout = 20
            while not wlan.isconnected() and timeout > 0:
                time.sleep(1)
                timeout -= 1

        if wlan.isconnected():
            print(f"WiFi connected: {wlan.ifconfig()[0]}")
            self.led.on()
            return True
        else:
            print("WiFi connection failed")
            return False

    def time_is_valid(self):
        """Check if system time looks reasonable (year 2024 or later)"""
        t = time.gmtime()
        return t[0] >= 2024

    def sync_time(self, retries=5):
        """Sync time with NTP server, with retries"""
        for attempt in range(retries):
            try:
                print(f"Syncing time with NTP (attempt {attempt + 1}/{retries})...")
                ntptime.settime()
                t = time.gmtime()
                # Sanity check - year should be 2024 or later
                if t[0] >= 2024:
                    print(f"Time synced: {t[0]}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}:{t[5]:02d} UTC")
                    return True
                else:
                    print(f"Time sync returned invalid year: {t[0]}")
            except Exception as e:
                print(f"NTP sync failed: {e}")
            if attempt < retries - 1:
                time.sleep(2)
        print("NTP sync failed after all retries")
        return False

    def ensure_time_valid(self):
        """Make sure time is valid, re-sync if needed"""
        if not self.time_is_valid():
            print("Clock looks wrong, re-syncing...")
            return self.sync_time()
        return True

    def authenticate_bluesky(self):
        """Authenticate with Bluesky ATP"""
        auth_url = "https://bsky.social/xrpc/com.atproto.server.createSession"

        auth_data = {
            "identifier": self.config['bluesky_handle'],
            "password": self.config['bluesky_password']
        }

        try:
            response = requests.post(auth_url, json=auth_data)

            if response.status_code == 200:
                auth_response = response.json()
                self.access_jwt = auth_response['accessJwt']
                self.refresh_jwt = auth_response['refreshJwt']
                self.did = auth_response['did']
                print("Bluesky authentication successful")
                return True
            else:
                print(f"Authentication failed: {response.status_code}")
                return False

        except Exception as e:
            print(f"Authentication error: {e}")
            return False
        finally:
            if 'response' in locals():
                response.close()

    def refresh_session(self):
        """Refresh the Bluesky session using refresh token"""
        if not self.refresh_jwt:
            return self.authenticate_bluesky()

        refresh_url = "https://bsky.social/xrpc/com.atproto.server.refreshSession"

        headers = {
            "Authorization": f"Bearer {self.refresh_jwt}"
        }

        try:
            response = requests.post(refresh_url, headers=headers)

            if response.status_code == 200:
                auth_response = response.json()
                self.access_jwt = auth_response['accessJwt']
                self.refresh_jwt = auth_response['refreshJwt']
                self.did = auth_response['did']
                print("Session refreshed successfully")
                return True
            else:
                print(f"Refresh failed ({response.status_code}), re-authenticating...")
                return self.authenticate_bluesky()

        except Exception as e:
            print(f"Refresh error: {e}")
            return self.authenticate_bluesky()
        finally:
            if 'response' in locals():
                response.close()

    def sanitize_text(self, text):
        """Replace curly quotes and other problematic characters with ASCII equivalents"""
        replacements = [
            ('\u2019', "'"),  # Right single quote -> apostrophe
            ('\u2018', "'"),  # Left single quote -> apostrophe
            ('\u201c', '"'),  # Left double quote -> straight quote
            ('\u201d', '"'),  # Right double quote -> straight quote
            ('\u2014', '--'), # Em dash
            ('\u2013', '-'),  # En dash
        ]
        for old, new in replacements:
            text = text.replace(old, new)
        return text

    def create_post(self, text):
        """Create a post on Bluesky"""
        if not self.access_jwt:
            print("Not authenticated with Bluesky")
            return False

        post_url = "https://bsky.social/xrpc/com.atproto.repo.createRecord"

        headers = {
            "Authorization": f"Bearer {self.access_jwt}",
            "Content-Type": "application/json"
        }

        # Sanitize text to avoid JSON encoding issues
        text = self.sanitize_text(text)

        # Create timestamp in ISO 8601 format
        t = time.gmtime()
        iso_time = "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}.000Z".format(
            t[0], t[1], t[2], t[3], t[4], t[5])

        record = {
            "repo": self.did,
            "collection": "app.bsky.feed.post",
            "record": {
                "$type": "app.bsky.feed.post",
                "text": text,
                "createdAt": iso_time
            }
        }

        try:
            payload = json.dumps(record)
            response = requests.post(post_url, data=payload, headers=headers)

            if response.status_code == 200:
                print(f"Posted: {text[:50]}...")
                return True
            elif response.status_code == 401:
                # Token expired, try refresh
                print("Token expired, refreshing...")
                response.close()
                if self.refresh_session():
                    return self.create_post(text)  # Retry with new token
                return False
            else:
                print(f"Post failed: {response.status_code}")
                print(response.text)
                return False

        except Exception as e:
            print(f"Post error: {e}")
            return False
        finally:
            if 'response' in locals():
                response.close()

    def blink_led(self, times=3):
        """Blink LED to indicate activity"""
        for _ in range(times):
            self.led.off()
            time.sleep(0.2)
            self.led.on()
            time.sleep(0.2)

    def post_next_couplet(self):
        """Post the next couplet from the file"""
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

    def run_continuous(self):
        """Run the bot continuously"""
        interval = self.config.get('interval_minutes', 10)
        print(f"Starting continuous mode ({interval} min intervals)")

        while True:
            try:
                # Check WiFi connection
                wlan = network.WLAN(network.STA_IF)
                if not wlan.isconnected():
                    print("WiFi disconnected, reconnecting...")
                    self.led.off()
                    if not self.connect_wifi():
                        time.sleep(60)
                        continue
                    self.sync_time()

                # Verify time is valid before posting
                if not self.ensure_time_valid():
                    print("Cannot verify time, skipping this cycle")
                    time.sleep(60)
                    continue

                # Authenticate if needed
                if not self.access_jwt:
                    if not self.authenticate_bluesky():
                        time.sleep(60)
                        continue

                # Post next couplet
                couplet = self.get_next_couplet()
                if couplet:
                    print(f"Posting couplet...")
                    if self.create_post(couplet):
                        self.save_state()
                        self.blink_led(2)
                    else:
                        self.blink_led(5)
                else:
                    print("All couplets posted! Stopping.")
                    break

                gc.collect()

                print(f"Waiting {interval} minutes...")
                time.sleep(interval * 60)

            except KeyboardInterrupt:
                print("\nBot stopped by user")
                break
            except Exception as e:
                print(f"Error: {e}")
                self.blink_led(10)
                time.sleep(60)

    def run_single(self):
        """Post a single couplet and exit"""
        if not self.connect_wifi():
            return False

        self.sync_time()

        if not self.authenticate_bluesky():
            return False

        return self.post_next_couplet()


def main():
    bot = BlueskyPoetryBot()

    if not bot.connect_wifi():
        print("Could not connect to WiFi")
        return

    if not bot.sync_time():
        print("Could not sync time - refusing to post with wrong clock")
        return

    bot.run_continuous()


if __name__ == "__main__":
    main()
