import requests
import hashlib
import base64
import datetime
import os
import time
import random
import secrets
import string
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from bip_utils import Bip39SeedGenerator, Bip32Secp256k1
from nacl.signing import SigningKey
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

# ═══════════════════════════════════════════════════
#  HUMAN-LIKE SESSION & PROXY ROTATION
# ═══════════════════════════════════════════════════

class HumanSession:
    """Requests session with randomized headers and proxy rotation."""

    def __init__(self, proxy_list=None):
        self.proxy_list = proxy_list or []
        self.failed_proxies = set()
        self.session = requests.Session()
        self._attach_retry()
        self._rotate_identity()
        self._rotate_proxy()

    def _attach_retry(self):
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _rotate_identity(self):
        """Randomize headers to appear human."""
        ua = random.choice(config.USER_AGENTS)
        al = random.choice(config.ACCEPT_LANGUAGES)
        origin_c = random.choice(config.CANTOR_ORIGINS)
        origin_v = random.choice(config.VECTOR_ORIGINS)

        self.cantor_headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "origin": origin_c,
            "referer": origin_c + "/",
            "user-agent": ua,
            "accept-language": al,
            "sec-ch-ua": '"Not.A/Brand";v="8", "Chromium";v="124", "Google Chrome";v="124"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
        }

        self.vector_headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "origin": origin_v,
            "referer": origin_v + "/",
            "user-agent": ua,
            "accept-language": al,
            "sec-ch-ua": '"Not.A/Brand";v="8", "Chromium";v="124", "Google Chrome";v="124"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
        }

    def _format_proxy(self, p):
        p = p.strip()
        if p.startswith("http://") or p.startswith("https://"):
            return p

        # Format: user:pass@ip:port
        if "@" in p:
            auth, host = p.split("@", 1)
            return f"http://{auth}@{host}"

        # Format: ip:port:user:pass
        parts = p.split(":")
        if len(parts) == 4:
            ip, port, user, pwd = parts
            return f"http://{user}:{pwd}@{ip}:{port}"

        # Format: ip:port (no auth)
        return f"http://{p}"

    def _rotate_proxy(self):
        if not self.proxy_list:
            return
        available = [p for p in self.proxy_list if p not in self.failed_proxies]
        if not available:
            self.failed_proxies.clear()
            available = self.proxy_list
        chosen = random.choice(available)
        proxy_url = self._format_proxy(chosen)
        self.session.proxies.update({"http": proxy_url, "https": proxy_url})
        self._current_proxy = proxy_url
        self._current_raw = chosen

    def mark_proxy_failed(self):
        if hasattr(self, '_current_raw'):
            self.failed_proxies.add(self._current_raw)
        self._rotate_proxy()

    def post(self, url, headers=None, json=None, timeout=30, use_cantor=True):
        time.sleep(random.uniform(0.3, 1.2))  # human delay
        h = headers or (self.cantor_headers if use_cantor else self.vector_headers)
        try:
            r = self.session.post(url, headers=h, json=json, timeout=timeout)
            if r.status_code == 429:
                time.sleep(random.uniform(2, 5))
                r = self.session.post(url, headers=h, json=json, timeout=timeout)
            return r
        except (requests.exceptions.ProxyError, requests.exceptions.SSLError):
            self.mark_proxy_failed()
            raise

    def get(self, url, headers=None, params=None, timeout=30, use_cantor=True):
        time.sleep(random.uniform(0.2, 0.8))
        h = headers or (self.cantor_headers if use_cantor else self.vector_headers)
        try:
            r = self.session.get(url, headers=h, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(random.uniform(2, 5))
                r = self.session.get(url, headers=h, params=params, timeout=timeout)
            return r
        except (requests.exceptions.ProxyError, requests.exceptions.SSLError):
            self.mark_proxy_failed()
            raise

    def close(self):
        self.session.close()


# ═══════════════════════════════════════════════════
#  KEYPAIR & AUTH
# ═══════════════════════════════════════════════════

def build_keypair_from_mnemonic(mnemonic: str):
    seed = Bip39SeedGenerator(mnemonic.strip()).Generate()
    root = Bip32Secp256k1.FromSeed(seed)
    child = root.DerivePath("m/501'/800245900'/0'/0'/0'")
    signing_key = SigningKey(child.PrivateKey().Raw().ToBytes())
    pub = signing_key.verify_key.encode()
    party_id = f"{hashlib.sha256(pub).hexdigest()}::1220{pub.hex()}"
    return signing_key, party_id


def derive_pubkeys_for_recovery(mnemonic, count=20):
    seed = Bip39SeedGenerator(mnemonic.strip()).Generate()
    root = Bip32Secp256k1.FromSeed(seed)
    keys = []
    for i in range(count):
        child = root.DerivePath(f"m/501'/800245900'/0'/0'/{i}'")
        signing_key = SigningKey(child.PrivateKey().Raw().ToBytes())
        pub = signing_key.verify_key.encode()
        keys.append(pub.hex())
    return keys


def get_party_id(hs: HumanSession, mnemonic):
    pubkeys = derive_pubkeys_for_recovery(mnemonic)
    r = hs.post(config.RECOVERY_URL, json={"public_keys": pubkeys}, timeout=60)
    if r.status_code != 200:
        return None
    results = r.json().get("results", [])
    for acc in results:
        if acc and acc.get("party_id"):
            return acc["party_id"]
    return None


def cantor_login(hs: HumanSession, party_id, signing_key, max_retry=3):
    for attempt in range(max_retry):
        try:
            r = hs.post(config.CHALLENGE_URL, json={"party_id": party_id})
            data = r.json()
            if "challenge" not in data:
                raise ValueError(f"No 'challenge' in response: {data}")
            challenge = data["challenge"]
            signature = signing_key.sign(challenge.encode()).signature.hex()
            r = hs.post(config.LOGIN_URL, json={
                "party_id": party_id,
                "challenge": challenge,
                "signature": signature
            })
            login_data = r.json()
            if "access_token" not in login_data:
                raise ValueError(f"No 'access_token' in response: {login_data}")
            return login_data["access_token"]
        except Exception as e:
            if attempt < max_retry - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"cantor_login failed after {max_retry} attempts: {e}")


