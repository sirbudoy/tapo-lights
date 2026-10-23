#!/usr/bin/env python3
"""
Tapo Circadian Light Manager for Office Light 1 (192.168.68.59) & Office Light 2 (192.168.68.68).

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


@contextmanager
def tapo_lock() -> Iterator[None]:
    """Ensure CLI and background daemon do not collide on TPAP handshakes."""
    LOCK_PATH.touch(exist_ok=True)
    with LOCK_PATH.open("r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


async def connect_bulb(client: ApiClient, ip: str) -> Any:
    """Connect to an L530 bulb with a brief retry in case the mobile app just handshaked."""
    for attempt in range(2):
        try:
            return await client.l530(ip)
        except Exception:
            if attempt == 0:
                await asyncio.sleep(1.5)
            else:
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


def get_running_police_pid() -> Optional[int]:
    if not POLICE_PID_PATH.exists():
        return None
    try:
        pid = int(POLICE_PID_PATH.read_text().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        try:
            POLICE_PID_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        return None


def stop_police(quiet: bool = False) -> bool:
    pid = get_running_police_pid()
    if pid is None:
        return False
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
    finally:
        POLICE_PID_PATH.unlink(missing_ok=True)
    if not quiet:
        print(f"Stopped police mode (PID {pid})")
    return True


async def apply_target_to_bulb(
    client: ApiClient,
    ip: str,
    target: Dict[str, Any],
    state: Dict[str, Any],
    force: bool = False,
) -> None:
    bulb_state = state.setdefault("bulbs", {}).setdefault(ip, {})

    if not force and bulb_state.get("manual_override"):
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
                "Manual app change detected on %s (%s): %s. Pausing auto-adjust for this bulb until 'lights' command is run.",
                nickname,
                ip,
                reason,
            )
            return

    if not target["on"]:
        if info.device_on or force:
            await dev.off()
            logger.info("Turned OFF %s (%s) [%s]", nickname, ip, target["phase"])
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
                "Set %s (%s) -> %dK @ %d%% [%s]",
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


async def sync_once(force: bool = False) -> None:
    load_dotenv(ENV_PATH)
    username = os.environ["TAPO_USERNAME"]
    password = os.environ["TAPO_PASSWORD"]
    ips = [ip.strip() for ip in os.environ["TAPO_IPS"].split(",") if ip.strip()]

    if force:
        stop_police(quiet=False)

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
                stop_police(quiet=True)
                for b in state.get("bulbs", {}).values():
                    b["manual_override"] = False
                state["paused"] = False

        state["last_phase"] = target["phase"]

        if state.get("paused") and not force:
            save_state(state)
            return

        client = ApiClient(username, password)
        for ip in ips:
            try:
                await apply_target_to_bulb(client, ip, target, state, force=force)
            except Exception as e:
                logger.error("Error communicating with %s: %s", ip, e)

        save_state(state)


async def show_status() -> None:
    load_dotenv(ENV_PATH)
    username = os.environ["TAPO_USERNAME"]
    password = os.environ["TAPO_PASSWORD"]
    ips = [ip.strip() for ip in os.environ["TAPO_IPS"].split(",") if ip.strip()]

    with tapo_lock():
        now = dt.datetime.now().astimezone()
        target = get_circadian_target(now)
        state = load_state()
        police_pid = get_running_police_pid()

        print(f"Local Time      : {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"Current Phase   : {target['phase']}")
        if target["on"]:
            print(f"Schedule Target : ON  | {target['color_temp']}K | {target['brightness']}% brightness")
        else:
            print("Schedule Target : OFF (Sleep window)")
        if police_pid:
            mode_summary = f"POLICE MODE ACTIVE (PID {police_pid})"
        elif state.get("paused"):
            mode_summary = "PAUSED"
        else:
            mode_summary = "ACTIVE (auto-tracking)"
        print(f"Service Mode    : {mode_summary}")
        print("-" * 64)

        client = ApiClient(username, password)
        for ip in ips:
            try:
                dev = await connect_bulb(client, ip)
                info = await dev.get_device_info()
                bstate = state.get("bulbs", {}).get(ip, {})
                override = bstate.get("manual_override", False)
                mode_str = (
                    f"MANUAL OVERRIDE ({bstate.get('override_reason', 'app')})"
                    if override
                    else "ON SCHEDULE"
                )
                if not info.device_on:
                    light_str = "OFF"
                elif info.color_temp == 0:
                    cname = hue_to_approx_name(info.hue, info.saturation)
                    light_str = f"ON  | {cname} (hue={info.hue}, sat={info.saturation}) | {info.brightness}%"
                else:
                    light_str = f"ON  | {info.color_temp}K | {info.brightness}%"
                print(f"{info.nickname:<15} ({ip}) : {light_str:<36} [{mode_str}]")
            except Exception as e:
                print(f"{ip:<32} : ERROR ({e})")


async def manual_command(
    action: str,
    kelvin: Optional[int] = None,
    brightness: Optional[int] = None,
    hue_sat: Optional[Tuple[int, int]] = None,
    label: Optional[str] = None,
) -> None:
    load_dotenv(ENV_PATH)
    username = os.environ["TAPO_USERNAME"]
    password = os.environ["TAPO_PASSWORD"]
    ips = [ip.strip() for ip in os.environ["TAPO_IPS"].split(",") if ip.strip()]

    stop_police(quiet=False)

    with tapo_lock():
        state = load_state()
        client = ApiClient(username, password)

        async def _apply(ip: str) -> None:
            try:
                dev = await connect_bulb(client, ip)
                info = await dev.get_device_info()
                nickname = info.nickname or ip
                override_detail = label or action

                if action == "off":
                    await dev.off()
                    print(f"Turned OFF {nickname} ({ip})")
                elif action == "on":
                    await dev.on()
                    print(f"Turned ON {nickname} ({ip})")
                elif action == "brightness_only":
                    br = brightness if brightness is not None else 100
                    await dev.set().on().brightness(br).send(dev)
                    print(f"Set {nickname} ({ip}) -> brightness {br}%")
                    override_detail = f"{br}% brightness"
                elif action == "kelvin" or (action == "set" and kelvin is not None):
                    br = brightness if brightness is not None else 100
                    await dev.set().on().color_temperature(kelvin).brightness(br).send(dev)
                    k_desc = label or f"{kelvin}K"
                    print(f"Set {nickname} ({ip}) -> {k_desc} @ {br}%")
                    override_detail = f"{k_desc} @ {br}%"
                elif hue_sat is not None:
                    hue, sat = hue_sat
                    br = brightness if brightness is not None else 100
                    await dev.set().on().hue_saturation(hue, sat).brightness(br).send(dev)
                    c_desc = label or action.upper()
                    print(f"Set {nickname} ({ip}) -> {c_desc} (hue={hue}, sat={sat}) @ {br}%")
                    override_detail = f"{c_desc} @ {br}%"

                bstate = state.setdefault("bulbs", {}).setdefault(ip, {})
                bstate["manual_override"] = True
                bstate["override_reason"] = f"CLI '{override_detail}'"
                bstate["last_applied"] = {
                    "on": action != "off",
                    "color_temp": kelvin or (0 if hue_sat is not None else info.color_temp),
                    "brightness": brightness or info.brightness,
                    "updated_at": dt.datetime.now().astimezone().isoformat(),
                }
            except Exception as e:
                print(f"Error on {ip}: {e}", file=sys.stderr)

        await asyncio.gather(*(_apply(ip) for ip in ips))
        save_state(state)


async def run_police_loop(interval: float = 0.30, sync_bulbs: bool = False) -> None:
    load_dotenv(ENV_PATH)
    username = os.environ["TAPO_USERNAME"]
    password = os.environ["TAPO_PASSWORD"]
    ips = [ip.strip() for ip in os.environ["TAPO_IPS"].split(",") if ip.strip()]

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    client = ApiClient(username, password)
    devs: Dict[str, Any] = {}

    async def _init_bulb(idx: int, ip: str) -> None:
        dev = await connect_bulb(client, ip)
        devs[ip] = dev
        hue, sat = RED_HUE_SAT if (sync_bulbs or idx % 2 == 0) else BLUE_HUE_SAT
        await dev.set().hue_saturation(hue, sat).brightness(100).send(dev)

    with tapo_lock():
        state = load_state()
        for ip in ips:
            bstate = state.setdefault("bulbs", {}).setdefault(ip, {})
            bstate["manual_override"] = True
            bstate["override_reason"] = "CLI 'police'"
        save_state(state)
        await asyncio.gather(*(_init_bulb(i, ip) for i, ip in enumerate(ips)), return_exceptions=True)

    step = 1
    while not stop_event.is_set():
        t0 = time.monotonic()

        async def _flip_bulb(idx: int, ip: str) -> None:
            color_idx = step if sync_bulbs else (step + idx)
            hue, sat = RED_HUE_SAT if (color_idx % 2 == 0) else BLUE_HUE_SAT
            dev = devs.get(ip)
            try:
                if dev is None:
                    dev = await connect_bulb(client, ip)
                    devs[ip] = dev
                    await dev.set().hue_saturation(hue, sat).brightness(100).send(dev)
                else:
                    await dev.set_hue_saturation(hue, sat)
            except Exception:
                try:
                    dev = await connect_bulb(client, ip)
                    devs[ip] = dev
                    await dev.set().hue_saturation(hue, sat).brightness(100).send(dev)
                except Exception:
                    devs.pop(ip, None)

        await asyncio.gather(*(_flip_bulb(i, ip) for i, ip in enumerate(ips)), return_exceptions=True)
        step += 1

        elapsed = time.monotonic() - t0
        wait_for = max(0.05, interval - elapsed)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_for)
        except asyncio.TimeoutError:
            pass


def start_police(interval: float = 0.30, foreground: bool = False, sync_bulbs: bool = False) -> None:
    stop_police(quiet=False)
    if foreground:
        POLICE_PID_PATH.write_text(str(os.getpid()))
        try:
            print(
                f"Police mode running in foreground (interval={interval:.2f}s). Press Ctrl+C to stop."
            )
            asyncio.run(run_police_loop(interval=interval, sync_bulbs=sync_bulbs))
        finally:
            POLICE_PID_PATH.unlink(missing_ok=True)
        return

    cmd: List[str] = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_police_worker",
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
    POLICE_PID_PATH.write_text(str(proc.pid))
    print(
        f"Police mode active (PID {proc.pid}, alternating Red & Blue every ~{interval:.2f}s). "
        "Run 'lights', 'lights red', or 'lights off' to stop."
    )


async def run_daemon(interval: int = 180) -> None:
    logger.info("Starting Tapo Circadian Light daemon (interval=%ds)...", interval)
    await sync_once(force=False)
    while True:
        await asyncio.sleep(interval)
        try:
            await sync_once(force=False)
        except Exception as e:
            logger.error("Daemon tick error: %s", e)


async def sync_and_show() -> None:
    await sync_once(force=True)
    await show_status()


def print_help() -> None:
    print(
        """Usage: lights [command | color | kelvin | brightness] [brightness]

