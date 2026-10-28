#!/usr/bin/env python3
"""
Tapo Circadian Light Manager (Multi-Room).

Configured rooms in .env via TAPO_IPS_<ROOM> (e.g., TAPO_IPS_BEDROOM=192.168.68.68,192.168.68.59).

Body clock schedule (Work: 22:00 - 06:00, Sleep: 08:00 - 16:00):
  - 08:00 - 16:00 : Sleep (OFF)
  - 16:00 - 17:00 : Wake-Up / Dawn (3000K @ 30% -> 6000K @ 100%)
  - 17:00 - 22:00 : Pre-Shift Daylight (6000K @ 100%)
  - 22:00 - 01:00 : Peak Work Shift (6500K @ 100%)
  - 01:00 - 02:00 : Mid-Shift Transition (6500K @ 100% -> 5500K @ 100%)
  - 02:00 - 05:00 : Late Shift (5500K @ 100% -> 3500K @ 75%)
  - 05:00 - 06:00 : End of Shift (3500K @ 75% -> 3000K @ 50%)
  - 06:00 - 08:00 : Wind-Down (2700K @ 35% -> 2500K @ 10%)
"""

import argparse
import asyncio
import colorsys
from contextlib import contextmanager
import datetime as dt
import fcntl
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from dotenv import load_dotenv
from tapo import ApiClient

BASE_DIR = Path("/home/david/tapo-lights")
ENV_PATH = BASE_DIR / ".env"
STATE_PATH = BASE_DIR / "state.json"
LOCK_PATH = BASE_DIR / "tapo.lock"
POLICE_PID_PATH = BASE_DIR / "police.pid"

RED_HUE_SAT = (0, 100)
BLUE_HUE_SAT = (240, 100)

# Comprehensive CSS & Named Colors mapping to Hex
CSS_COLORS: Dict[str, str] = {
    # Reds & Pinks
    "red": "#FF0000",
    "darkred": "#8B0000",
    "crimson": "#DC143C",
    "firebrick": "#B22222",
    "indianred": "#CD5C5C",
    "lightcoral": "#F08080",
    "salmon": "#FA8072",
    "darksalmon": "#E9967A",
    "lightsalmon": "#FFA07A",
    "pink": "#FFC0CB",
    "lightpink": "#FFB6C1",
    "hotpink": "#FF69B4",
    "deeppink": "#FF1493",
    "mediumvioletred": "#C71585",
    "palevioletred": "#DB7093",
    "rose": "#FF007F",
    "ruby": "#E0115F",
    "maroon": "#800000",
    # Oranges & Browns
    "orange": "#FFA500",
    "darkorange": "#FF8C00",
    "coral": "#FF7F50",
    "tomato": "#FF6347",
    "orangered": "#FF4500",
    "gold": "#FFD700",
    "amber": "#FFBF00",
    "peach": "#FFE5B4",
    "brown": "#A52A2A",
    "saddlebrown": "#8B4513",
    "sienna": "#A0522D",
    "chocolate": "#D2691E",
    "peru": "#CD853F",
    "sandybrown": "#F4A460",
    "burlywood": "#DEB887",
    "tan": "#D2B48C",
    "khaki": "#F0E68C",
    "bronze": "#CD7F32",
    "rust": "#B7410E",
    "copper": "#B87333",
    # Yellows
    "yellow": "#FFFF00",
    "lightyellow": "#FFFFE0",
    "lemon": "#FFF700",
    "lemonchiffon": "#FFFACD",
    "papayawhip": "#FFEFD5",
    "moccasin": "#FFE4B5",
    "banana": "#FFE135",
    "mustard": "#FFDB58",
    # Greens
    "green": "#00FF00",
    "lime": "#00FF00",
    "limegreen": "#32CD32",
    "chartreuse": "#7FFF00",
    "lawngreen": "#7CFC00",
    "darkgreen": "#006400",
    "forestgreen": "#228B22",
    "seagreen": "#2E8B57",
    "mediumseagreen": "#3CB371",
    "springgreen": "#00FF7F",
    "mint": "#3EB489",
    "mintgreen": "#98FF98",
    "olive": "#808000",
    "olivedrab": "#6B8E23",
    "darkolivegreen": "#556B2F",
    "emerald": "#50C878",
    "jade": "#00A86B",
    "sage": "#9DC183",
    "moss": "#8A9A5B",
    # Cyans & Teals
    "cyan": "#00FFFF",
    "aqua": "#00FFFF",
    "aquamarine": "#7FFFD4",
    "turquoise": "#40E0D0",
    "mediumturquoise": "#48D1CC",
    "darkturquoise": "#00CED1",
    "lightseagreen": "#20B2AA",
    "teal": "#008080",
    "darkcyan": "#008B8B",
    "cadetblue": "#5F9EA0",
    # Blues
    "blue": "#0000FF",
    "mediumblue": "#0000CD",
    "darkblue": "#00008B",
    "navy": "#000080",
    "navyblue": "#000080",
    "midnightblue": "#191970",
    "royalblue": "#4169E1",
    "cornflowerblue": "#6495ED",
    "dodgerblue": "#1E90FF",
    "deepskyblue": "#00BFFF",
    "lightskyblue": "#87CEFA",
    "skyblue": "#87CEEB",
    "lightblue": "#ADD8E6",
    "powderblue": "#B0E0E6",
    "steelblue": "#4682B4",
    "cobalt": "#0047AB",
    "azure": "#007FFF",
    "indigo": "#4B0082",
    "sapphire": "#0F52BA",
    # Purples & Violets
    "purple": "#800080",
    "violet": "#EE82EE",
    "darkviolet": "#9400D3",
    "darkmagenta": "#8B008B",
    "magenta": "#FF00FF",
    "fuchsia": "#FF00FF",
    "mediumpurple": "#9370DB",
    "blueviolet": "#8A2BE2",
    "plum": "#DDA0DD",
    "orchid": "#DA70D6",
    "mediumorchid": "#BA55D3",
    "darkorchid": "#9932CC",
    "thistle": "#D8BFD8",
    "lavender": "#E6E6FA",
    "lilac": "#C8A2C8",
    "amethyst": "#9966CC",
    "mauve": "#E0B0FF",
    "grape": "#6F2DA8",
    "wine": "#722F37",
    "burgundy": "#800020",
    "periwinkle": "#CCCCFF",
}

# Color Temperature Presets (Kelvin 2500K - 6500K)
COLOR_TEMPS: Dict[str, int] = {
    "candle": 2500,
    "candlelight": 2500,
    "cozy": 2500,
    "warm": 2700,
    "warmwhite": 2700,
    "soft": 3000,
    "softwhite": 3000,
    "incandescent": 3000,
    "reading": 4000,
    "neutral": 4000,
    "neutralwhite": 4000,
    "white": 4000,
    "daylight": 5500,
    "cool": 6500,
    "coolwhite": 6500,
    "cold": 6500,
    "focus": 6500,
}