def vector_login(hs: HumanSession, canton_address, max_retry=3):
    for attempt in range(max_retry):
        try:
            nonce_resp = hs.get(config.NONCE_URL, use_cantor=False).json()
            if "nonce" not in nonce_resp:
                raise ValueError(f"No 'nonce' in response: {nonce_resp}")
            nonce = nonce_resp["nonce"]
            r = hs.post(config.SIGN_URL, json={"nonce": nonce, "cantonAddress": canton_address}, use_cantor=False)
            data = r.json()
            if "accessToken" not in data:
                raise ValueError(f"No 'accessToken' in response: {data}")
            return data["accessToken"]
        except Exception as e:
            if attempt < max_retry - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"vector_login failed after {max_retry} attempts: {e}")


# ═══════════════════════════════════════════════════
#  BALANCE & LEADERBOARD
# ═══════════════════════════════════════════════════

def get_balance(hs: HumanSession, token):
    r = hs.get(config.BALANCE_URL, headers={**hs.cantor_headers, "authorization": f"Bearer {token}"})
    holdings = r.json().get("holdings", {})
    canton = float(holdings.get("Amulet", {}).get("balance", 0) or 0)
    usdcx  = float(holdings.get("USDCx", {}).get("balance", 0) or 0)
    ceth   = float(holdings.get("cETH", {}).get("balance", 0) or 0)
    return canton, usdcx, ceth


def get_leaderboard(hs: HumanSession, party_id):
    r = hs.get(config.LEADERBOARD_URL, params={
        "limit": 50,
        "address": party_id,
        "includeRewards": "true",
        "includeAll": "true"
    }, use_cantor=False)
    if r.status_code != 200:
        return None
    return r.json()


def get_leaderboard_month(hs: HumanSession, party_id):
    month_start = datetime.datetime.utcnow().strftime("%Y-%m-01")
    r = hs.get(config.LEADERBOARD_URL, params={
        "limit": 1,
        "address": party_id,
        "includeRewards": "true",
        "rewardDateFrom": month_start,
        "includeAll": "true"
    }, use_cantor=False)
    if r.status_code != 200:
        return None
    return r.json()


def safe_leaderboard_range(hs: HumanSession, party_id, d1, d2):
    r1 = get_leaderboard_range(hs, party_id, d1, d2)
    time.sleep(0.5)
    r2 = get_leaderboard_range(hs, party_id, d1, d2)

    def extract(r):
        if r and r.get("requestedAddress"):
            a = r["requestedAddress"]
            return (int(a.get("rewardSwapCount", 0) or 0), float(a.get("rewardVolumeUsd", 0) or 0))
        return (0, 0)

    tx1, vol1 = extract(r1)
    tx2, vol2 = extract(r2)
    return max(tx1, tx2), max(vol1, vol2)


