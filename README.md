# Tapo Circadian & Color Light Manager

A robust, local Python manager for **TP-Link Tapo L530 Multicolor Smart Bulbs**. 

Built for shift workers operating across inverted time zones (e.g., working US hours while living in Asia) who need automated circadian body-clock lighting, seamless smartphone app synchronization, and a natural-language CLI.

---

## Features

* **Circadian Body-Clock Automation:** Automatically tracks 24-hour phase transitions with smooth linear interpolation (`lerp`):
  * **08:00 – 16:00 (Sleep):** Lights completely OFF.
  * **16:00 – 17:00 (Wake-Up / Dawn):** Warm 3000K @ 30% gradually ramping to bright 6000K @ 100%.
  * **17:00 – 22:00 (Pre-Shift Daylight):** 6000K @ 100%.
  * **22:00 – 01:00 (Peak Shift):** Focused 6500K @ 100%.
  * **01:00 – 05:00 (Late Shift Transition):** 6500K slowly dialing back to 3500K @ 75%.
  * **05:00 – 06:00 (Shift End):** 3500K @ 75% tapering to 3000K @ 50%.
  * **06:00 – 08:00 (Post-Work Wind-Down):** Relaxing 2700K @ 35% stepping down to 2500K @ 10%.
* **Natural Language Color CLI:** Supports 80+ standard CSS color names, raw hex codes, white temperature presets, Kelvin values, and standalone brightness percentages.
* **Smart App-Override Detection:** Automatically detects if you changed power, color, Kelvin, or brightness via the physical switch or Tapo mobile app, and gracefully pauses the daemon so it never fights your adjustments.
* **Concurrency-Safe:** Inter-process file locking (`fcntl.flock`) prevents the background daemon from colliding with manual terminal commands.
* **Emergency Police Mode:** Background alternating Red/Blue emergency flash mode with configurable intervals.

---

## CLI Usage

Run `lights` with no arguments to sync back to the circadian schedule, or specify any color/brightness:

```bash
# Return to the active circadian schedule
lights
lights schedule

# Named colors with optional brightness (1-100%)
lights green 60
lights purple 40
lights cyan 80
lights gold 50
lights hotpink

# Hex codes
lights ff8800 60
lights '#33ff99' 50

# White temperature presets & direct Kelvin
lights warm 75       # 2700K at 75%
lights soft 80       # 3000K at 80%
lights daylight 100  # 5500K at 100%
lights cool 50       # 6500K at 50%
lights 4000k 60      # Kelvin directly

# Brightness slider (keeps current color)
lights 50%
lights 80

# Power & status
lights on
lights off
lights status

# Emergency police flash
lights police [--interval 0.25]
lights stop
```

---

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone git@github.com:sirbudoy/tapo-lights.git
   cd tapo-lights
   ```

2. **Create a virtual environment and install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configure your credentials:**
   ```bash
   cp .env.example .env
   nano .env
   ```
   Add your Tapo account username, password, and the local IP addresses of your L530 bulbs.

4. **Create a global wrapper script (optional):**
   ```bash
   sudo bash -c 'cat <<EOF > /usr/local/bin/lights
   #!/usr/bin/env bash
   exec /path/to/tapo-lights/.venv/bin/python /path/to/tapo-lights/tapo_manager.py "\$@"
   EOF'
   sudo chmod +x /usr/local/bin/lights
   ```

5. **Run as a 24/7 background systemd service:**
   ```bash
   sudo bash -c 'cat <<EOF > /etc/systemd/system/tapo-lights.service
   [Unit]
   Description=Tapo Smart Lights Circadian Management Service
   After=network-online.target
   Wants=network-online.target

   [Service]
   Type=simple
   User=david
   WorkingDirectory=/path/to/tapo-lights
   ExecStart=/path/to/tapo-lights/.venv/bin/python /path/to/tapo-lights/tapo_manager.py daemon --interval 180
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   EOF'

   sudo systemctl daemon-reload
   sudo systemctl enable --now tapo-lights.service
   ```

---

## License

MIT License. Feel free to use, modify, and expand.