def hex_to_hs(hex_str: str) -> Tuple[int, int]:
    """Convert hex color string (#RGB or #RRGGBB) to (hue, saturation) for Tapo L530."""
    hex_str = hex_str.lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join(c * 2 for c in hex_str)
    r, g, b = (int(hex_str[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    h, s, _ = colorsys.rgb_to_hsv(r, g, b)
    hue = int(round(h * 360)) % 360
    sat = max(1, min(100, int(round(s * 100))))
    return (hue, sat)


def hue_to_approx_name(hue: int, sat: int) -> str:
    """Return an approximate color name for display in status."""
    if sat < 20:
        return "White tint"
    if 345 <= hue or hue < 15:
        return "Red"
    if 15 <= hue < 45:
        return "Orange"
    if 45 <= hue < 70:
        return "Yellow"
    if 70 <= hue < 165:
        return "Green"
    if 165 <= hue < 195:
        return "Cyan"
    if 195 <= hue < 255:
        return "Blue"
    if 255 <= hue < 290:
        return "Indigo"
    if 290 <= hue < 325:
        return "Purple"
    if 325 <= hue < 345:
        return "Pink"
    return "RGB"


def parse_custom_lights_command(argv: List[str]) -> Optional[Dict[str, Any]]:
    """
    Parse flexible command line arguments into color, color temperature, or brightness.
    Examples supported:
      lights green 60      -> Green at 60%
      lights 60 green      -> Green at 60%
      lights green         -> Green at 100%
      lights #ff8800 80    -> Hex color at 80%
      lights warm 75       -> 2700K at 75%
      lights daylight 50   -> 5500K at 50%
      lights 4000k 70      -> 4000K at 70%
      lights 2700 80       -> 2700K at 80%
      lights 60            -> Brightness 60%
      lights 60%           -> Brightness 60%
    """
    if not argv:
        return None

    brightness: Optional[int] = None
    remaining_tokens: List[str] = []

    for arg in argv:
        clean = arg.strip()
        # Explicit percentage like '60%'
        if re.fullmatch(r"(\d+)%", clean):
            pct = int(clean.rstrip("%"))
            brightness = max(1, min(100, pct))
        # Plain digit 1-100 when another token exists or brightness already set
        elif clean.isdigit() and 1 <= int(clean) <= 100 and (len(argv) > 1 or brightness is not None):
            brightness = int(clean)
        else:
            remaining_tokens.append(clean)

    # Standalone brightness adjustment, e.g. 'lights 50' or 'lights 50%'
    if brightness is None and len(remaining_tokens) == 1 and remaining_tokens[0].isdigit():
        val = int(remaining_tokens[0])
        if 1 <= val <= 100:
            return {"type": "brightness_only", "brightness": val, "label": f"{val}%"}

    if brightness is not None and not remaining_tokens:
        return {"type": "brightness_only", "brightness": brightness, "label": f"{brightness}%"}

    if not remaining_tokens:
        return None

    color_str = " ".join(remaining_tokens).strip().lower()
    color_norm = re.sub(r"[\s\-_]+", "", color_str)

    # Check Kelvin with or without 'k', e.g. '4000k', '2700'
    m_k = re.fullmatch(r"(\d{4})k?", color_str)
    if m_k:
        k_val = int(m_k.group(1))
        if 2500 <= k_val <= 6500:
            br = brightness if brightness is not None else 100
            return {
                "type": "kelvin",
                "kelvin": k_val,
                "brightness": br,
                "label": f"{k_val}K",
            }

    # Check Color Temperature Presets
    if color_norm in COLOR_TEMPS:
        k_val = COLOR_TEMPS[color_norm]
        br = brightness if brightness is not None else 100
        return {
            "type": "kelvin",
            "kelvin": k_val,
            "brightness": br,
            "label": f"{color_str.title()} ({k_val}K)",
        }

    # Check Hex Color (#RRGGBB or #RGB or without #)
    if re.fullmatch(r"#?[0-9a-fA-F]{3}([0-9a-fA-F]{3})?", color_str):
        # Prevent numbers like '3000' being misinterpreted as hex if not prefixed by #
        if not color_str.startswith("#") and color_str.isdigit():
            pass
        else:
            hs = hex_to_hs(color_str)
            br = brightness if brightness is not None else 100
            hex_label = color_str if color_str.startswith("#") else f"#{color_str}"
            return {
                "type": "color",
                "hue_sat": hs,
                "brightness": br,
                "label": hex_label.upper(),
            }

    # Check Named CSS Colors
    if color_norm in CSS_COLORS:
        hex_code = CSS_COLORS[color_norm]
        hs = hex_to_hs(hex_code)
        br = brightness if brightness is not None else 100
        return {
            "type": "color",
            "hue_sat": hs,
            "brightness": br,
            "label": color_str.title(),
        }

    return None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tapo-lights")


_LOCK_DEPTH = 0


@contextmanager
def tapo_lock(timeout: float = 3.0) -> Iterator[None]:
    """Ensure CLI and background daemon do not collide on TPAP handshakes without deadlocking."""
    global _LOCK_DEPTH
    if _LOCK_DEPTH > 0:
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
        return

    LOCK_PATH.touch(exist_ok=True)
    with LOCK_PATH.open("r+") as f:
        acquired = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                time.sleep(0.05)
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
            if acquired:
                try:
                    fcntl.flock(f, fcntl.LOCK_UN)
                except Exception:
                    pass


_TAPO_DEVS: Dict[str, Any] = {}
_TAPO_CLIENTS: Dict[str, ApiClient] = {}
_TAPO_LOCKOUT_UNTIL: Dict[str, float] = {}
_WEB_ASYNC_LOCK: Optional[asyncio.Lock] = None


def get_web_lock() -> asyncio.Lock:
    global _WEB_ASYNC_LOCK
    if _WEB_ASYNC_LOCK is None:
        _WEB_ASYNC_LOCK = asyncio.Lock()
    return _WEB_ASYNC_LOCK


async def connect_bulb(client: Optional[ApiClient], ip: str, force_new: bool = False) -> Any:
    """Connect to an L530 bulb using a dedicated per-IP client and cached session."""
    now_m = time.monotonic()
    if now_m < _TAPO_LOCKOUT_UNTIL.get(ip, 0.0):
        rem = int(_TAPO_LOCKOUT_UNTIL[ip] - now_m)
        raise RuntimeError(f"TPAP auth cooldown ({rem}s remaining)")

    if not force_new and ip in _TAPO_DEVS:
        return _TAPO_DEVS[ip]

    username = os.environ.get("TAPO_USERNAME", "")
    password = os.environ.get("TAPO_PASSWORD", "")

    for attempt in range(2):
        try:
            ip_client = ApiClient(username, password) if (username and password) else client
            _TAPO_CLIENTS[ip] = ip_client
            dev = await ip_client.l530(ip)
            _TAPO_DEVS[ip] = dev
            _TAPO_LOCKOUT_UNTIL.pop(ip, None)
            return dev
        except Exception as e:
            _TAPO_DEVS.pop(ip, None)
            _TAPO_CLIENTS.pop(ip, None)
            if "TPAP_AUTH_ATTEMPTS_LIMIT" in str(e):
                _TAPO_LOCKOUT_UNTIL[ip] = time.monotonic() + 60.0
                raise
            if attempt == 0:
                await asyncio.sleep(0.8)
            else:
                raise


async def run_tapo_with_retry(client: ApiClient, ip: str, coro_factory: Any) -> Any:
    """Run an async operation on a cached Tapo bulb; reconnect once if the session expired."""
    for attempt in range(2):
        dev = await connect_bulb(client, ip, force_new=(attempt > 0))
        try:
            return await coro_factory(dev)
        except Exception as e:
            _TAPO_DEVS.pop(ip, None)
            if "TPAP_AUTH_ATTEMPTS_LIMIT" in str(e):
                _TAPO_LOCKOUT_UNTIL[ip] = time.monotonic() + 60.0
                raise
            if attempt == 0:
                await asyncio.sleep(0.3)
                continue
            raise


def lerp(a: float, b: float, t: float) -> int:
    t = max(0.0, min(1.0, t))
    return int(round(a + (b - a) * t))


def get_circadian_target(now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """
    Calculate the desired circadian state for the given local datetime.
    Returns dict with keys: phase, on, color_temp, brightness.
    """
    if now is None:
        now = dt.datetime.now().astimezone()

    minutes = now.hour * 60 + now.minute + now.second / 60.0

    # 08:00 (480) -> 16:00 (960): Sleep (OFF)
    if 8 * 60 <= minutes < 16 * 60:
        return {
            "phase": "Sleep (08:00–16:00)",
            "on": False,
            "color_temp": 2500,
            "brightness": 10,
        }

    # 16:00 (960) -> 17:00 (1020): Wake-Up / Dawn
    if 16 * 60 <= minutes < 17 * 60:
        t = (minutes - 16 * 60) / 60.0
        return {
            "phase": "Wake-Up / Dawn (16:00–17:00)",
            "on": True,
            "color_temp": lerp(3000, 6000, t),
            "brightness": lerp(30, 100, t),
        }

    # 17:00 (1020) -> 22:00 (1320): Afternoon / Pre-Shift Daylight
    if 17 * 60 <= minutes < 22 * 60:
        return {
            "phase": "Pre-Shift Daylight (17:00–22:00)",
            "on": True,
            "color_temp": 6000,
            "brightness": 100,
        }

    # 22:00 (1320) -> 01:00 (60): Peak Work Shift
    if minutes >= 22 * 60 or minutes < 1 * 60:
        return {
            "phase": "Peak Work Shift (22:00–01:00)",
            "on": True,
            "color_temp": 6500,
            "brightness": 100,
        }

    # 01:00 (60) -> 02:00 (120): Mid-Shift Transition
    if 1 * 60 <= minutes < 2 * 60:
        t = (minutes - 1 * 60) / 60.0
        return {
            "phase": "Mid-Shift Focus (01:00–02:00)",
            "on": True,
            "color_temp": lerp(6500, 5500, t),
            "brightness": 100,
        }

    # 02:00 (120) -> 05:00 (300): Late Work Shift
    if 2 * 60 <= minutes < 5 * 60:
        t = (minutes - 2 * 60) / 180.0
        return {
            "phase": "Late Work Shift (02:00–05:00)",
            "on": True,
            "color_temp": lerp(5500, 3500, t),
            "brightness": lerp(100, 75, t),
        }

    # 05:00 (300) -> 06:00 (360): End of Shift
    if 5 * 60 <= minutes < 6 * 60:
        t = (minutes - 5 * 60) / 60.0
        return {
            "phase": "End of Shift (05:00–06:00)",
            "on": True,
            "color_temp": lerp(3500, 3000, t),
            "brightness": lerp(75, 50, t),
        }

    # 06:00 (360) -> 08:00 (480): Post-Work Wind-Down
    t = (minutes - 6 * 60) / 120.0
    return {
        "phase": "Post-Work Wind-Down (06:00–08:00)",
        "on": True,
        "color_temp": lerp(2700, 2500, t),
        "brightness": lerp(35, 10, t),
    }


DEFAULT_WAKEUP: Dict[str, Any] = {
    "enabled": True,
    "time": "16:00",
    "duration": 30,
    "target_brightness": 80,
    "target_temp": 6000,
    "dismissed_date": None,
    "last_completed_date": None,
}


def get_wakeup_config(state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if state is None:
        state = load_state()
    cfg = dict(DEFAULT_WAKEUP)
    if "wakeup" in state and isinstance(state["wakeup"], dict):
        cfg.update(state["wakeup"])
    return cfg


def save_wakeup_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    with tapo_lock():
        state = load_state()
        cfg = get_wakeup_config(state)
        cfg.update(updates)
        if updates.get("enabled") is True or "time" in updates or "duration" in updates:
            cfg["dismissed_date"] = None
        state["wakeup"] = cfg
        save_state(state)
        return cfg


def get_bedroom_tapo_ips() -> List[str]:
    rooms = load_rooms()
    bed_cfg = rooms.get("bedroom", {})
    ips = [
        ip
        for ip in bed_cfg.get("ips", [])
        if bed_cfg.get("ip_providers", {}).get(ip, bed_cfg.get("provider", "tapo")) == "tapo"
    ]
    if not ips and os.environ.get("TAPO_IPS_BEDROOM", "").strip():
        ips = [ip.strip() for ip in os.environ["TAPO_IPS_BEDROOM"].split(",") if ip.strip()]
    return ips


def get_wakeup_window(now: dt.datetime, cfg: Dict[str, Any]) -> Tuple[dt.datetime, dt.datetime]:
    """Return (start_dt, wake_dt) for the current or upcoming wake-up occurrence."""
    time_str = cfg.get("time", "16:00")
    try:
        h, m = map(int, str(time_str).strip().split(":"))
    except Exception:
        h, m = 16, 0
    duration = int(cfg.get("duration", 30))

    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
    start = candidate - dt.timedelta(minutes=duration)
    end = candidate

    if start <= now <= end + dt.timedelta(minutes=30):
        return start, end

    if now > end + dt.timedelta(minutes=30):
        candidate += dt.timedelta(days=1)
        start = candidate - dt.timedelta(minutes=duration)
        end = candidate

    return start, end


def calculate_wakeup_progression(
    now: dt.datetime, cfg: Dict[str, Any]
) -> Tuple[bool, float, int, int, str]:
    """
    Biologically calibrated sunrise curve:
      - Phase 1 (0-33%):   Deep Dawn (1% to 8%, 2500K to 2700K candle twilight)
      - Phase 2 (33-66%):  Golden Morning (8% to 35%, 2700K to 4000K soft white)
      - Phase 3 (66-100%): Alert Daylight (35% to target_br, 4000K to target_k morning daylight)
    Returns: (is_active, progress, kelvin, brightness, description)
    """
    if not cfg.get("enabled", True):
        return False, 0.0, 2500, 1, "Wake-Up routine is disabled"

    start_dt, wake_dt = get_wakeup_window(now, cfg)
    today_str = now.strftime("%Y-%m-%d")

    if cfg.get("dismissed_date") == today_str and now < wake_dt + dt.timedelta(minutes=30):
        return False, 0.0, 2500, 1, "Wake-Up sunrise dismissed for today by user"

    if start_dt <= now <= wake_dt:
        total_sec = max(1.0, (wake_dt - start_dt).total_seconds())
        elapsed_sec = max(0.0, (now - start_dt).total_seconds())
        progress = max(0.0, min(1.0, elapsed_sec / total_sec))

        target_br = int(cfg.get("target_brightness", 80))
        target_k = int(cfg.get("target_temp", 6000))

        if progress < 0.33:
            frac = progress / 0.33
            br = lerp(1, 8, frac)
            k = lerp(2500, 2700, frac)
            phase_name = "Deep Dawn Twilight"
        elif progress < 0.66:
            frac = (progress - 0.33) / 0.33
            br = lerp(8, 35, frac)
            k = lerp(2700, 4000, frac)
            phase_name = "Golden Morning"
        else:
            frac = (progress - 0.66) / 0.34
            br = lerp(35, target_br, frac)
            k = lerp(4000, target_k, frac)
            phase_name = "Alert Daylight"

        desc = f"Wake-Up Sunrise [{phase_name}] ({int(progress * 100)}%): {k}K @ {br}% (target {wake_dt.strftime('%H:%M')})"
        return True, progress, k, br, desc

    mins_to_start = int((start_dt - now).total_seconds() / 60)
    if 0 < mins_to_start <= 720:
        desc = f"Scheduled: Sunrise starts in {mins_to_start}m ({start_dt.strftime('%H:%M')} -> {wake_dt.strftime('%H:%M')})"
    else:
        desc = f"Scheduled: Sunrise at {start_dt.strftime('%H:%M')} for {wake_dt.strftime('%H:%M')} wake-up"

    return False, 0.0, 2500, 1, desc


async def apply_wakeup_step(kelvin: int, brightness: int, progress: float) -> None:
    ips = get_bedroom_tapo_ips()
    if not ips:
        return
    username = os.environ.get("TAPO_USERNAME", "")
    password = os.environ.get("TAPO_PASSWORD", "")
    client = ApiClient(username, password) if (username and password) else None

    pct_int = int(round(progress * 100))
    desc = f"Wake-Up Sunrise ({pct_int}%: {kelvin}K @ {brightness}%)"

    async def _send(ip: str):
        try:
            await run_tapo_with_retry(
                client, ip, lambda d: d.set().on().color_temperature(kelvin).brightness(brightness).send(d)
            )
        except Exception as e:
            logger.warning("Wake-up step error on %s: %s", ip, e)

    await asyncio.gather(*(_send(ip) for ip in ips))

    with tapo_lock():
        st = load_state()
        for ip in ips:
            b = st.setdefault("bulbs", {}).setdefault(ip, {})
            b["manual_override"] = False
            b["override_reason"] = desc
            b["last_applied"] = {
                "on": True,
                "color_temp": kelvin,
                "brightness": brightness,
                "updated_at": dt.datetime.now().astimezone().isoformat(),
            }
        save_state(st)


async def check_wakeup_routine(force: bool = False) -> None:
    now = dt.datetime.now().astimezone()
    cfg = get_wakeup_config()
    is_active, progress, k, br, desc = calculate_wakeup_progression(now, cfg)

    today_str = now.strftime("%Y-%m-%d")
    start_dt, wake_dt = get_wakeup_window(now, cfg)

    if now >= wake_dt and now < wake_dt + dt.timedelta(minutes=30):
        if cfg.get("last_completed_date") != today_str:
            cfg["last_completed_date"] = today_str
            with tapo_lock():
                st = load_state()
                st["wakeup"] = cfg
                save_state(st)
            logger.info("Wake-up sunrise routine reached target wake time (%s).", wake_dt.strftime("%H:%M"))

    if not is_active:
        return

    ips = get_bedroom_tapo_ips()
    if not ips:
        return

    with tapo_lock():
        st = load_state()
        for ip in ips:
            bstate = st.get("bulbs", {}).get(ip, {})
            last_app = bstate.get("last_applied", {})
            if bstate.get("manual_override") and last_app.get("on") is False:
                ts_str = last_app.get("updated_at") or bstate.get("override_at")
                dismissed_during_window = False
                if ts_str:
                    try:
                        override_dt = dt.datetime.fromisoformat(ts_str)
                        if override_dt >= start_dt:
                            dismissed_during_window = True
                    except Exception:
                        pass
                if dismissed_during_window:
                    cfg["dismissed_date"] = today_str
                    st["wakeup"] = cfg
                    save_state(st)
                    logger.info("Wake-up routine dismissed for today (bedroom lights turned off by user during sunrise).")
                    return

        last_applied = None
        for ip in ips:
            last = st.get("bulbs", {}).get(ip, {}).get("last_applied")
            if last and last.get("on"):
                last_applied = last
                break

    if last_applied and not force:
        last_br = last_applied.get("brightness", 0)
        last_ct = last_applied.get("color_temp", 0)
        if abs(br - last_br) < 2 and abs(k - last_ct) < 75:
            return

    logger.info("Wake-Up Progress: %s", desc)
    await apply_wakeup_step(k, br, progress)


async def preview_wakeup_routine(steps: int = 6, step_delay: float = 2.5) -> None:
    """Run an accelerated 15-second simulation of the wake-up sunrise progression on bedroom Tapo lights."""
    cfg = get_wakeup_config()
    target_br = int(cfg.get("target_brightness", 80))
    target_k = int(cfg.get("target_temp", 6000))

    keyframes = [
        (0.0, 1, 2500, "1% Candle Twilight"),
        (0.2, 6, 2600, "6% Early Dawn"),
        (0.4, 18, 3000, "18% Golden Sunrise"),
        (0.6, 38, 4000, "38% Morning White"),
        (0.8, 62, 5000, "62% Daylight"),
        (1.0, target_br, target_k, f"{target_br}% Crisp {target_k}K Alert Daylight"),
    ]

    logger.info("Starting Wake-Up Sunrise Preview (%d steps, %.1fs)...", len(keyframes), len(keyframes) * step_delay)
    for progress, br, k, label in keyframes:
        logger.info("Sunrise Preview: %s", label)
        await apply_wakeup_step(k, br, progress)
        await asyncio.sleep(step_delay)


def handle_wakeup_cli(args: List[str]) -> None:
    now = dt.datetime.now().astimezone()
    cfg = get_wakeup_config()

    if not args or args[0] in ("status", "info"):
        is_active, progress, k, br, desc = calculate_wakeup_progression(now, cfg)
        start_dt, wake_dt = get_wakeup_window(now, cfg)
        status_word = "ENABLED" if cfg.get("enabled", True) else "DISABLED"
        print("Wake-Up Sunrise Routine (Bedroom Tapo Bulbs):")
        print(f"  Status        : {status_word}")
        print(f"  Target Wake-Up: {cfg.get('time', '16:00')}")
        print(f"  Dawn Duration : {cfg.get('duration', 30)} minutes (starts at {start_dt.strftime('%H:%M')})")
        print(f"  Target Light  : {cfg.get('target_temp', 6000)}K @ {cfg.get('target_brightness', 80)}% brightness")
        print(f"  Current State : {desc}")
        print("-" * 65)
        print("Commands:")
        print("  lights bedroom wakeup on / off       Enable or disable routine")
        print("  lights bedroom wakeup <HH:MM>        Set wake-up time (e.g. lights bedroom wakeup 16:00)")
        print("  lights bedroom wakeup <15-60>m       Set dawn duration (e.g. lights bedroom wakeup 30m)")
        print("  lights bedroom wakeup test           Run 15-second live sunrise simulation")
        return

    sub = args[0].lower().strip()
    if sub in ("on", "enable", "start"):
        save_wakeup_config({"enabled": True})
        cfg = get_wakeup_config()
        start_dt, wake_dt = get_wakeup_window(now, cfg)
        print(f"Wake-up sunrise routine ENABLED. Next sunrise starts at {start_dt.strftime('%H:%M')} for {wake_dt.strftime('%H:%M')} wake-up.")
        return

    if sub in ("off", "disable", "stop"):
        save_wakeup_config({"enabled": False})
        print("Wake-up sunrise routine DISABLED.")
        return

    if sub in ("test", "preview", "demo"):
        print("Running 15-second live Sunrise Dawn Preview on bedroom Tapo lights...")
        asyncio.run(preview_wakeup_routine())
        print("Sunrise Preview complete! Lights left at full wake-up daylight.")
        return

    if re.fullmatch(r"\d{1,2}:\d{2}", sub):
        h, m = map(int, sub.split(":"))
        t_str = f"{h:02d}:{m:02d}"
        save_wakeup_config({"time": t_str, "enabled": True})
        cfg = get_wakeup_config()
        start_dt, wake_dt = get_wakeup_window(now, cfg)
        print(f"Wake-up time set to {t_str} (ENABLED). Sunrise begins at {start_dt.strftime('%H:%M')} (30 min duration).")
        return

    if re.fullmatch(r"\d+m?", sub):
        mins = int(sub.rstrip("m"))
        mins = max(5, min(120, mins))
        save_wakeup_config({"duration": mins})
        cfg = get_wakeup_config()
        start_dt, wake_dt = get_wakeup_window(now, cfg)
        print(f"Dawn duration set to {mins} minutes. Sunrise will start at {start_dt.strftime('%H:%M')} for {wake_dt.strftime('%H:%M')} wake-up.")
        return

    print(f"Unrecognized wakeup option '{' '.join(args)}'. Run 'lights bedroom wakeup' for help.", file=sys.stderr)


def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"paused": False, "last_phase": None, "bulbs": {}}


def save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)


def is_manually_modified(info: Any, recorded: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
    """
    Check if a bulb's actual state differs from what the service last applied,
    indicating the user changed it via the Tapo mobile app.
    """
    if not recorded:
        return False, ""

    expected_on = recorded.get("on")
    if expected_on is not None and info.device_on != expected_on:
        return True, f"power changed (app={info.device_on}, schedule={expected_on})"

    if info.device_on:
        # In Tapo L530, when RGB/color mode is selected in the app, color_temp becomes 0
        if info.color_temp == 0:
            return True, f"RGB color active (hue={info.hue}, sat={info.saturation})"
        expected_ct = recorded.get("color_temp")
        if expected_ct and abs(info.color_temp - expected_ct) > 100:
            return True, f"color_temp changed (app={info.color_temp}K, schedule={expected_ct}K)"
        expected_br = recorded.get("brightness")
        if expected_br and abs(info.brightness - expected_br) > 5:
            return True, f"brightness changed (app={info.brightness}%, schedule={expected_br}%)"

    return False, ""


def load_rooms() -> Dict[str, Dict[str, Any]]:
    """
    Load room configuration from .env:
      - TAPO_IPS_<ROOM>=ip1,ip2  -> provider: 'tapo'
      - TUYA_IPS_<ROOM>=ip1,ip2  -> provider: 'tuya'
      - CYNC_IPS_<ROOM>=ip1,ip2  -> provider: 'cync'
      - ROOM_DEFAULT_<ROOM>=...  -> default command when running 'lights <room>' (e.g. '5500k 50')
      - CIRCADIAN_ROOMS=...      -> comma-separated rooms managed by circadian daemon
    """
    load_dotenv(ENV_PATH, override=True)
    rooms: Dict[str, Dict[str, Any]] = {}
    circadian_set = {
        r.strip().lower().replace("_", "-")
        for r in os.environ.get("CIRCADIAN_ROOMS", "office").split(",")
        if r.strip()
    }

    for prefix, provider in (("TAPO_IPS_", "tapo"), ("TUYA_IPS_", "tuya"), ("CYNC_IPS_", "cync")):
        for key, val in sorted(os.environ.items()):
            if key.startswith(prefix) and val.strip():
                room_name = key[len(prefix) :].strip().lower().replace("_", "-")
                ips = [ip.strip() for ip in val.split(",") if ip.strip()]
                if room_name and ips:
                    env_suffix = key[len(prefix) :].strip().upper()
                    default_cmd = os.environ.get(f"ROOM_DEFAULT_{env_suffix}", "").strip() or None
                    if room_name == "bedroom" and not default_cmd:
                        default_cmd = "5500k 50"
                    if room_name not in rooms:
                        rooms[room_name] = {
                            "provider": provider,
                            "ips": [],
                            "ip_providers": {},
                            "default": default_cmd,
                            "circadian": room_name in circadian_set,
                        }
                    else:
                        if rooms[room_name]["provider"] != provider:
                            rooms[room_name]["provider"] = f"{rooms[room_name]['provider']}+{provider}"
                    for ip in ips:
                        if ip not in rooms[room_name]["ips"]:
                            rooms[room_name]["ips"].append(ip)
                        rooms[room_name]["ip_providers"][ip] = provider

    if not rooms and os.environ.get("TAPO_IPS", "").strip():
        ips = [ip.strip() for ip in os.environ["TAPO_IPS"].split(",") if ip.strip()]
        if ips:
            rooms["bedroom"] = {
                "provider": "tapo",
                "ips": ips,
                "ip_providers": {ip: "tapo" for ip in ips},
                "default": "5500k 50",
                "circadian": "bedroom" in circadian_set,
            }

    return rooms


def _send_cync_sync(ip: str, action: str, brightness: Optional[int] = None) -> None:
    import socket
    import ssl
    from pycync.tcp import packet_builder

    ip_key = ip.replace(".", "_")
    dev_id = int(os.environ.get(f"CYNC_ID_{ip_key}", "282949266").strip())
    mesh_id = int(os.environ.get(f"CYNC_MESH_{ip_key}", "1").strip())
    token_path = BASE_DIR / "cync_token.json"
    if not token_path.exists():
        raise RuntimeError("Missing cync_token.json for GE Cync bulb")

    td = json.loads(token_path.read_text())
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with socket.create_connection(("cm-sec.gelighting.com", 23779), timeout=4.0) as raw_sock:
        raw_sock.settimeout(4.0)
        with ctx.wrap_socket(raw_sock, server_hostname="cm-sec.gelighting.com") as ssock:
            ssock.settimeout(4.0)
            ssock.sendall(packet_builder.build_login_request_packet(td["authorize"], td["user_id"]))
            ssock.recv(32)
            if action == "off":
                ssock.sendall(packet_builder.build_power_state_request_packet(dev_id, mesh_id, False))
            elif action == "on" and brightness is None:
                ssock.sendall(packet_builder.build_power_state_request_packet(dev_id, mesh_id, True))
            else:
                br = max(1, min(100, brightness if brightness is not None else 50))
                ssock.sendall(packet_builder.build_power_state_request_packet(dev_id, mesh_id, True))
                ssock.sendall(packet_builder.build_brightness_request_packet(dev_id, mesh_id, br))
            time.sleep(0.1)


async def send_cync_command(ip: str, action: str, brightness: Optional[int] = None) -> None:
    """Send a fast TLS command to a GE Cync Wi-Fi bulb using cached token and packet_builder."""
    await asyncio.to_thread(_send_cync_sync, ip, action, brightness)


def resolve_target_rooms(room: Optional[str] = None, circadian_only: bool = False) -> Dict[str, Dict[str, Any]]:
    """Return a dict of {room_name: room_cfg} for a specific room or all rooms."""
    rooms = load_rooms()
    if not rooms:
        raise RuntimeError("No bulb IPs configured in .env (expected TAPO_IPS_<ROOM> or TUYA_IPS_<ROOM>).")

    if room is None or room.lower() == "all":
        if circadian_only:
            return {k: v for k, v in rooms.items() if v.get("circadian")}
        return rooms

    norm = room.strip().lower().replace("_", "-")
    if norm in ("ge", "lamp") and "bedside" in rooms:
        norm = "bedside"
    if norm in rooms:
        return {norm: rooms[norm]}

    available = ", ".join(rooms.keys())
    raise ValueError(f"Unknown room '{room}'. Configured rooms: {available} (or 'all').")


def get_tuya_bulb(ip: str) -> Any:
    """Instantiate a tinytuya.BulbDevice for the given IP using .env credentials."""
    import tinytuya

    ip_key = ip.replace(".", "_")
    dev_id = os.environ.get(f"TUYA_ID_{ip_key}", "").strip()
    local_key = os.environ.get(f"TUYA_KEY_{ip_key}", "").strip()
    version = float(os.environ.get(f"TUYA_VER_{ip_key}", "3.5").strip() or "3.5")

    if not dev_id:
        raise RuntimeError(f"Missing TUYA_ID_{ip_key} in .env for Tuya bulb {ip}")
    if not local_key:
        raise RuntimeError(
            f"Missing TUYA_KEY_{ip_key} (16-char Tuya Local Key) in .env for Tuya bulb {ip} (ID: {dev_id})"
        )

    dev = tinytuya.BulbDevice(dev_id, ip, local_key)
    dev.set_version(version)
    dev.set_socketTimeout(3)
    dev.set_socketRetryLimit(2)
    return dev


def kelvin_to_tuya_pct(kelvin: int) -> int:
    """Convert 2500K-6500K to 0-100% for Tuya bulbs."""
    pct = int(round((kelvin - 2500) / (6500 - 2500) * 100))
    return max(0, min(100, pct))


def tuya_pct_to_kelvin(pct: int) -> int:
    """Convert 0-100% from Tuya bulb to 2500K-6500K."""
    return int(round(2500 + (pct / 100.0) * (6500 - 2500)))


def get_police_pid_path(room: str) -> Path:
    return BASE_DIR / f"police_{room}.pid"


def get_running_police_pids(room: Optional[str] = None) -> Dict[str, int]:
    """Return {room_name: pid} for any active police mode processes."""
    rooms = load_rooms()
    target_names = list(rooms.keys()) if (room is None or room == "all") else [room]
    active: Dict[str, int] = {}

    for r in target_names:
        pid_path = get_police_pid_path(r)
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text().strip())
                os.kill(pid, 0)
                active[r] = pid
            except Exception:
                pid_path.unlink(missing_ok=True)

    # Also check legacy police.pid
    if (room is None or room in ("all", "bedroom")) and POLICE_PID_PATH.exists():
        try:
            pid = int(POLICE_PID_PATH.read_text().strip())
            os.kill(pid, 0)
            active.setdefault("bedroom", pid)
        except Exception:
            POLICE_PID_PATH.unlink(missing_ok=True)

    return active


def _kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            try:
                os.kill(pid, 0)
                time.sleep(0.05)
            except ProcessLookupError:
                break
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def stop_police(room: Optional[str] = None, quiet: bool = False) -> bool:
    active = get_running_police_pids(room)
    if not active:
        return False

    for r, pid in active.items():
        _kill_pid(pid)
        get_police_pid_path(r).unlink(missing_ok=True)
        if r == "bedroom":
            POLICE_PID_PATH.unlink(missing_ok=True)
        if not quiet:
            print(f"Stopped police mode for {r} (PID {pid})")
    return True


async def apply_target_to_bulb(
    client: ApiClient,
    ip: str,
    target: Dict[str, Any],
    state: Dict[str, Any],
    force: bool = False,
    room: str = "office",
    provider: str = "tapo",
) -> None:
    bulb_state = state.setdefault("bulbs", {}).setdefault(ip, {})

    if not force and bulb_state.get("manual_override"):
        return

    if provider == "tuya":
        dev = get_tuya_bulb(ip)
        st = await asyncio.to_thread(dev.state)
        if isinstance(st, dict) and "Error" in st:
            raise RuntimeError(st["Error"])
        device_on = bool(st.get("is_on"))
        mode = st.get("mode", "white")
        br_pct = int(round(await asyncio.to_thread(dev.get_brightness_percentage) or 0)) if device_on else 0
        ct_pct = int(round(await asyncio.to_thread(dev.get_colourtemp_percentage) or 0)) if device_on else 0
        cur_ct = 0 if mode in ("colour", "music") else tuya_pct_to_kelvin(ct_pct)
        nickname = f"Tuya ({ip})"

        if force:
            bulb_state["manual_override"] = False

        if not force:
            recorded = bulb_state.get("last_applied")
            if recorded:
                expected_on = recorded.get("on")
                reason = ""
                if expected_on is not None and device_on != expected_on:
                    reason = f"power changed (app={device_on}, schedule={expected_on})"
                elif device_on:
                    if mode in ("colour", "music"):
                        reason = "RGB color active"
                    else:
                        expected_ct = recorded.get("color_temp")
                        expected_br = recorded.get("brightness")
                        if expected_ct and abs(cur_ct - expected_ct) > 200:
                            reason = f"color_temp changed (app={cur_ct}K, schedule={expected_ct}K)"
                        elif expected_br and abs(br_pct - expected_br) > 5:
                            reason = f"brightness changed (app={br_pct}%, schedule={expected_br}%)"
                if reason:
                    bulb_state["manual_override"] = True
                    bulb_state["override_reason"] = reason
                    bulb_state["override_at"] = dt.datetime.now().astimezone().isoformat()
                    logger.info(
                        "Manual app change detected on [%s] %s: %s. Pausing auto-adjust until 'lights %s' is run.",
                        room,
                        nickname,
                        reason,
                        room,
                    )
                    return

        if not target["on"]:
            if device_on or force:
                await asyncio.to_thread(dev.turn_off)
                logger.info("Turned OFF [%s] %s [%s]", room, nickname, target["phase"])
            bulb_state["last_applied"] = {
                "on": False,
                "color_temp": target["color_temp"],
                "brightness": target["brightness"],
                "updated_at": dt.datetime.now().astimezone().isoformat(),
            }
        else:
            ct = target["color_temp"]
            br = target["brightness"]
            needs_update = (
                force
                or not device_on
                or mode != "white"
                or abs(cur_ct - ct) > 150
                or abs(br_pct - br) > 5
            )
            if needs_update:
                t_pct = kelvin_to_tuya_pct(ct)
                await asyncio.to_thread(dev.set_white_percentage, br, t_pct)
                logger.info("Set [%s] %s -> %dK @ %d%% [%s]", room, nickname, ct, br, target["phase"])
            bulb_state["last_applied"] = {
                "on": True,
                "color_temp": ct,
                "brightness": br,
                "updated_at": dt.datetime.now().astimezone().isoformat(),
            }
        return

    dev = await connect_bulb(client, ip)
    info = await dev.get_device_info()
    nickname = info.nickname or ip

    if force:
        bulb_state["manual_override"] = False

    if not force:
        modified, reason = is_manually_modified(info, bulb_state.get("last_applied"))
        if modified:
            bulb_state["manual_override"] = True
            bulb_state["override_reason"] = reason
            bulb_state["override_at"] = dt.datetime.now().astimezone().isoformat()
            logger.info(
                "Manual app change detected on [%s] %s (%s): %s. Pausing auto-adjust for this bulb until 'lights %s' is run.",
                room,
                nickname,
                ip,
                reason,
                room,
            )
            return

    if not target["on"]:
        if info.device_on or force:
            await dev.off()
            logger.info("Turned OFF [%s] %s (%s) [%s]", room, nickname, ip, target["phase"])
        bulb_state["last_applied"] = {
            "on": False,
            "color_temp": target["color_temp"],
            "brightness": target["brightness"],
            "updated_at": dt.datetime.now().astimezone().isoformat(),
        }
    else:
        ct = target["color_temp"]
        br = target["brightness"]
        needs_update = (
            force
            or not info.device_on
            or info.color_temp != ct
            or info.brightness != br
        )
        if needs_update:
            await dev.set().color_temperature(ct).brightness(br).send(dev)
            logger.info(
                "Set [%s] %s (%s) -> %dK @ %d%% [%s]",
                room,
                nickname,
                ip,
                ct,
                br,
                target["phase"],
            )
        bulb_state["last_applied"] = {
            "on": True,
            "color_temp": ct,
            "brightness": br,
            "updated_at": dt.datetime.now().astimezone().isoformat(),
        }


async def sync_once(force: bool = False, room: Optional[str] = None) -> None:
    target_rooms = resolve_target_rooms(room, circadian_only=(not force and room is None))
    username = os.environ.get("TAPO_USERNAME", "")
    password = os.environ.get("TAPO_PASSWORD", "")

    if force:
        stop_police(room=room, quiet=False)

    with tapo_lock():
        state = load_state()
        if force:
            state["paused"] = False

        target = get_circadian_target()

        # Clear manual overrides automatically when entering Sleep (08:00) or Wake-Up (16:00)
        prev_phase = state.get("last_phase")
        if prev_phase and prev_phase != target["phase"]:
            if target["phase"].startswith("Sleep") or target["phase"].startswith("Wake-Up"):
                logger.info(
                    "Major phase boundary (%s -> %s): clearing manual overrides.",
                    prev_phase,
                    target["phase"],
                )
                stop_police(room=None, quiet=True)
                for b in state.get("bulbs", {}).values():
                    b["manual_override"] = False
                state["paused"] = False

        state["last_phase"] = target["phase"]

        if state.get("paused") and not force:
            save_state(state)
            return

        client = ApiClient(username, password) if (username and password) else None
        for r_name, r_cfg in target_rooms.items():
            if not force and not r_cfg.get("circadian"):
                continue
            provider = r_cfg.get("provider", "tapo")
            for ip in r_cfg.get("ips", []):
                try:
                    await apply_target_to_bulb(
                        client, ip, target, state, force=force, room=r_name, provider=provider
                    )
                except Exception as e:
                    logger.error("Error communicating with [%s] %s: %s", r_name, ip, e)

        save_state(state)


async def show_status(room: Optional[str] = None) -> None:
    target_rooms = resolve_target_rooms(room)
    username = os.environ.get("TAPO_USERNAME", "")
    password = os.environ.get("TAPO_PASSWORD", "")

    with tapo_lock():
        now = dt.datetime.now().astimezone()
        target = get_circadian_target(now)
        state = load_state()
        police_pids = get_running_police_pids(room)

        print(f"Local Time      : {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"Current Phase   : {target['phase']}")
        if target["on"]:
            print(f"Schedule Target : ON  | {target['color_temp']}K | {target['brightness']}% brightness")
        else:
            print("Schedule Target : OFF (Sleep window)")
        if police_pids:
            p_str = ", ".join(f"{r} (PID {pid})" for r, pid in police_pids.items())
            mode_summary = f"POLICE MODE ACTIVE [{p_str}]"
        elif state.get("paused"):
            mode_summary = "PAUSED"
        else:
            circ_rooms = [r for r, cfg in load_rooms().items() if cfg.get("circadian")]
            mode_summary = (
                f"ACTIVE (circadian rooms: {', '.join(circ_rooms)})"
                if circ_rooms
                else "MANUAL / DEFAULT ONLY (no circadian rooms)"
            )
        print(f"Service Mode    : {mode_summary}")
        print("-" * 78)

        client = ApiClient(username, password) if (username and password) else None
        for r_name, r_cfg in target_rooms.items():
            is_circ = r_cfg.get("circadian", False)
            default_desc = r_cfg.get("default")
            for ip in r_cfg.get("ips", []):
                provider = r_cfg.get("ip_providers", {}).get(ip, r_cfg.get("provider", "tapo"))
                room_tag = f"[{r_name}]"
                try:
                    bstate = state.get("bulbs", {}).get(ip, {})
                    override = bstate.get("manual_override", False)
                    if not is_circ:
                        mode_str = f"DEFAULT ({default_desc})" if not override else f"SET ({bstate.get('override_reason', 'manual')})"
                    else:
                        mode_str = (
                            f"MANUAL OVERRIDE ({bstate.get('override_reason', 'app')})"
                            if override
                            else "ON SCHEDULE"
                        )

                    if provider == "cync":
                        ip_key = ip.replace(".", "_")
                        nickname = os.environ.get(f"CYNC_NAME_{ip_key}", "Bedside Lamp (GE)")
                        last = bstate.get("last_applied", {})
                        cync_on = last.get("on", True)
                        cync_br = last.get("brightness", 50)
                        light_str = "OFF" if not cync_on else f"ON  | Dimmable White | {cync_br}%"
                        print(f"{room_tag:<10} {nickname:<15} ({ip}) : {light_str:<36} [{mode_str}]")
                    elif provider == "tuya":
                        dev = get_tuya_bulb(ip)
                        st = await asyncio.to_thread(dev.state)
                        if isinstance(st, dict) and "Error" in st:
                            raise RuntimeError(st["Error"])
                        device_on = bool(st.get("is_on"))
                        if not device_on:
                            light_str = "OFF"
                        elif st.get("mode") in ("colour", "music"):
                            h, s, v = await asyncio.to_thread(dev.colour_hsv)
                            hue = int(round(h * 360)) % 360
                            sat = int(round(s * 100))
                            br = int(round(v * 100))
                            cname = hue_to_approx_name(hue, sat)
                            light_str = f"ON  | {cname} (hue={hue}, sat={sat}) | {br}%"
                        else:
                            br_pct = int(round(await asyncio.to_thread(dev.get_brightness_percentage) or 0))
                            ct_pct = int(round(await asyncio.to_thread(dev.get_colourtemp_percentage) or 0))
                            ct_k = tuya_pct_to_kelvin(ct_pct)
                            light_str = f"ON  | {ct_k}K | {br_pct}%"
                        print(f"{room_tag:<10} {'Tuya Bulb':<15} ({ip}) : {light_str:<36} [{mode_str}]")
                    else:
                        dev = await connect_bulb(client, ip)
                        info = await dev.get_device_info()
                        if not info.device_on:
                            light_str = "OFF"
                        elif info.color_temp == 0:
                            cname = hue_to_approx_name(info.hue, info.saturation)
                            light_str = f"ON  | {cname} (hue={info.hue}, sat={info.saturation}) | {info.brightness}%"
                        else:
                            light_str = f"ON  | {info.color_temp}K | {info.brightness}%"
                        print(f"{room_tag:<10} {info.nickname:<15} ({ip}) : {light_str:<36} [{mode_str}]")
                except Exception as e:
                    print(f"{room_tag:<10} {ip:<32} : ERROR ({e})")


async def manual_command(
    action: str,
    kelvin: Optional[int] = None,
    brightness: Optional[int] = None,
    hue_sat: Optional[Tuple[int, int]] = None,
    label: Optional[str] = None,
    room: Optional[str] = None,
) -> None:
    target_rooms = resolve_target_rooms(room)
    username = os.environ.get("TAPO_USERNAME", "")
    password = os.environ.get("TAPO_PASSWORD", "")

    stop_police(room=room, quiet=False)

    with tapo_lock():
        state = load_state()
        client = ApiClient(username, password) if (username and password) else None

        async def _apply(r_name: str, provider: str, ip: str) -> None:
            prefix = f"[{r_name}]"
            override_detail = label or action
            try:
                if provider == "cync":
                    ip_key = ip.replace(".", "_")
                    nickname = os.environ.get(f"CYNC_NAME_{ip_key}", "Bedside Lamp (GE)")
                    if action == "off":
                        await send_cync_command(ip, "off")
                        print(f"{prefix} Turned OFF {nickname} ({ip})")
                    elif action == "on":
                        await send_cync_command(ip, "on")
                        print(f"{prefix} Turned ON {nickname} ({ip})")
                    else:
                        br = brightness if brightness is not None else 50
                        await send_cync_command(ip, "brightness", br)
                        print(f"{prefix} Set {nickname} ({ip}) -> brightness {br}%")
                        override_detail = f"{br}% brightness"
                elif provider == "tuya":
                    dev = get_tuya_bulb(ip)
                    nickname = "Tuya Bulb"
                    if action == "off":
                        await asyncio.to_thread(dev.turn_off)
                        print(f"{prefix} Turned OFF {nickname} ({ip})")
                    elif action == "on":
                        await asyncio.to_thread(dev.turn_on)
                        print(f"{prefix} Turned ON {nickname} ({ip})")
                    elif action == "brightness_only":
                        br = brightness if brightness is not None else 100
                        await asyncio.to_thread(dev.turn_on)
                        await asyncio.to_thread(dev.set_brightness_percentage, br)
                        print(f"{prefix} Set {nickname} ({ip}) -> brightness {br}%")
                        override_detail = f"{br}% brightness"
                    elif action == "kelvin" or (action == "set" and kelvin is not None):
                        br = brightness if brightness is not None else 100
                        t_pct = kelvin_to_tuya_pct(kelvin or 5500)
                        await asyncio.to_thread(dev.set_white_percentage, br, t_pct)
                        k_desc = label or f"{kelvin}K"
                        print(f"{prefix} Set {nickname} ({ip}) -> {k_desc} @ {br}%")
                        override_detail = f"{k_desc} @ {br}%"
                    elif hue_sat is not None:
                        hue, sat = hue_sat
                        br = brightness if brightness is not None else 100
                        await asyncio.to_thread(dev.set_hsv, hue / 360.0, sat / 100.0, br / 100.0)
                        c_desc = label or action.upper()
                        print(f"{prefix} Set {nickname} ({ip}) -> {c_desc} (hue={hue}, sat={sat}) @ {br}%")
                        override_detail = f"{c_desc} @ {br}%"
                else:
                    nickname = "Bedroom Light 1" if ip.endswith(".59") else ("Bedroom Light 2" if ip.endswith(".68") else ip)

                    if action == "off":
                        await run_tapo_with_retry(client, ip, lambda d: d.off())
                        print(f"{prefix} Turned OFF {nickname} ({ip})")
                    elif action == "on":
                        await run_tapo_with_retry(client, ip, lambda d: d.on())
                        print(f"{prefix} Turned ON {nickname} ({ip})")
                    elif action == "brightness_only":
                        br = brightness if brightness is not None else 100
                        await run_tapo_with_retry(client, ip, lambda d: d.set().on().brightness(br).send(d))
                        print(f"{prefix} Set {nickname} ({ip}) -> brightness {br}%")
                        override_detail = f"{br}% brightness"
                    elif action == "kelvin" or (action == "set" and kelvin is not None):
                        br = brightness if brightness is not None else 100
                        await run_tapo_with_retry(
                            client, ip, lambda d: d.set().on().color_temperature(kelvin).brightness(br).send(d)
                        )
                        k_desc = label or f"{kelvin}K"
                        print(f"{prefix} Set {nickname} ({ip}) -> {k_desc} @ {br}%")
                        override_detail = f"{k_desc} @ {br}%"
                    elif hue_sat is not None:
                        hue, sat = hue_sat
                        br = brightness if brightness is not None else 100
                        await run_tapo_with_retry(
                            client, ip, lambda d: d.set().on().hue_saturation(hue, sat).brightness(br).send(d)
                        )
                        c_desc = label or action.upper()
                        print(f"{prefix} Set {nickname} ({ip}) -> {c_desc} (hue={hue}, sat={sat}) @ {br}%")
                        override_detail = f"{c_desc} @ {br}%"

                now_iso = dt.datetime.now().astimezone().isoformat()
                bstate = state.setdefault("bulbs", {}).setdefault(ip, {})
                prev_last = bstate.get("last_applied", {})
                bstate["manual_override"] = True
                bstate["override_reason"] = f"CLI '{override_detail}'"
                bstate["override_at"] = now_iso
                bstate["last_applied"] = {
                    "on": action != "off",
                    "color_temp": kelvin if kelvin is not None else (0 if hue_sat else prev_last.get("color_temp", 5500)),
                    "brightness": brightness if brightness is not None else prev_last.get("brightness", 50),
                    "hue": hue_sat[0] if hue_sat else prev_last.get("hue", 0),
                    "sat": hue_sat[1] if hue_sat else prev_last.get("sat", 0),
                    "updated_at": now_iso,
                }
            except Exception as e:
                print(f"[{r_name}] Error on {ip}: {e}", file=sys.stderr)

        tasks = [
            _apply(r_name, r_cfg.get("ip_providers", {}).get(ip, r_cfg.get("provider", "tapo")), ip)
            for r_name, r_cfg in target_rooms.items()
            for ip in r_cfg.get("ips", [])
        ]
        await asyncio.gather(*tasks)
        save_state(state)


async def run_police_loop(
    room: Optional[str] = None,
    interval: float = 0.30,
    sync_bulbs: bool = False,
) -> None:
    target_rooms = resolve_target_rooms(room)
    username = os.environ.get("TAPO_USERNAME", "")
    password = os.environ.get("TAPO_PASSWORD", "")
    bulbs: List[Tuple[str, str]] = [
        (r_cfg.get("ip_providers", {}).get(ip, r_cfg.get("provider", "tapo")), ip)
        for r_cfg in target_rooms.values()
        for ip in r_cfg.get("ips", [])
        if r_cfg.get("ip_providers", {}).get(ip, r_cfg.get("provider", "tapo")) != "cync"
    ]

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    client = ApiClient(username, password) if (username and password) else None
    devs: Dict[str, Any] = {}

    # Tuya DP 28 (control_data / music mode) instant jump payloads:
    # Leading '0' = Direct Jump (no crossfade through purple!)
    # '0000' = Red (0 deg), '00f0' = Blue (240 deg), '03e8' = 100% sat, '03e8' = 100% brightness
    tuya_red_jump = "0000003e803e800000000"
    tuya_blue_jump = "000f003e803e800000000"

    async def _init_bulb(idx: int, provider: str, ip: str) -> None:
        is_red = sync_bulbs or (idx % 2 == 0)
        hue, sat = RED_HUE_SAT if is_red else BLUE_HUE_SAT
        if provider == "tuya":
            dev = get_tuya_bulb(ip)
            dev.set_socketPersistent(True)
            dev.set_socketNODELAY(True)
            devs[ip] = dev
            payload = tuya_red_jump if is_red else tuya_blue_jump
            await asyncio.to_thread(
                dev.set_multiple_values, {"20": True, "21": "music", "28": payload}, nowait=True
            )
        else:
            dev = await connect_bulb(client, ip)
            devs[ip] = dev
            await dev.set().hue_saturation(hue, sat).brightness(100).send(dev)

    with tapo_lock():
        state = load_state()
        for _, ip in bulbs:
            bstate = state.setdefault("bulbs", {}).setdefault(ip, {})
            bstate["manual_override"] = True
            bstate["override_reason"] = "CLI 'police'"
        save_state(state)
        await asyncio.gather(
            *(_init_bulb(i, prov, ip) for i, (prov, ip) in enumerate(bulbs)), return_exceptions=True
        )

    step = 1
    while not stop_event.is_set():
        t0 = time.monotonic()

        async def _flip_bulb(idx: int, provider: str, ip: str) -> None:
            color_idx = step if sync_bulbs else (step + idx)
            is_red = color_idx % 2 == 0
            hue, sat = RED_HUE_SAT if is_red else BLUE_HUE_SAT
            dev = devs.get(ip)
            try:
                if provider == "tuya":
                    if dev is None:
                        dev = get_tuya_bulb(ip)
                        dev.set_socketPersistent(True)
                        dev.set_socketNODELAY(True)
                        devs[ip] = dev
                        await asyncio.to_thread(
                            dev.set_multiple_values, {"20": True, "21": "music"}, nowait=True
                        )
                    payload = tuya_red_jump if is_red else tuya_blue_jump
                    await asyncio.to_thread(dev.set_value, "28", payload, nowait=True)
                else:
                    if dev is None:
                        dev = await connect_bulb(client, ip)
                        devs[ip] = dev
                        await dev.set().hue_saturation(hue, sat).brightness(100).send(dev)
                    else:
                        await dev.set_hue_saturation(hue, sat)
            except Exception:
                devs.pop(ip, None)

        await asyncio.gather(
            *(_flip_bulb(i, prov, ip) for i, (prov, ip) in enumerate(bulbs)), return_exceptions=True
        )
        step += 1

        elapsed = time.monotonic() - t0
        wait_for = max(0.05, interval - elapsed)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_for)
        except asyncio.TimeoutError:
            pass


def start_police(
    room: Optional[str] = None,
    interval: float = 0.30,
    foreground: bool = False,
    sync_bulbs: bool = False,
) -> None:
    resolve_target_rooms(room)
    room_key = room.strip().lower().replace("_", "-") if (room and room.lower() != "all") else "all"
    pid_path = get_police_pid_path(room_key)

    stop_police(room=room, quiet=False)
    if foreground:
        pid_path.write_text(str(os.getpid()))
        try:
            print(
                f"Police mode [{room_key}] running in foreground (interval={interval:.2f}s). Press Ctrl+C to stop."
            )
            asyncio.run(run_police_loop(room=room, interval=interval, sync_bulbs=sync_bulbs))
        finally:
            pid_path.unlink(missing_ok=True)
        return

    cmd: List[str] = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_police_worker",
        "--room",
        room_key,
        "--interval",
        str(interval),
    ]
    if sync_bulbs:
        cmd.append("--sync")

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_path.write_text(str(proc.pid))
    room_hint = room_key if room_key != "all" else "bedroom"
    print(
        f"Police mode [{room_key}] active (PID {proc.pid}, alternating Red & Blue every ~{interval:.2f}s). "
        f"Run 'lights {room_hint}', 'lights {room_hint} red', or 'lights {room_hint} off' to stop."
    )


async def get_status_dict(room: Optional[str] = None) -> Dict[str, Any]:
    target_rooms = resolve_target_rooms(room)
    username = os.environ.get("TAPO_USERNAME", "")
    password = os.environ.get("TAPO_PASSWORD", "")
    now = dt.datetime.now().astimezone()
    target = get_circadian_target(now)
    police_pids = get_running_police_pids(room)

    with tapo_lock():
        state = load_state()
        client = ApiClient(username, password) if (username and password) else None
        rooms_out: Dict[str, Any] = {}

        for r_name, r_cfg in target_rooms.items():
            room_provider = r_cfg.get("provider", "tapo")
            is_circ = r_cfg.get("circadian", False)
            default_desc = r_cfg.get("default")
            bulbs_list: List[Dict[str, Any]] = []

            for ip in r_cfg.get("ips", []):
                provider = r_cfg.get("ip_providers", {}).get(ip, room_provider)
                bstate = state.get("bulbs", {}).get(ip, {})
                override = bstate.get("manual_override", False)
                if not is_circ:
                    mode_str = f"DEFAULT ({default_desc})" if not override else f"SET ({bstate.get('override_reason', 'manual')})"
                else:
                    mode_str = f"MANUAL ({bstate.get('override_reason', 'app')})" if override else "ON SCHEDULE"

                try:
                    if provider == "cync":
                        ip_key = ip.replace(".", "_")
                        nickname = os.environ.get(f"CYNC_NAME_{ip_key}", "Bedside Lamp (GE)")
                        last = bstate.get("last_applied", {})
                        cync_on = last.get("on", True)
                        cync_br = last.get("brightness", 50)
                        bulbs_list.append({
                            "ip": ip,
                            "nickname": nickname,
                            "on": cync_on,
                            "brightness": cync_br if cync_on else 0,
                            "color_temp": 3000 if cync_on else 0,
                            "summary": "Dimmable White" if cync_on else "OFF",
                            "mode_label": mode_str,
                        })
                    elif provider == "tuya":
                        dev = get_tuya_bulb(ip)
                        st = await asyncio.to_thread(dev.state)
                        if isinstance(st, dict) and "Error" in st:
                            raise RuntimeError(st["Error"])
                        device_on = bool(st.get("is_on"))
                        if not device_on:
                            bulbs_list.append({
                                "ip": ip,
                                "nickname": "Office Bulb",
                                "on": False,
                                "brightness": 0,
                                "color_temp": 0,
                                "summary": "OFF",
                                "mode_label": mode_str,
                            })
                        elif st.get("mode") in ("colour", "music"):
                            h, s, v = await asyncio.to_thread(dev.colour_hsv)
                            hue = int(round(h * 360)) % 360
                            sat = int(round(s * 100))
                            br = int(round(v * 100))
                            cname = hue_to_approx_name(hue, sat)
                            bulbs_list.append({
                                "ip": ip,
                                "nickname": "Office Bulb",
                                "on": True,
                                "brightness": br,
                                "color_temp": 0,
                                "hue": hue,
                                "sat": sat,
                                "summary": cname,
                                "mode_label": mode_str,
                            })
                        else:
                            br_pct = int(round(await asyncio.to_thread(dev.get_brightness_percentage) or 0))
                            ct_pct = int(round(await asyncio.to_thread(dev.get_colourtemp_percentage) or 0))
                            ct_k = tuya_pct_to_kelvin(ct_pct)
                            bulbs_list.append({
                                "ip": ip,
                                "nickname": "Office Bulb",
                                "on": True,
                                "brightness": br_pct,
                                "color_temp": ct_k,
                                "summary": f"{ct_k}K",
                                "mode_label": mode_str,
                            })
                    else:
                        info = await run_tapo_with_retry(client, ip, lambda d: d.get_device_info())
                        nickname = info.nickname or ip
                        if r_name == "bedroom" and nickname.startswith("Office Light"):
                            nickname = nickname.replace("Office Light", "Bedroom Light")
                        if not info.device_on:
                            bulbs_list.append({
                                "ip": ip,
                                "nickname": nickname,
                                "on": False,
                                "brightness": 0,
                                "color_temp": 0,
                                "summary": "OFF",
                                "mode_label": mode_str,
                            })
                        elif info.color_temp == 0:
                            cname = hue_to_approx_name(info.hue, info.saturation)
                            bulbs_list.append({
                                "ip": ip,
                                "nickname": nickname,
                                "on": True,
                                "brightness": info.brightness,
                                "color_temp": 0,
                                "hue": info.hue,
                                "sat": info.saturation,
                                "summary": cname,
                                "mode_label": mode_str,
                            })
                        else:
                            bulbs_list.append({
                                "ip": ip,
                                "nickname": nickname,
                                "on": True,
                                "brightness": info.brightness,
                                "color_temp": info.color_temp,
                                "summary": f"{info.color_temp}K",
                                "mode_label": mode_str,
                            })
                except Exception as e:
                    last = bstate.get("last_applied")
                    fallback_name = "Bedroom Light 1" if ip.endswith(".59") else ("Bedroom Light 2" if ip.endswith(".68") else ip)
                    if last:
                        l_on = bool(last.get("on", True))
                        l_ct = int(last.get("color_temp", 5500))
                        l_br = int(last.get("brightness", 50))
                        l_hue = int(last.get("hue", 0))
                        l_sat = int(last.get("sat", 100))
                        l_sum = "OFF" if not l_on else (f"{l_ct}K" if l_ct > 0 else hue_to_approx_name(l_hue, l_sat))
                        bulbs_list.append({
                            "ip": ip,
                            "nickname": fallback_name,
                            "on": l_on,
                            "brightness": l_br if l_on else 0,
                            "color_temp": l_ct if l_on else 0,
                            "hue": l_hue,
                            "sat": l_sat,
                            "summary": l_sum,
                            "mode_label": mode_str,
                        })
                    else:
                        bulbs_list.append({
                            "ip": ip,
                            "nickname": fallback_name,
                            "on": False,
                            "error": str(e),
                            "mode_label": "ERROR",
                        })

            rooms_out[r_name] = {
                "provider": room_provider,
                "circadian": is_circ,
                "default": default_desc,
                "police_pid": police_pids.get(r_name) or police_pids.get("all"),
                "bulbs": bulbs_list,
            }

        wake_cfg = get_wakeup_config(state)
        is_active, progress, k, br, desc = calculate_wakeup_progression(now, wake_cfg)
        start_dt, wake_dt = get_wakeup_window(now, wake_cfg)

        return {
            "local_time": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "phase": target,
            "wakeup": {
                "enabled": bool(wake_cfg.get("enabled", True)),
                "time": wake_cfg.get("time", "16:00"),
                "duration": int(wake_cfg.get("duration", 30)),
                "target_brightness": int(wake_cfg.get("target_brightness", 80)),
                "target_temp": int(wake_cfg.get("target_temp", 6000)),
                "is_active": is_active,
                "progress": round(progress, 2),
                "current_kelvin": k if is_active else None,
                "current_brightness": br if is_active else None,
                "next_start": start_dt.strftime("%H:%M"),
                "next_wake": wake_dt.strftime("%H:%M"),
                "status_text": desc,
            },
            "rooms": rooms_out,
        }


async def execute_web_command(room: Optional[str], command: str) -> None:
    cmd_clean = command.strip()
    if not cmd_clean or cmd_clean.lower() in ("default", "reset"):
        await apply_room_default_or_sync(room=room, show_output=False)
        return

    tokens = cmd_clean.split()
    sub = tokens[0].lower()

    if sub in ("schedule", "sync"):
        await sync_once(force=True, room=room)
    elif sub in ("on", "off"):
        await manual_command(sub, room=room)
    elif sub == "police":
        start_police(room=room, interval=0.30, foreground=False, sync_bulbs=False)
    elif sub == "stop":
        stop_police(room=room, quiet=True)
    elif sub in ("wakeup", "sunrise"):
        rest = tokens[1:]
        if not rest or rest[0] in ("status", "info"):
            pass
        elif rest[0] in ("on", "enable", "start"):
            save_wakeup_config({"enabled": True})
        elif rest[0] in ("off", "disable", "stop"):
            save_wakeup_config({"enabled": False})
        elif rest[0] in ("test", "preview", "demo"):
            await preview_wakeup_routine()
        elif re.fullmatch(r"\d{1,2}:\d{2}", rest[0]):
            h, m = map(int, rest[0].split(":"))
            save_wakeup_config({"time": f"{h:02d}:{m:02d}", "enabled": True})
        elif re.fullmatch(r"\d+m?", rest[0]):
            d = int(rest[0].rstrip("m"))
            save_wakeup_config({"duration": max(5, min(120, d))})
    else:
        custom = parse_custom_lights_command(tokens)
        if custom is None:
            raise ValueError(f"Unrecognized command: {command}")
        c_type = custom["type"]
        if c_type == "color":
            await manual_command(
                "color",
                hue_sat=custom["hue_sat"],
                brightness=custom["brightness"],
                label=custom["label"],
                room=room,
            )
        elif c_type == "kelvin":
            await manual_command(
                "kelvin",
                kelvin=custom["kelvin"],
                brightness=custom["brightness"],
                label=custom["label"],
                room=room,
            )
        elif c_type == "brightness_only":
            await manual_command(
                "brightness_only",
                brightness=custom["brightness"],
                label=custom["label"],
                room=room,
            )


async def start_web_server(host: str = "127.0.0.1", port: int = 8095) -> Any:
    from aiohttp import web

    ui_path = BASE_DIR / "web_ui.html"

    async def handle_index(_request: web.Request) -> web.Response:
        return web.Response(text=ui_path.read_text(), content_type="text/html")

    async def handle_status(request: web.Request) -> web.Response:
        room = request.query.get("room")
        async with get_web_lock():
            data = await get_status_dict(room=room)
        return web.json_response(data)

    async def handle_action(request: web.Request) -> web.Response:
        body = await request.json()
        room = body.get("room")
        command = body.get("command", "")
        async with get_web_lock():
            await execute_web_command(room=room, command=command)
            data = await get_status_dict(room=None)
        return web.json_response({"ok": True, "status": data})

    async def handle_wakeup(request: web.Request) -> web.Response:
        body = await request.json()
        updates = {}
        if "enabled" in body:
            updates["enabled"] = bool(body["enabled"])
        if "time" in body:
            t = str(body["time"]).strip()
            if re.fullmatch(r"\d{1,2}:\d{2}", t):
                h, m = map(int, t.split(":"))
                updates["time"] = f"{h:02d}:{m:02d}"
        if "duration" in body:
            updates["duration"] = max(5, min(120, int(body["duration"])))
        if "target_brightness" in body:
            updates["target_brightness"] = max(10, min(100, int(body["target_brightness"])))
        if "target_temp" in body:
            updates["target_temp"] = max(2500, min(6500, int(body["target_temp"])))
        if updates:
            save_wakeup_config(updates)
        async with get_web_lock():
            if updates:
                await check_wakeup_routine(force=True)
            data = await get_status_dict(room=None)
        return web.json_response({"ok": True, "status": data, "wakeup": get_wakeup_config()})

    async def handle_wakeup_preview(_request: web.Request) -> web.Response:
        asyncio.create_task(preview_wakeup_routine())
        return web.json_response({"ok": True, "message": "Sunrise preview started (15s)"})

    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/status", handle_status)
    app.router.add_post("/api/action", handle_action)
    app.router.add_post("/api/wakeup", handle_wakeup)
    app.router.add_post("/api/wakeup/preview", handle_wakeup_preview)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Lights Web UI listening on http://%s:%d", host, port)
    return runner


async def run_daemon(interval: int = 180) -> None:
    logger.info("Starting Circadian & Wake-Up Light daemon (interval=%ds)...", interval)
    await start_web_server(host="127.0.0.1", port=8095)
    last_circadian_sync = 0.0
    while True:
        now_ts = time.time()
        try:
            async with get_web_lock():
                await check_wakeup_routine()
                if now_ts - last_circadian_sync >= interval:
                    await sync_once(force=False, room=None)
                    last_circadian_sync = now_ts
        except Exception as e:
            logger.error("Daemon tick error: %s", e)
        await asyncio.sleep(20)


async def apply_room_default_or_sync(room: Optional[str] = None, show_output: bool = True) -> None:
    """
    For rooms with a static default (e.g. bedroom -> 5500K @ 50%) and not on circadian schedule,
    apply their static default. For circadian rooms, sync to the active circadian phase.
    """
    target_rooms = resolve_target_rooms(room)
    for r_name, r_cfg in target_rooms.items():
        default_str = r_cfg.get("default")
        if default_str and not r_cfg.get("circadian"):
            parsed = parse_custom_lights_command(default_str.split())
            if parsed and parsed["type"] == "kelvin":
                await manual_command(
                    "kelvin",
                    kelvin=parsed["kelvin"],
                    brightness=parsed["brightness"],
                    label=f"Default ({parsed['kelvin']}K)",
                    room=r_name,
                )
                with tapo_lock():
                    st = load_state()
                    for ip in r_cfg.get("ips", []):
                        st.setdefault("bulbs", {}).setdefault(ip, {})["manual_override"] = False
                    save_state(st)
            elif parsed and parsed["type"] == "color":
                await manual_command(
                    "color",
                    hue_sat=parsed["hue_sat"],
                    brightness=parsed["brightness"],
                    label=f"Default ({parsed['label']})",
                    room=r_name,
                )
            elif parsed and parsed["type"] == "brightness_only":
                await manual_command(
                    "brightness_only",
                    brightness=parsed["brightness"],
                    label=f"Default ({parsed['brightness']}%)",
                    room=r_name,
                )
                with tapo_lock():
                    st = load_state()
                    for ip in r_cfg.get("ips", []):
                        st.setdefault("bulbs", {}).setdefault(ip, {})["manual_override"] = False
                    save_state(st)
        else:
            await sync_once(force=True, room=r_name)
    if show_output:
        await show_status(room=room)


def print_help() -> None:
    rooms = load_rooms()
    room_lines = []
    for r, cfg in rooms.items():
        prov = cfg.get("provider", "tapo").upper()
        ips = ", ".join(cfg.get("ips", []))
        mode = "circadian schedule" if cfg.get("circadian") else f"default: {cfg.get('default') or 'manual'}"
        room_lines.append(f"  {r:<12} : [{prov}] {ips} ({mode})")
    room_summary = "\n".join(room_lines) or "  (none configured)"

    print(
        f"""Usage: lights <room> [command | color | kelvin | brightness] [brightness]

Smart Lights Multi-Room Manager (TP-Link Tapo & Tuya)

Configured Rooms (.env):
{room_summary}
  all          : Target all configured rooms simultaneously

Room & Service Commands:
  lights <room>                     Reset room to its default (e.g. bedroom -> 5500K @ 50%, office -> circadian)
  lights <room> default / reset     Alias to reset room to its default or circadian schedule
  lights <room> schedule (sync)     Force room to current circadian schedule phase
  lights <room> status              Display live bulb state, color/temp, and schedule phase
  lights <room> on / off            Turn room lights ON or OFF
  lights <room> police [--interval] Flashing Red & Blue emergency mode (background)
  lights <room> stop                Stop police mode for room and leave current light states
  lights bedroom wakeup [on|off|time|test] Configure or test automated sunrise wake-up
  lights status                     Show status across all rooms
  lights daemon [--interval]        Run background circadian sync service for circadian rooms

Color & Brightness Controls:
  lights <room> <color> [1-100]     Set any named color (e.g. lights bedroom green 60, purple 40)
  lights <room> <#hex> [1-100]      Set custom hex color (e.g. lights bedroom #ff8800 80)
  lights <room> <temp> [1-100]      Set white preset (warm, soft, daylight, cool)
  lights <room> <kelvin> [1-100]    Set Kelvin directly 2500-6500 (e.g. lights bedroom 2700 80)
  lights <room> <1-100>%            Set brightness directly (e.g. lights bedroom 60%, 80)

Popular Named Colors:
  red, darkred, crimson, pink, hotpink, rose, orange, amber, gold, yellow,
  lime, green, forestgreen, mint, teal, cyan, turquoise, skyblue, blue,
  navy, indigo, purple, violet, magenta, lavender, peach, coral, chocolate...

White Temperature Presets:
  candle (2500K), warm / warmwhite (2700K), soft / incandescent (3000K),
  reading / white (4000K), daylight (5500K), cool / coolwhite / focus (6500K)
"""
    )


def main() -> None:
    raw_args = sys.argv[1:]
    rooms = load_rooms()

    # 1. No arguments: apply default/schedule and show status across all rooms
    if not raw_args:
        asyncio.run(apply_room_default_or_sync(room=None))
        return

    first = raw_args[0].lower().strip()

    # 2. Help flags
    if first in ("-h", "--help", "help"):
        print_help()
        return

    # 3. Internal/service subcommands that don't take a leading room name
    if first == "_police_worker":
        p = argparse.ArgumentParser(prog="lights _police_worker")
        p.add_argument("--room", type=str, default="bedroom")
        p.add_argument("--interval", type=float, default=0.30)
        p.add_argument("--sync", action="store_true")
        args = p.parse_args(raw_args[1:])
        pid_path = get_police_pid_path(args.room)
        try:
            asyncio.run(run_police_loop(room=args.room, interval=args.interval, sync_bulbs=args.sync))
        finally:
            pid_path.unlink(missing_ok=True)
        return
    elif first == "daemon":
        p = argparse.ArgumentParser(prog="lights daemon")
        p.add_argument("--interval", type=int, default=180, help="Poll/update interval in seconds")
        args = p.parse_args(raw_args[1:])
        asyncio.run(run_daemon(interval=args.interval))
        return
    elif first in ("wakeup", "sunrise"):
        handle_wakeup_cli(raw_args[1:])
        return

    # 4. Determine target room if first argument is a configured room or 'all'
    first_norm = first.replace("_", "-")
    if first_norm in ("ge", "lamp") and "bedside" in rooms:
        first_norm = "bedside"
    room: Optional[str] = None
    cmd_args = raw_args

    if first_norm in rooms or first_norm == "all":
        room = first_norm
        cmd_args = raw_args[1:]

    # 5. If only a room name was passed (e.g. `lights bedroom`), apply room default/schedule & show status
    if not cmd_args:
        asyncio.run(apply_room_default_or_sync(room=room))
        return

    sub = cmd_args[0].lower().strip()

    if sub in ("-h", "--help", "help"):
        print_help()
        return

    # 6. Known single-word subcommands
    if sub in ("default", "reset"):
        asyncio.run(apply_room_default_or_sync(room=room))
        return
    elif sub in ("schedule", "sync"):
        asyncio.run(sync_once(force=True, room=room))
        asyncio.run(show_status(room=room))
        return
    elif sub == "status":
        asyncio.run(show_status(room=room))
        return
    elif sub in ("on", "off"):
        asyncio.run(manual_command(sub, room=room))
        return
    elif sub == "stop":
        if not stop_police(room=room, quiet=False):
            print("Police mode is not running.")
        return
    elif sub in ("wakeup", "sunrise"):
        handle_wakeup_cli(cmd_args[1:])
        return

    # 7. Built-in subcommands with options (police, set)
    if sub == "police":
        prog_name = f"lights {room} police" if room else "lights police"
        p = argparse.ArgumentParser(prog=prog_name)
        p.add_argument("--interval", type=float, default=0.30, help="Target seconds per color swap")
        p.add_argument(
            "--fg", "--foreground", dest="foreground", action="store_true", help="Run in foreground"
        )
        p.add_argument(
            "--sync",
            action="store_true",
            help="Flash bulbs the same color simultaneously",
        )
        args = p.parse_args(cmd_args[1:])
        start_police(room=room, interval=args.interval, foreground=args.foreground, sync_bulbs=args.sync)
        return
    elif sub == "set":
        prog_name = f"lights {room} set" if room else "lights set"
        p = argparse.ArgumentParser(prog=prog_name)
        p.add_argument("kelvin", type=int, help="Color temperature in Kelvin (2500-6500)")
        p.add_argument(
            "brightness",
            type=int,
            nargs="?",
            default=100,
            help="Brightness percentage (1-100)",
        )
        args = p.parse_args(cmd_args[1:])
        asyncio.run(manual_command("set", kelvin=args.kelvin, brightness=args.brightness, room=room))
        return

    # 8. Custom Color, Color Temp, Kelvin, or Brightness command
    custom = parse_custom_lights_command(cmd_args)
    if custom is not None:
        c_type = custom["type"]
        if c_type == "color":
            asyncio.run(
                manual_command(
                    "color",
                    hue_sat=custom["hue_sat"],
                    brightness=custom["brightness"],
                    label=custom["label"],
                    room=room,
                )
            )
            return
        elif c_type == "kelvin":
            asyncio.run(
                manual_command(
                    "kelvin",
                    kelvin=custom["kelvin"],
                    brightness=custom["brightness"],
                    label=custom["label"],
                    room=room,
                )
            )
            return
        elif c_type == "brightness_only":
            asyncio.run(
                manual_command(
                    "brightness_only",
                    brightness=custom["brightness"],
                    label=custom["label"],
                    room=room,
                )
            )
            return

    # 9. Unrecognized room or command
    available_rooms = ", ".join(rooms.keys())
    if room is None:
        print(
            f"Error: Unknown room, command, or color '{' '.join(raw_args)}'.\n"
            f"Configured rooms: {available_rooms} (or 'all').\n"
            f"Example: lights bedroom {' '.join(raw_args[1:]) if len(raw_args) > 1 else 'green 60'}",
            file=sys.stderr,
        )
    else:
        print(
            f"Error: Unknown lights command or color '{' '.join(cmd_args)}' for room '{room}'.",
            file=sys.stderr,
        )
    print("Run 'lights --help' to see all available rooms, commands, colors, and presets.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