def get_leaderboard_range(hs: HumanSession, party_id, date_from, date_to):
    r = hs.get(config.LEADERBOARD_URL, params={
        "limit": 1,
        "address": party_id,
        "includeRewards": "true",
        "rewardDateFrom": date_from,
        "rewardDateTo": date_to,
        "includeAll": "true"
    }, use_cantor=False)
    if r.status_code != 200:
        return None
    return r.json()


# ═══════════════════════════════════════════════════
#  QUOTES & RATES
# ═══════════════════════════════════════════════════

def get_cc_rate(hs: HumanSession, from_asset, send_amount):
    if send_amount <= 0:
        return 0.0
    try:
        time.sleep(0.3 + random.uniform(0.1, 0.3))
        payload = {
            "fromChain": "CC",
            "fromAsset": from_asset,
            "toChain": "CC",
            "toAsset": "0x0",
            "sendAmount": str(send_amount),
        }
        r = hs.post(config.QUOTES_URL, json=payload, timeout=15, use_cantor=False)
        if r.status_code == 200:
            return float(r.json().get("receiveAmount", 0))
    except Exception:
        pass
    return 0.0


def safe_get_rate(hs, asset, amount):
    if amount <= 0:
        return 0.0
    ref_amount = 5.0 if asset == "USDCX" else (0.01 if asset == "cETH" else amount)
    receive = get_cc_rate(hs, asset, ref_amount)
    if receive > 0:
        rate = receive / ref_amount
        return rate * amount
    return 0.0


def get_reverse_rate(hs: HumanSession, to_asset):
    try:
        time.sleep(0.3 + random.uniform(0.1, 0.3))
        test_amount = 10 if to_asset == "USDCX" else (50 if to_asset == "cETH" else 10)
        payload = {
            "fromChain": "CC",
            "fromAsset": "0x0",
            "toChain": "CC",
            "toAsset": to_asset,
            "sendAmount": str(test_amount),
        }
        r = hs.post(config.QUOTES_URL, json=payload, timeout=15, use_cantor=False)
        if r.status_code == 200:
            receive = float(r.json().get("receiveAmount", 0))
            if receive > 0:
                return receive / test_amount
    except Exception:
        pass
    return 0.0


# ═══════════════════════════════════════════════════
#  CHECKER (dari lb3.py)
# ═══════════════════════════════════════════════════

def check_account(idx, mnemonic, proxy_list):
    for attempt in range(3):
        hs = HumanSession(proxy_list)
        try:
            party_id = get_party_id(hs, mnemonic)
            if not party_id:
                hs.close()
                continue
            signing_key, _ = build_keypair_from_mnemonic(mnemonic)
            token = cantor_login(hs, party_id, signing_key)
            now = datetime.datetime.utcnow()
            day = now.day

            canton, usdc, ceth = get_balance(hs, token)
            lb_total = get_leaderboard(hs, party_id)
            time.sleep(0.3)
            lb_month = get_leaderboard_month(hs, party_id)
            time.sleep(0.3)

            month_start = now.strftime("%Y-%m-01")
            month_15 = now.strftime("%Y-%m-15")

            vol = tx = reward = 0
            tx_month = vol_month = tx_range = vol_range = reward_month = 0

            if lb_total and lb_total.get("requestedAddress"):
                addr = lb_total["requestedAddress"]
                vol = float(addr.get("volumeUsd", 0) or 0)
                tx = int(addr.get("swapCount", 0) or 0)
                reward = float(addr.get("rewardAccruedCc", 0) or 0)

            if lb_month and lb_month.get("requestedAddress"):
                addr_m = lb_month["requestedAddress"]
                tx_month = int(addr_m.get("rewardSwapCount", 0) or 0)
                vol_month = float(addr_m.get("rewardVolumeUsd", 0) or 0)
                reward_month = float(addr_m.get("rewardAccruedCc", 0) or 0)

            if day <= 15:
                tx_range, vol_range = safe_leaderboard_range(hs, party_id, month_start, now.strftime("%Y-%m-%d"))
            else:
                tx_1_15, vol_1_15 = safe_leaderboard_range(hs, party_id, month_start, month_15)
                time.sleep(0.3)
                tx_range = tx_month - tx_1_15
                vol_range = vol_month - vol_1_15
                tx_range = max(0, min(tx_range, tx_month))
                vol_range = max(0, min(vol_range, vol_month))

            short = party_id[:6] + "..." + party_id[-4:]
            is_low = (canton < 11 and usdc < 1.6 and ceth < 0.0007)
            hs.close()
            return {
                "idx": idx,
                "short": short,
                "canton": canton,
                "usdc": usdc,
                "ceth": ceth,
                "vol_range": vol_range,
                "tx_range": tx_range,
                "reward_month": reward_month,
                "reward": reward,
                "is_low": is_low,
            }
        except Exception:
            hs.mark_proxy_failed()
            hs.close()
            continue
    return None


