#!/usr/bin/env python3
"""One-time setup: writes agentops/.env.agent (chmod 600). Runs on the Mac (Python 3.9+)."""
import getpass
import json
import os
import pathlib
import ssl
import time
import urllib.parse
import urllib.request

ENVF = pathlib.Path(__file__).resolve().parent / ".env.agent"
# python.org builds ship a stale CA bundle; prefer the macOS system one
CTX = ssl.create_default_context(cafile="/etc/ssl/cert.pem") if os.path.exists("/etc/ssl/cert.pem") else None


def tg(token, method, **params):
    data = urllib.parse.urlencode(params).encode()
    for attempt in range(5):  # Telegram connections occasionally get reset; retry instead of crashing
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", data, timeout=40, context=CTX) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError:
            raise  # bad token etc. — retrying won't help
        except OSError:
            if attempt == 4:
                raise
            time.sleep(3)


def main():
    env = {}
    if ENVF.exists():
        for line in ENVF.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v

    def save():
        ENVF.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
        os.chmod(ENVF, 0o600)

    def ask(key, prompt, secret=True):
        cur = env.get(key, "")
        shown = " [keep existing]" if cur else ""
        val = (getpass.getpass if secret else input)(f"{prompt}{shown}: ").strip()
        if val:
            env[key] = val
            save()

    print("\n1) Claude: in ANOTHER terminal run `claude setup-token`, log in, and paste the token here.")
    print("   (Uses your Claude subscription; the agent waits out usage limits automatically.)")
    ask("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")

    print("\n2) OpenAI API key (optional, can add later): only needed for the Realtime API live demo in M1.")
    print("   Codex review uses your ChatGPT login instead: run `./cook codex-login` after setup. Leave empty to skip.")
    ask("OPENAI_API_KEY", "OPENAI_API_KEY")

    print("\n3) Telegram: open @BotFather in Telegram → /newbot → copy the token.")
    ask("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    token = env.get("TELEGRAM_BOT_TOKEN")
    if token and not env.get("TELEGRAM_CHAT_ID"):
        try:
            me = tg(token, "getMe")["result"]["username"]
        except Exception as e:
            print(f"   ✗ Telegram rejected the token or is unreachable ({e}). Re-run ./cook setup; other values are saved.")
            return
        print(f"\n   Now send any message to @{me} in Telegram. Waiting up to 2 minutes…")
        deadline = time.time() + 120
        while time.time() < deadline and not env.get("TELEGRAM_CHAT_ID"):
            for u in tg(token, "getUpdates", timeout=20).get("result", []):
                chat = (u.get("message") or {}).get("chat", {})
                if chat.get("type") == "private":
                    env["TELEGRAM_CHAT_ID"] = str(chat["id"])
        if env.get("TELEGRAM_CHAT_ID"):
            tg(token, "sendMessage", chat_id=env["TELEGRAM_CHAT_ID"], text="👋 Voice Room agent connected.")
            print("   ✓ Connected. Check Telegram.")
        else:
            print("   ✗ No message received. Re-run ./cook setup to retry.")

    save()
    print(f"\nSaved {ENVF}. Start with: ./cook")


if __name__ == "__main__":
    main()
