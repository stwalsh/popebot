import urequests as requests
import ujson as json
import time
import ntptime
import network
import gc
from machine import Pin, WDT, reset

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

        # Watchdog timer - resets device if not fed within 8 seconds
        self.wdt = None  # Initialized later in run_continuous

        # Failure tracking for hard reset
        self.consecutive_wifi_failures = 0
        self.MAX_WIFI_FAILURES = 5  # Hard reset after this many consecutive failures
        self.consecutive_ntp_failures = 0
        self.MAX_NTP_FAILURES = 3  # Reset WiFi after this many NTP failures

        # HTTP request timeout (seconds) - must be LESS than watchdog (8s)
        # so requests fail gracefully before watchdog hard-resets
        self.HTTP_TIMEOUT = 7

        # WiFi connection timeout (seconds) - mesh networks can be slow
        self.WIFI_TIMEOUT = 45

        # Logging (temporary - for diagnostics)
        self.log_file = 'log.txt'
        self.log_enabled = True

        # Load configuration and state
        self.load_config()
        self.load_state()

    def log(self, msg):
        """Write timestamped message to log file"""
        if not self.log_enabled:
            return
        try:
            t = time.gmtime()
            timestamp = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
                t[0], t[1], t[2], t[3], t[4], t[5])
            line = f"[{timestamp}] {msg}\n"
            print(line.strip())  # Also print to console
            with open(self.log_file, 'a') as f:
                f.write(line)
        except:
            pass  # Don't let logging break the bot

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
        """Save current position to state file (atomic write)"""
        import os
        tmp_file = self.state_file + '.tmp'
        try:
            with open(tmp_file, 'w') as f:
                json.dump(self.state, f)
            # Atomic rename - if power fails here, old state.json is intact
            os.rename(tmp_file, self.state_file)
        except OSError:
            print("Error: Could not save state file")

    def get_next_couplet(self):
        """Read the next couplet from file without loading everything into memory"""
        MAX_LINES = 20  # Safety limit to prevent RAM exhaustion
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
                        if len(lines) > MAX_LINES:
                            print(f"Warning: Couplet exceeded {MAX_LINES} lines, truncating")
                            self.state['position'] = f.tell()
                            return '\n'.join(lines)

        except OSError:
            print(f"Error: Could not read {self.couplets_file}")
            return None

    def connect_wifi(self):
        """Connect to WiFi network"""
        wlan = network.WLAN(network.STA_IF)

        # Reset the interface to clear any bad state
        # Longer delays help mesh networks clear stale sessions
        wlan.active(False)
        time.sleep(3)
        wlan.active(True)
        time.sleep(3)

        if not wlan.isconnected():
            print(f"Connecting to WiFi: {self.config['wifi_ssid']}")
            wlan.connect(self.config['wifi_ssid'], self.config['wifi_password'])

            # Wait for connection with timeout
            timeout = self.WIFI_TIMEOUT
            while not wlan.isconnected() and timeout > 0:
                self.feed_watchdog()
                time.sleep(1)
                timeout -= 1

        if wlan.isconnected():
            print(f"WiFi connected: {wlan.ifconfig()[0]}")
            # Disable power management to prevent mesh dropouts
            wlan.config(pm=0)
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
        # Try multiple NTP servers in case one is blocked
        ntp_servers = ['pool.ntp.org', 'time.google.com', 'time.cloudflare.com']

        for attempt in range(retries):
            # Rotate through servers
            server = ntp_servers[attempt % len(ntp_servers)]
            try:
                self.feed_watchdog()
                print(f"Syncing time with NTP ({server}, attempt {attempt + 1}/{retries})...")
                ntptime.host = server
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
                self.feed_watchdog()
                time.sleep(2)
        print("NTP sync failed after all retries")
        return False

    def ensure_time_valid(self):
        """Make sure time is valid, re-sync if needed"""
        if not self.time_is_valid():
            print("Clock looks wrong, re-syncing...")
            if self.sync_time():
                self.consecutive_ntp_failures = 0
                return True
            else:
                self.consecutive_ntp_failures += 1
                print(f"NTP failure {self.consecutive_ntp_failures}/{self.MAX_NTP_FAILURES}")
                if self.consecutive_ntp_failures >= self.MAX_NTP_FAILURES:
                    print("Too many NTP failures, resetting WiFi...")
                    self.consecutive_ntp_failures = 0
                    self.led.off()
                    self.connect_wifi()
                return False
        return True

    def authenticate_bluesky(self):
        """Authenticate with Bluesky ATP"""
        gc.collect()  # Clean heap before network call
        auth_url = "https://bsky.social/xrpc/com.atproto.server.createSession"

        auth_data = {
            "identifier": self.config['bluesky_handle'],
            "password": self.config['bluesky_password']
        }

        try:
            response = requests.post(auth_url, json=auth_data, timeout=self.HTTP_TIMEOUT)

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
            print(f"Authentication error (timeout or network): {e}")
            gc.collect()  # Clean up orphaned sockets on failure
            return False
        finally:
            if 'response' in locals():
                response.close()
            gc.collect()  # Always clean up after network call

    def refresh_session(self):
        """Refresh the Bluesky session using refresh token"""
        if not self.refresh_jwt:
            return self.authenticate_bluesky()

        gc.collect()  # Clean heap before network call
        refresh_url = "https://bsky.social/xrpc/com.atproto.server.refreshSession"

        headers = {
            "Authorization": f"Bearer {self.refresh_jwt}"
        }

        try:
            response = requests.post(refresh_url, headers=headers, timeout=self.HTTP_TIMEOUT)

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
            print(f"Refresh error (timeout or network): {e}")
            gc.collect()  # Clean up orphaned sockets on failure
            return self.authenticate_bluesky()
        finally:
            if 'response' in locals():
                response.close()
            gc.collect()  # Always clean up after network call

    def sanitize_text(self, text):
        """Replace curly quotes and other problematic characters with ASCII equivalents"""
        # Known replacements for common typographic characters
        replacements = [
            ('\u2019', "'"),  # Right single quote -> apostrophe
            ('\u2018', "'"),  # Left single quote -> apostrophe
            ('\u201c', '"'),  # Left double quote -> straight quote
            ('\u201d', '"'),  # Right double quote -> straight quote
            ('\u2014', '--'), # Em dash
            ('\u2013', '-'),  # En dash
            ('\u2026', '...'), # Ellipsis
            ('\u00e6', 'ae'), # ae ligature
            ('\u0153', 'oe'), # oe ligature
        ]
        for old, new in replacements:
            text = text.replace(old, new)

        # Aggressive fallback: strip any remaining non-ASCII characters
        cleaned = []
        for char in text:
            if ord(char) < 128:
                cleaned.append(char)
            # Skip non-ASCII characters entirely
        return ''.join(cleaned)

    def create_post(self, text):
        """Create a post on Bluesky"""
        if not self.access_jwt:
            print("Not authenticated with Bluesky")
            return False

        # Clean heap before memory-intensive operations
        gc.collect()

        post_url = "https://bsky.social/xrpc/com.atproto.repo.createRecord"

        headers = {
            "Authorization": f"Bearer {self.access_jwt}",
            "Content-Type": "application/json"
        }

        # Sanitize text to avoid JSON encoding issues
        text = self.sanitize_text(text)

        # Verify time is still valid right before posting
        t = time.gmtime()
        if t[0] < 2024:
            print(f"Clock invalid ({t[0]}), refusing to post with wrong timestamp")
            return False

        # Create timestamp in ISO 8601 format
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
            gc.collect()  # Clean up after json.dumps before network call
            response = requests.post(post_url, data=payload, headers=headers, timeout=self.HTTP_TIMEOUT)

            if response.status_code == 200:
                return True
            elif response.status_code in (400, 401):
                # Token expired - Bluesky returns 400 with ExpiredToken, not 401
                self.log(f"Token expired ({response.status_code}), refreshing...")
                response.close()
                if self.refresh_session():
                    return self.create_post(text)  # Retry with new token
                self.log("Token refresh failed")
                return False
            else:
                self.log(f"Post failed: HTTP {response.status_code}")
                try:
                    self.log(f"Response: {response.text[:200]}")
                except Exception:
                    pass
                return False

        except Exception as e:
            self.log(f"Post exception: {e}")
            gc.collect()  # Clean up orphaned sockets on failure
            return False
        finally:
            if 'response' in locals():
                response.close()
            gc.collect()  # Always clean up after network call

    def blink_led(self, times=3):
        """Blink LED to indicate activity"""
        for _ in range(times):
            self.led.off()
            time.sleep(0.2)
            self.led.on()
            time.sleep(0.2)

    def heartbeat(self):
        """Quick single blink to show the bot is alive"""
        self.led.off()
        time.sleep(0.05)
        self.led.on()

    def feed_watchdog(self):
        """Feed the watchdog timer to prevent reset"""
        if self.wdt:
            self.wdt.feed()

    def active_ping(self):
        """Send a tiny request to keep mesh network connection alive"""
        try:
            self.feed_watchdog()
            res = requests.head("http://connectivitycheck.gstatic.com/generate_204", timeout=self.HTTP_TIMEOUT)
            res.close()
            gc.collect()  # Clean up after each ping to prevent memory buildup
            print("  (mesh ping ok)")
            return True
        except Exception:
            print("  (mesh ping failed)")
            gc.collect()
            return False

    def sleep_with_heartbeat(self, total_seconds):
        """Sleep in chunks with periodic heartbeat blinks and watchdog feeds"""
        chunk_seconds = 5  # Wake up every 5 seconds (must be < 8s watchdog timeout)
        elapsed = 0
        while elapsed < total_seconds:
            sleep_time = min(chunk_seconds, total_seconds - elapsed)
            time.sleep(sleep_time)
            elapsed += sleep_time
            self.feed_watchdog()
            self.heartbeat()

            # Active ping every 60 seconds to keep mesh network connection alive
            if elapsed % 60 == 0 and elapsed > 0:
                remaining = total_seconds - elapsed
                if not self.active_ping():
                    # Ping failed - force reconnect
                    self.log(f"Ping failed at {elapsed}s, forcing WiFi reconnect")
                    self.led.off()
                    self.connect_wifi()
                    self.access_jwt = None  # Force re-auth after reconnect
                    self.log("Reconnected, cleared auth tokens")

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
        self.log(f"=== Bot starting, {interval} min intervals ===")

        # Enable watchdog if not already enabled (may be initialized in main())
        if not self.wdt:
            print("Enabling watchdog timer...")
            self.wdt = WDT(timeout=8000)
        self.feed_watchdog()

        while True:
            try:
                self.feed_watchdog()

                # Check WiFi connection
                wlan = network.WLAN(network.STA_IF)
                if not wlan.isconnected():
                    self.log("WiFi disconnected, reconnecting...")
                    self.led.off()
                    if not self.connect_wifi():
                        self.consecutive_wifi_failures += 1
                        self.log(f"WiFi failure {self.consecutive_wifi_failures}/{self.MAX_WIFI_FAILURES}")
                        if self.consecutive_wifi_failures >= self.MAX_WIFI_FAILURES:
                            self.log("Too many WiFi failures, performing hard reset...")
                            time.sleep(1)
                            reset()
                        self.sleep_with_heartbeat(60)
                        continue
                    self.consecutive_wifi_failures = 0  # Reset counter on success
                    self.sync_time()
                    self.access_jwt = None  # Force re-auth after WiFi reconnect
                    self.log("WiFi reconnected, cleared auth tokens")

                self.feed_watchdog()

                # Verify time is valid before posting
                if not self.ensure_time_valid():
                    self.log("Cannot verify time, skipping this cycle")
                    self.sleep_with_heartbeat(60)
                    continue

                self.feed_watchdog()

                # Authenticate if needed
                if not self.access_jwt:
                    self.log("No auth token, authenticating...")
                    if not self.authenticate_bluesky():
                        self.log("Auth failed, will retry next cycle")
                        self.sleep_with_heartbeat(60)
                        continue
                    self.log("Auth successful")

                self.feed_watchdog()

                # Post next couplet
                old_pos = self.state['position']
                couplet = self.get_next_couplet()
                if couplet:
                    self.log(f"Posting couplet (pos={self.state['position']})...")
                    if self.create_post(couplet):
                        self.save_state()
                        self.log("Post successful")
                        self.blink_led(2)
                    else:
                        self.log("Post FAILED - will retry same couplet")
                        self.state['position'] = old_pos  # Restore position to retry
                        self.blink_led(5)
                else:
                    self.log("All couplets posted! Stopping.")
                    break

                self.feed_watchdog()
                gc.collect()

                self.log(f"Sleeping {interval} minutes...")
                self.sleep_with_heartbeat(interval * 60)

            except KeyboardInterrupt:
                self.log("Bot stopped by user")
                break
            except Exception as e:
                self.log(f"ERROR in main loop: {e}")
                self.blink_led(10)
                self.sleep_with_heartbeat(60)

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

    # Enable watchdog immediately - protects entire startup sequence
    print("Enabling watchdog timer...")
    bot.wdt = WDT(timeout=8000)
    bot.feed_watchdog()

    if not bot.connect_wifi():
        print("Could not connect to WiFi")
        return

    if not bot.sync_time():
        print("Could not sync time - refusing to post with wrong clock")
        return

    bot.run_continuous()


if __name__ == "__main__":
    main()