def run_checker(mnemonics, proxy_list, progress_callback=None):
    total_cc = total_usdc = total_ceth = total_reward = 0
    low_accounts = []
    results = []
    max_threads = min(50, len(mnemonics)) if mnemonics else 1

    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        futures = {executor.submit(check_account, i+1, m, proxy_list): i for i, m in enumerate(mnemonics)}
        for f in as_completed(futures):
            res = f.result()
            if res:
                results.append(res)
                total_cc += res["canton"]
                total_usdc += res["usdc"]
                total_ceth += res["ceth"]
                total_reward += res["reward"]
                if res["is_low"]:
                    low_accounts.append(res)
            if progress_callback:
                progress_callback(len(results), len(mnemonics))

    # Estimasi rates
    hs = HumanSession(proxy_list)
    usdcx_cc = ceth_cc = 0
    cc_to_usdc = get_reverse_rate(hs, "USDCX")
    if cc_to_usdc > 0 and total_usdc > 0:
        usdcx_cc = total_usdc * (1 / cc_to_usdc)

    cc_to_ceth = get_reverse_rate(hs, "cETH")
    if cc_to_ceth > 0 and total_ceth > 0.00000001:
        ceth_cc = total_ceth * (1 / cc_to_ceth)

    hs.close()
    grand_total = total_cc + usdcx_cc + ceth_cc + total_reward

    return {
        "accounts": results,
        "low_accounts": low_accounts,
        "total_cc": total_cc,
        "total_usdc": total_usdc,
        "total_ceth": total_ceth,
        "total_reward": total_reward,
        "usdcx_cc": usdcx_cc,
        "ceth_cc": ceth_cc,
        "grand_total": grand_total,
        "cc_to_usdc": cc_to_usdc if total_usdc > 0 else 0,
        "cc_to_ceth": cc_to_ceth if total_ceth > 0 else 0,
    }


# ═══════════════════════════════════════════════════
#  TRADER (dari ceth (1).py)
# ═══════════════════════════════════════════════════

def get_mode_config(mode):
    mode = mode.upper()
    if mode == "BUY":
        return {
            "quote_payload": {"fromChain": "CC", "fromAsset": "CETH", "toChain": "CC", "toAsset": "0x0"},
            "instrument_admin_id": "rails-cethMain-1::12200350ba6e96e3b701c3048b5aa013a8c1c08833e8ebf54339cff581055c29003a",
            "instrument_id": "cETH",
        }
    elif mode == "SELL":
        return {
            "quote_payload": {"fromChain": "CC", "fromAsset": "0x0", "toChain": "CC", "toAsset": "CETH"},
            "instrument_admin_id": "DSO::1220b1431ef217342db44d516bb9befde802be7d8899637d290895fa58880f19accc",
            "instrument_id": "Amulet",
        }
    else:
        raise ValueError("MODE must be BUY or SELL")


def get_quote(hs: HumanSession, vector_token, mode, amount):
    cfg = get_mode_config(mode)
    payload = {**cfg["quote_payload"], "sendAmount": amount}
    r = hs.post(config.QUOTES_URL, headers={**hs.vector_headers, "authorization": f"Bearer {vector_token}"}, json=payload, use_cantor=False)
    return r.json()


def generate_order_id():
    return "ord_" + "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(16))


