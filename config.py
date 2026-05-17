import os

# ─── Telegram ───
BOT_TOKEN = os.environ.get("BOT_TOKEN")
AUTHORIZED_CHAT_ID = 6469077855

# ─── File System ───
PHRASE_FILE = "phrase.txt"
PROXY_FILE = "proxy.txt"

# ─── Endpoints ───
CANTOR_BASE   = "https://wallet-backend.main.digik.cantor8.tech"
RECOVERY_URL  = f"{CANTOR_BASE}/api/accounts/recovery_v3"
CHALLENGE_URL = f"{CANTOR_BASE}/api/auth/challenge"
LOGIN_URL     = f"{CANTOR_BASE}/api/auth/login"
BALANCE_URL   = f"{CANTOR_BASE}/api/balance"
OFFERS_URL    = f"{CANTOR_BASE}/api/offers_v2"
PREPARE_URL   = f"{CANTOR_BASE}/api/transfer/prepare"
EXECUTE_URL   = f"{CANTOR_BASE}/api/transaction/execute"

VECTOR_BASE       = "https://api.vectornine.tech"
NONCE_URL         = f"{VECTOR_BASE}/auth/nonce"
SIGN_URL          = f"{VECTOR_BASE}/auth/signature"
ACTIVE_ORDER_URL  = f"{VECTOR_BASE}/orders/active"
QUOTES_URL        = f"{VECTOR_BASE}/quotes"
ORDERS_URL        = f"{VECTOR_BASE}/orders"
LEADERBOARD_URL   = f"{VECTOR_BASE}/leaderboard"

# ─── Human-Like Headers ───
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
]

ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.8",
    "en;q=0.9,id;q=0.8",
    "en-US,en;q=0.9,ms;q=0.8",
]

CANTOR_ORIGINS = [
    "https://wallet.cantor8.tech",
]

VECTOR_ORIGINS = [
    "https://exchange.cantor8.tech",
]