Tapo Smart Lights Circadian Manager & Quick Color Controller
Office Light 1 (192.168.68.59) & Office Light 2 (192.168.68.68)

Circadian & Service Commands:
  lights                     Revert lights to active circadian schedule & show status
  lights schedule (sync)     Alias to revert lights to active circadian schedule
  lights status              Display live bulb state, color/temp, and schedule phase
  lights on / off            Turn both lights ON or OFF
  lights police [--interval] Flashing Red & Blue emergency mode (background)
  lights stop                Stop police mode and leave current light states
  lights daemon [--interval] Run background circadian sync service

Color & Brightness Controls:
  lights <color> [1-100]     Set any named color (e.g. green 60, purple 40, cyan)
  lights <#hex> [1-100]      Set custom hex color (e.g. #ff8800 80, #e0115f 50)
  lights <temp> [1-100]      Set white preset (warm, soft, daylight, cool)
  lights <kelvin> [1-100]    Set Kelvin directly 2500-6500 (e.g. 2700 80, 4000k 60)
  lights <1-100>%            Set brightness directly (e.g. 60%, 80)

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

    # 1. No arguments: default to schedule and status
    if not raw_args:
        asyncio.run(sync_and_show())
        return

    first = raw_args[0].lower().strip()

    # 2. Help flags
    if first in ("-h", "--help", "help"):
        print_help()
        return

    # 3. Known single-word subcommands
    if first in ("schedule", "sync", "reset"):
        asyncio.run(sync_and_show())
        return
    elif first == "status":
        asyncio.run(show_status())
        return
    elif first in ("on", "off"):
        asyncio.run(manual_command(first))
        return
    elif first == "stop":
        if not stop_police(quiet=False):
            print("Police mode is not running.")
        return

    # 4. Built-in subcommands with options (police, daemon, _police_worker, set)
    if first == "police":
        p = argparse.ArgumentParser(prog="lights police")
        p.add_argument("--interval", type=float, default=0.30, help="Target seconds per color swap")
        p.add_argument(
            "--fg", "--foreground", dest="foreground", action="store_true", help="Run in foreground"
        )
        p.add_argument(
            "--sync",
            action="store_true",
            help="Flash both bulbs the same color simultaneously",
        )
        args = p.parse_args(raw_args[1:])
        start_police(interval=args.interval, foreground=args.foreground, sync_bulbs=args.sync)
        return
    elif first == "_police_worker":
        p = argparse.ArgumentParser(prog="lights _police_worker")
        p.add_argument("--interval", type=float, default=0.30)
        p.add_argument("--sync", action="store_true")
        args = p.parse_args(raw_args[1:])
        try:
            asyncio.run(run_police_loop(interval=args.interval, sync_bulbs=args.sync))
        finally:
            POLICE_PID_PATH.unlink(missing_ok=True)
        return
    elif first == "daemon":
        p = argparse.ArgumentParser(prog="lights daemon")
        p.add_argument("--interval", type=int, default=180, help="Poll/update interval in seconds")
        args = p.parse_args(raw_args[1:])
        asyncio.run(run_daemon(interval=args.interval))
        return
    elif first == "set":
        p = argparse.ArgumentParser(prog="lights set")
        p.add_argument("kelvin", type=int, help="Color temperature in Kelvin (2500-6500)")
        p.add_argument(
            "brightness",
            type=int,
            nargs="?",
            default=100,
            help="Brightness percentage (1-100)",
        )
        args = p.parse_args(raw_args[1:])
        asyncio.run(manual_command("set", kelvin=args.kelvin, brightness=args.brightness))
        return

    # 5. Custom Color, Color Temp, Kelvin, or Brightness command
    custom = parse_custom_lights_command(raw_args)
    if custom is not None:
        c_type = custom["type"]
        if c_type == "color":
            asyncio.run(
                manual_command(
                    "color",
                    hue_sat=custom["hue_sat"],
                    brightness=custom["brightness"],
                    label=custom["label"],
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
                )
            )
            return
        elif c_type == "brightness_only":
            asyncio.run(
                manual_command(
                    "brightness_only",
                    brightness=custom["brightness"],
                    label=custom["label"],
                )
            )
            return

    # 6. Unrecognized command
    print(f"Error: Unknown lights command or color '{' '.join(raw_args)}'.", file=sys.stderr)
    print("Run 'lights --help' to see all available commands, colors, and presets.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