def create_order(hs: HumanSession, vector_token, quote_id, to_address, max_retry=10):
    for _ in range(max_retry):
        order_id = generate_order_id()
        r = hs.post(config.ORDERS_URL, headers={**hs.vector_headers, "authorization": f"Bearer {vector_token}"}, json={
            "orderId": order_id,
            "quoteId": quote_id,
            "toAddress": to_address
        }, use_cantor=False)

        if r.status_code == 429:
            return "SERVICE_DOWN"
        try:
            order = r.json()
        except Exception:
            continue
        if not isinstance(order, dict):
            continue
        order["generatedOrderId"] = order_id

        if "detail" in order and isinstance(order["detail"], dict) and order["detail"].get("error") == "ORDER_EXISTS_ACTIVE":
            time.sleep(300)
            continue
        if order.get("detail") == "Quote expired":
            return "QUOTE_EXPIRED"
        if "deposit" in order and isinstance(order["deposit"], dict):
            return order
        if isinstance(order, dict) and "detail" in order:
            detail = str(order["detail"]).lower()
            if "temporarily unavailable" in detail or "service" in detail:
                return "SERVICE_DOWN"
        return None
    return None


def prepare_transfer(hs: HumanSession, cantor_token, order, mode):
    cfg = get_mode_config(mode)
    deposit = order["deposit"]
    payload = {
        "instrument_admin_id": cfg["instrument_admin_id"],
        "instrument_id": cfg["instrument_id"],
        "receiver_party_id": deposit["address"],
        "amount": float(order["requiredAmount"]),
        "reason": order["orderId"],
        "app_name": "swap-v1",
        "metadata": {}
    }
    r = hs.post(config.PREPARE_URL, headers={**hs.cantor_headers, "authorization": f"Bearer {cantor_token}"}, json=payload)
    return r.json()


def sign_hash_b64(signing_key, hash_b64: str) -> str:
    hash_bytes = base64.b64decode(hash_b64)
    signature = signing_key.sign(hash_bytes).signature
    return base64.b64encode(signature).decode()


def execute_transaction(hs: HumanSession, cantor_token, prepared, signing_key, mode, order_id):
    before_canton, _, before_ceth = get_balance(hs, cantor_token)
    sig = sign_hash_b64(signing_key, prepared["hash_b64"])
    payload = {
        "command_id": prepared["command_id"],
        "prepared_tx_b64": prepared["prepared_tx_b64"],
        "hashing_scheme_version": prepared["hashing_scheme_version"],
        "signature_b64": sig
    }
    r = hs.post(config.EXECUTE_URL, headers={**hs.cantor_headers, "authorization": f"Bearer {cantor_token}"}, json=payload, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"Execute failed: {r.text}")

    for _ in range(300):
        time.sleep(2)
        after_canton, _, after_ceth = get_balance(hs, cantor_token)
        if mode == "SELL" and (after_ceth - before_ceth) > 0.0001:
            return after_ceth - before_ceth
        if mode == "BUY" and (after_canton - before_canton) > 0.0001:
            return after_canton - before_canton
    return "TIMEOUT_NO_BALANCE_CHANGE"


def get_active_order(hs: HumanSession, vector_token):
    try:
        r = hs.get(config.ACTIVE_ORDER_URL, headers={**hs.vector_headers, "authorization": f"Bearer {vector_token}"}, timeout=120, use_cantor=False)
    except Exception:
        return "ERROR"
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        return "ERROR"
    return r.json()


def cancel_order(hs: HumanSession, vector_token, order_id):
    try:
        url = f"{config.VECTOR_BASE}/orders/{order_id}/cancel"
        r = hs.post(url, headers={**hs.vector_headers, "authorization": f"Bearer {vector_token}"}, timeout=30, use_cantor=False)
        if r.status_code != 200:
            return False
        return r.json().get("status") == "CANCELLED"
    except Exception:
        return False


def wait_until_no_active_order(hs: HumanSession, vector_token, timeout_seconds=300):
    start_time = time.time()
    active = get_active_order(hs, vector_token)
    if active == "ERROR":
        time.sleep(5)
        return False
    if not active:
        return True
    last_order_id = active.get("orderId")
    while True:
        elapsed = time.time() - start_time
        if elapsed >= timeout_seconds:
            if last_order_id:
                cancel_order(hs, vector_token, last_order_id)
                for _ in range(10):
                    time.sleep(2)
                    if not get_active_order(hs, vector_token):
                        break
            return False
        time.sleep(5)
        active = get_active_order(hs, vector_token)
        if active == "ERROR":
            continue
        if not active:
            return True
        current_id = active.get("orderId")
        if current_id != last_order_id:
            last_order_id = current_id


def safe_create_prepare_execute(hs: HumanSession, cantor_token, vector_token, signing_key, mode, amount, party_id):
    if not wait_until_no_active_order(hs, vector_token):
        return "SKIP_CYCLE"
    step = 0.05 if mode == "SELL" else 0.000001
    for attempt in range(2):
        adj_amount = round(float(amount) - (step if attempt == 1 else 0), 6)
        if adj_amount <= 0:
            return None
        order = create_order_with_fresh_quote(hs, vector_token, mode, str(adj_amount), party_id)
        if order == "SERVICE_DOWN":
            return "SERVICE_DOWN"
        if not order or not isinstance(order, dict):
            return None
        prepared = prepare_transfer(hs, cantor_token, order, mode)
        if not prepared or "hash_b64" not in prepared:
            continue
        result = execute_transaction(hs, cantor_token, prepared, signing_key, mode, order.get("generatedOrderId", "UNKNOWN"))
        if result == "TIMEOUT_NO_BALANCE_CHANGE":
            return "SKIP_CYCLE"
        return result
    return "SKIP_CYCLE"


def create_order_with_fresh_quote(hs, vector_token, mode, amount, party_id):
    amount = float(amount)
    step = 0.05 if mode == "SELL" else 0.000001
    for i in range(5):
        adj_amount = round(amount - (i * step), 6)
        if adj_amount <= 0:
            return None
        quote = get_quote(hs, vector_token, mode, str(adj_amount))
        if not isinstance(quote, dict) or "quoteId" not in quote:
            return None
        order = create_order(hs, vector_token, quote["quoteId"], party_id)
        if order == "QUOTE_EXPIRED":
            time.sleep(30)
            continue
        if order == "SERVICE_DOWN":
            return "SERVICE_DOWN"
        if not order or not isinstance(order, dict):
            return "SKIP_CYCLE"
        return order
    return None


# ═══════════════════════════════════════════════════
#  TRADER WORKER
# ═══════════════════════════════════════════════════

def trader_cycle(hs: HumanSession, mnemonic, party_id, signing_key, status_callback=None):
    def log(msg):
        if status_callback:
            status_callback(msg)

    cantor_token = cantor_login(hs, party_id, signing_key)
    vector_token = vector_login(hs, party_id)
    current_canton, _, current_ceth = get_balance(hs, cantor_token)

    MIN_PREBUY_CETH = 0.00165

    if current_canton < 26:
        log("PRE-BUY mode activated")
        if current_ceth < MIN_PREBUY_CETH:
            log("ERROR: Insufficient cETH for pre-buy. Stopping.")
            return False
        buy_amount = round(max(min(current_ceth - 0.000001, current_ceth), MIN_PREBUY_CETH), 6)
        log(f"Pre-buy using {buy_amount:.6f} cETH")
        result = safe_create_prepare_execute(hs, cantor_token, vector_token, signing_key, "BUY", buy_amount, party_id)
        if result in ("SERVICE_DOWN", "SKIP_CYCLE", None):
            log(f"Pre-buy failed: {result}")
            return False
        time.sleep(1800)
        initial_cc, _, initial_ceth = get_balance(hs, cantor_token)
    else:
        initial_cc, initial_ceth = current_canton, current_ceth

    # SELL
    log("SELL phase started")
    current_canton, _, _ = get_balance(hs, cantor_token)
    if current_canton <= 26:
        log("ERROR: No Canton balance to sell")
        return False
    sell_percent = random.uniform(0.997, 0.998)
    sell_amount = round(max(min(current_canton * sell_percent - 0.05, current_canton), 0), 6)
    log(f"Selling {sell_amount:.6f} CC ({sell_percent*100:.2f}%)")
    ceth_received = safe_create_prepare_execute(hs, cantor_token, vector_token, signing_key, "SELL", sell_amount, party_id)
    if ceth_received in ("SERVICE_DOWN", "SKIP_CYCLE") or not ceth_received:
        log(f"SELL failed: {ceth_received}")
        return False
    time.sleep(1800)

    # BUYBACK
    log("BUYBACK phase started")
    _, _, current_ceth = get_balance(hs, cantor_token)
    if current_ceth <= 0.00001:
        log("ERROR: No CETH to buy back")
        return False
    buy_amount = round(max(current_ceth - 0.000001, 0), 6)
    log(f"Buying back using {buy_amount:.6f} cETH")
    result = safe_create_prepare_execute(hs, cantor_token, vector_token, signing_key, "BUY", buy_amount, party_id)
    if result in ("SERVICE_DOWN", "SKIP_CYCLE") or not result:
        log(f"BUYBACK failed: {result}")
        return False
    time.sleep(1800)

    final_cc, _, final_ceth = get_balance(hs, cantor_token)
    log(f"Cycle complete. CC: {initial_cc:.4f} -> {final_cc:.4f}")
    return True


def run_trader_worker(mnemonic, proxy_list, status_callback=None, stop_event=None):
    def log(msg):
        if status_callback:
            status_callback(msg)

    fail_count = 0
    MAX_FAIL_WAIT = 1800  # max 30 menit

    while True:
        if stop_event and stop_event.is_set():
            return

        hs = HumanSession(proxy_list)
        try:
            party_id = get_party_id(hs, mnemonic)
            if not party_id:
                fail_count += 1
                wait = min(60 * fail_count, MAX_FAIL_WAIT)
                log(f"ERROR: Failed to get party_id. Retry in {wait}s...")
                hs.close()
                time.sleep(wait)
                continue

            signing_key, _ = build_keypair_from_mnemonic(mnemonic)
            cycle_count = 0
            fail_count = 0  # reset setelah berhasil login

            while True:
                if stop_event and stop_event.is_set():
                    hs.close()
                    return

                log(f"Starting trade cycle #{cycle_count + 1}")
                try:
                    success = trader_cycle(hs, mnemonic, party_id, signing_key, status_callback)
                except Exception as cycle_err:
                    log(f"ERROR in cycle: {cycle_err}. Retrying in 5 minutes...")
                    time.sleep(300)
                    # Refresh session setelah error
                    hs.close()
                    hs = HumanSession(proxy_list)
                    continue

                if not success:
                    log("Cycle failed. Retrying in 5 minutes...")
                    time.sleep(300)
                else:
                    cycle_count += 1
                    fail_count = 0

                if cycle_count > 0 and cycle_count % 20 == 0:
                    hs.close()
                    hs = HumanSession(proxy_list)

        except Exception as e:
            fail_count += 1
            wait = min(120 * fail_count, MAX_FAIL_WAIT)
            log(f"ERROR: {e}. Restart in {wait}s... (attempt {fail_count})")
            hs.close()
            time.sleep(wait)

# ═══════════════════════════════════════════════════
#  BATCH TRADER RUNNER (5 wallet per batch)
# ═══════════════════════════════════════════════════

def run_trader_batch(mnemonics, proxy_list, batch_size=5, batch_delay=(30, 60),
                     status_callback=None, stop_event=None):
    """
    Run traders in batches of `batch_size` wallets.
    Wait random `batch_delay` seconds before starting next batch.
    """
    total = len(mnemonics)
    active_threads = []
    active_events = []

    for batch_start in range(0, total, batch_size):
        if stop_event and stop_event.is_set():
            break

        batch_end = min(batch_start + batch_size, total)
        batch = mnemonics[batch_start:batch_end]

        if status_callback:
            status_callback(f"Batch starting: wallets {batch_start+1}-{batch_end} (size: {len(batch)})")

        for i, mnemonic in enumerate(batch):
            global_idx = batch_start + i
            if stop_event and stop_event.is_set():
                break

            ev = threading.Event()
            active_events.append(ev)

            def make_cb(idx):
                def cb(msg):
                    if status_callback:
                        status_callback(f"[Wallet {idx+1}] {msg}")
                return cb

            t = threading.Thread(
                target=run_trader_worker,
                args=(mnemonic, proxy_list, make_cb(global_idx), ev),
                daemon=True
            )
            t.start()
            active_threads.append((global_idx, t))

        # Jika masih ada batch berikutnya, delay
        if batch_end < total:
            delay = random.randint(batch_delay[0], batch_delay[1])
            if status_callback:
                status_callback(f"Batch {batch_start+1}-{batch_end} launched. Waiting {delay}s before next batch...")

            # Sleep dengan cek stop_event setiap detik
            for _ in range(delay):
                if stop_event and stop_event.is_set():
                    break
                time.sleep(1)

    if status_callback:
        status_callback(f"All batches launched. Total wallets: {total}")

    return active_threads, active_events
