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
            status_forcelist=[500, 502, 503, 504],  # 429 di-handle manual per endpoint
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

    def _is_proxy_error(self, e):
        """Deteksi semua jenis error yang disebabkan proxy."""
        if isinstance(e, (requests.exceptions.ProxyError, requests.exceptions.SSLError)):
            return True
        if isinstance(e, requests.exceptions.ConnectionError):
            msg = str(e).lower()
            if any(x in msg for x in ["429", "not enough connections", "tunnel connection failed", "proxy"]):
                return True
        return False

    def post(self, url, headers=None, json=None, timeout=30, use_cantor=True):
        time.sleep(random.uniform(0.3, 1.2))  # human delay
        h = headers or (self.cantor_headers if use_cantor else self.vector_headers)
        try:
            r = self.session.post(url, headers=h, json=json, timeout=timeout)
            if r.status_code == 429:
                wait = random.uniform(10, 20)
                time.sleep(wait)
                r = self.session.post(url, headers=h, json=json, timeout=timeout)
            return r
        except Exception as e:
            if self._is_proxy_error(e):
                self.mark_proxy_failed()
            raise

    def get(self, url, headers=None, params=None, timeout=30, use_cantor=True):
        time.sleep(random.uniform(0.2, 0.8))
        h = headers or (self.cantor_headers if use_cantor else self.vector_headers)
        try:
            r = self.session.get(url, headers=h, params=params, timeout=timeout)
            if r.status_code == 429:
                wait = random.uniform(10, 20)
                time.sleep(wait)
                r = self.session.get(url, headers=h, params=params, timeout=timeout)
            return r
        except Exception as e:
            if self._is_proxy_error(e):
                self.mark_proxy_failed()
            raise

    def close(self):
        self.session.close()


# ═══════════════════════════════════════════════════
#  PROXY TEST
# ═══════════════════════════════════════════════════

def test_proxy(proxy_url: str, timeout: int = 8) -> bool:
    """Test apakah proxy bisa konek ke server Cantor."""
    try:
        r = requests.get(
            config.CANTOR_BASE + "/",
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=timeout
        )
        return r.status_code in [200, 401, 404]
    except Exception:
        return False


# ═══════════════════════════════════════════════════
#  DAILY UTC RANGE
# ═══════════════════════════════════════════════════

def get_daily_range_utc():
    """Return (date_from, date_to) untuk hari ini UTC."""
    import datetime as _dt
    now_utc = _dt.datetime.utcnow()
    start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + _dt.timedelta(days=1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


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
    """
    Ambil party_id dari Cantor.
    Cukup kirim 1 pubkey (dari derivation path utama), bukan 20 seperti sebelumnya.
    """
    signing_key, party_id_unused = build_keypair_from_mnemonic(mnemonic)
    pub_hex = signing_key.verify_key.encode().hex()
    r = hs.post(config.RECOVERY_URL, json={"public_keys": [pub_hex]}, timeout=60)
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
#  POST-LOGIN STEPS (dari script referensi)
# ═══════════════════════════════════════════════════

def post_confirm(hs: HumanSession, cantor_token, max_retry=3):
    """
    Cek transaksi pending yang butuh tanda tangan setelah login.
    Endpoint: POST /api/register/post_confirm_v2
    Return: list tx atau [] jika tidak ada, False jika error.
    """
    for attempt in range(max_retry):
        try:
            r = hs.post(
                config.CONFIRM_URL,
                headers={**hs.cantor_headers, "authorization": f"Bearer {cantor_token}"},
                json={}
            )
            if r.status_code >= 500:
                raise RuntimeError(f"Server error {r.status_code}")
            if r.status_code != 200:
                return False
            return r.json().get("transactions_to_sign", [])
        except Exception as e:
            if attempt < max_retry - 1:
                time.sleep(2 ** attempt)
                continue
            return False


def finalise_transaction(hs: HumanSession, cantor_token, signing_key, txs: list, max_retry=3):
    """
    Tanda tangani dan kirim transaksi pending dari post_confirm.
    Endpoint: POST /api/register/finalise_v3
    """
    signed = [
        {**tx, "signature_b64": sign_hash_b64(signing_key, tx["hash_b64"])}
        for tx in txs
    ]
    for attempt in range(max_retry):
        try:
            r = hs.post(
                config.FINALISE_URL,
                headers={**hs.cantor_headers, "authorization": f"Bearer {cantor_token}"},
                json={"signed_transactions": signed}
            )
            if r.status_code != 200:
                raise RuntimeError(f"Failed {r.status_code}")
            return r.json().get("party_id")
        except Exception as e:
            if attempt < max_retry - 1:
                time.sleep(2 ** attempt)
                continue
            return False


OFFER_INSTRUMENT_IDS = {"Amulet", "USDCx", "cETH"}

def check_and_accept_offers(hs: HumanSession, cantor_token, signing_key, max_retry=3):
    """
    Cek dan accept open offers setelah login.
    Endpoint: GET /api/offers_v2 → POST /api/transaction/execute (tiap offer)
    Return: True kalau ada offer yang di-accept (bot harus skip trade cycle ini).
    """
    for attempt in range(max_retry):
        try:
            r = hs.get(
                config.OFFERS_URL,
                headers={**hs.cantor_headers, "authorization": f"Bearer {cantor_token}"}
            )
            if r.status_code != 200:
                return False
            offers = r.json().get("offers", [])
            break
        except Exception:
            if attempt < max_retry - 1:
                time.sleep(2 ** attempt)
                continue
            return False

    accepted = False
    for offer in offers:
        if offer.get("instrument_id") not in OFFER_INSTRUMENT_IDS:
            continue
        try:
            accept_data = offer.get("accept", {})
            if not accept_data or "hash_b64" not in accept_data:
                continue
            payload = {
                "command_id":             accept_data["command_id"],
                "prepared_tx_b64":        accept_data["prepared_tx_b64"],
                "hashing_scheme_version": accept_data["hashing_scheme_version"],
                "signature_b64":          sign_hash_b64(signing_key, accept_data["hash_b64"]),
            }
            r = hs.post(
                config.EXECUTE_URL,
                headers={**hs.cantor_headers, "authorization": f"Bearer {cantor_token}"},
                json=payload
            )
            if r.status_code == 200:
                accepted = True
        except Exception:
            continue
    return accepted


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
    """Return (tx, vol, reward) untuk range tanggal tertentu."""
    r = get_leaderboard_range(hs, party_id, d1, d2)
    if r and r.get("requestedAddress"):
        a = r["requestedAddress"]
        tx     = int(a.get("rewardSwapCount", 0) or 0)
        vol    = float(a.get("rewardVolumeUsd", 0) or 0)
        reward = float(a.get("rewardAccruedCc", 0) or 0)
        return tx, vol, reward
    return 0, 0, 0


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
    """
    Estimasi nilai asset dalam CC.
    cETH sekarang dikonversi via USDCx (pair baru), bukan langsung ke CC.
    """
    if amount <= 0:
        return 0.0

    if asset == "cETH":
        # cETH → USDCx → CC (2 hop)
        ref_ceth = 0.01
        usdc_recv = get_cc_rate(hs, "cETH", ref_ceth)   # rate cETH/USDCx
        if usdc_recv <= 0:
            return 0.0
        ceth_to_usdc_rate = usdc_recv / ref_ceth
        total_usdc = amount * ceth_to_usdc_rate

        ref_usdc = 5.0
        cc_recv = get_cc_rate(hs, "USDCX", ref_usdc)    # rate USDCx/CC
        if cc_recv <= 0:
            return 0.0
        usdc_to_cc_rate = cc_recv / ref_usdc
        return total_usdc * usdc_to_cc_rate

    else:
        # USDCx atau aset lain — langsung ke CC
        ref_amount = 5.0 if asset == "USDCX" else amount
        receive = get_cc_rate(hs, asset, ref_amount)
        if receive > 0:
            return (receive / ref_amount) * amount
        return 0.0


def get_reverse_rate(hs: HumanSession, to_asset):
    """
    Estimasi berapa unit to_asset yang didapat per 1 CC.
    cETH sekarang via USDCx.
    """
    try:
        time.sleep(0.3 + random.uniform(0.1, 0.3))

        if to_asset == "cETH":
            # CC → USDCx → cETH (2 hop, pakai rate masing-masing)
            test_cc = 10
            payload_cc_usdc = {
                "fromChain": "CC", "fromAsset": "0x0",
                "toChain":   "CC", "toAsset":   "USDCX",
                "sendAmount": str(test_cc),
            }
            r1 = hs.post(config.QUOTES_URL, json=payload_cc_usdc, timeout=15, use_cantor=False)
            if r1.status_code != 200:
                return 0.0
            usdc_recv = float(r1.json().get("receiveAmount", 0))
            if usdc_recv <= 0:
                return 0.0

            payload_usdc_ceth = {
                "fromChain": "CC", "fromAsset": "USDCX",
                "toChain":   "CC", "toAsset":   "CETH",
                "sendAmount": str(usdc_recv),
            }
            r2 = hs.post(config.QUOTES_URL, json=payload_usdc_ceth, timeout=15, use_cantor=False)
            if r2.status_code != 200:
                return 0.0
            ceth_recv = float(r2.json().get("receiveAmount", 0))
            if ceth_recv > 0:
                return ceth_recv / test_cc   # unit cETH per 1 CC
            return 0.0

        else:
            # USDCX atau aset lain — langsung
            test_amount = 10 if to_asset == "USDCX" else 10
            payload = {
                "fromChain": "CC", "fromAsset": "0x0",
                "toChain":   "CC", "toAsset":   to_asset,
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
            # ===== TEST PROXY DULU =====
            if proxy_list and hasattr(hs, '_current_proxy'):
                if not test_proxy(hs._current_proxy):
                    hs.mark_proxy_failed()
                    hs.close()
                    continue

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

            # ===== DAILY TRACKING =====
            daily_from, daily_to = get_daily_range_utc()
            daily_tx, daily_vol, daily_reward = safe_leaderboard_range(
                hs, party_id, daily_from, daily_to
            )
            time.sleep(0.2)

            month_start = now.strftime("%Y-%m-01")
            month_15    = now.strftime("%Y-%m-15")

            vol = tx = reward = 0
            tx_month = vol_month = reward_month = 0
            tx_range = vol_range = reward_range = 0

            if lb_total and lb_total.get("requestedAddress"):
                addr = lb_total["requestedAddress"]
                vol    = float(addr.get("volumeUsd", 0) or 0)
                tx     = int(addr.get("swapCount", 0) or 0)
                reward = float(addr.get("rewardAccruedCc", 0) or 0)

            if lb_month and lb_month.get("requestedAddress"):
                addr_m = lb_month["requestedAddress"]
                tx_month     = int(addr_m.get("rewardSwapCount", 0) or 0)
                vol_month    = float(addr_m.get("rewardVolumeUsd", 0) or 0)
                reward_month = float(addr_m.get("rewardAccruedCc", 0) or 0)

            # ===== REWARD RANGE PER PERIODE =====
            if day <= 15:
                tx_range, vol_range, reward_range = safe_leaderboard_range(
                    hs, party_id, month_start, now.strftime("%Y-%m-%d")
                )
            else:
                tx_1_15, vol_1_15, reward_1_15 = safe_leaderboard_range(
                    hs, party_id, month_start, month_15
                )
                time.sleep(0.3)
                tx_range     = max(0, min(tx_month - tx_1_15, tx_month))
                vol_range    = max(0, min(vol_month - vol_1_15, vol_month))
                reward_range = max(0, min(reward_month - reward_1_15, reward_month))

            short  = party_id[:6] + "..." + party_id[-4:]
            # Low balance: CC rendah DAN tidak ada USDCx/cETH yang bisa di-recover
            is_low = (canton < 11 and usdc < 0.5 and ceth < 0.0005)
            hs.close()
            return {
                "idx":          idx,
                "short":        short,
                "canton":       canton,
                "usdc":         usdc,
                "ceth":         ceth,
                "vol_range":    vol_range,
                "tx_range":     tx_range,
                "reward_range": reward_range,
                "daily_tx":     daily_tx,
                "daily_vol":    daily_vol,
                "daily_reward": daily_reward,
                "reward_month": reward_month,
                "reward":       reward,
                "is_low":       is_low,
            }
        except Exception:
            hs.mark_proxy_failed()
            hs.close()
            continue
    return None


def run_checker(mnemonics, proxy_list, progress_callback=None):
    total_cc = total_usdc = total_ceth = total_reward = 0
    total_reward_range = total_tx_range = 0
    total_daily_tx = total_daily_reward = 0
    low_accounts = []
    results = []
    max_threads = min(50, len(mnemonics)) if mnemonics else 1

    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        futures = {executor.submit(check_account, i+1, m, proxy_list): i for i, m in enumerate(mnemonics)}
        for f in as_completed(futures):
            res = f.result()
            if res:
                results.append(res)
                total_cc           += res["canton"]
                total_usdc         += res["usdc"]
                total_ceth         += res["ceth"]
                total_reward       += res["reward"]
                total_reward_range += res["reward_range"]
                total_tx_range     += res["tx_range"]
                total_daily_tx     += res["daily_tx"]
                total_daily_reward += res["daily_reward"]
                if res["is_low"]:
                    low_accounts.append(res)
            if progress_callback:
                progress_callback(len(results), len(mnemonics))

    # Estimasi rates
    # cETH sekarang dikonversi via USDCx (pair CC/cETH sudah paused)
    hs = HumanSession(proxy_list)
    usdcx_cc = ceth_cc = 0
    cc_to_usdc = get_reverse_rate(hs, "USDCX")
    if cc_to_usdc > 0 and total_usdc > 0:
        usdcx_cc = total_usdc * (1 / cc_to_usdc)

    # cETH → USDCx → CC (2 hop)
    cc_to_ceth = get_reverse_rate(hs, "cETH")  # sudah 2-hop di dalam fungsi
    if cc_to_ceth > 0 and total_ceth > 0.00000001:
        ceth_cc = total_ceth * (1 / cc_to_ceth)

    hs.close()
    grand_total = total_cc + usdcx_cc + ceth_cc + total_reward

    return {
        "accounts":          results,
        "low_accounts":      low_accounts,
        "total_cc":          total_cc,
        "total_usdc":        total_usdc,
        "total_ceth":        total_ceth,
        "total_reward":      total_reward,
        "total_reward_range": total_reward_range,
        "total_tx_range":    total_tx_range,
        "total_daily_tx":    total_daily_tx,
        "total_daily_reward": total_daily_reward,
        "usdcx_cc":          usdcx_cc,
        "ceth_cc":           ceth_cc,
        "grand_total":       grand_total,
        "cc_to_usdc":        cc_to_usdc if total_usdc > 0 else 0,
        "cc_to_ceth":        cc_to_ceth if total_ceth > 0 else 0,
    }


# ═══════════════════════════════════════════════════
#  TRADER
#  UPDATE 2025-05-20: CC/cETH pair PAUSED oleh Cantor8.
#  Pair utama cETH sekarang: cETH/USDCx.
#  Flow baru: CC → USDCx → cETH → USDCx → CC (4 langkah/cycle).
# ═══════════════════════════════════════════════════

# ── Instrument Admin IDs ─────────────────────────────────────────────────────
# Nilai ini diambil dari response API Cantor saat prepare_transfer.
# Jika swap gagal di tahap prepare, kemungkinan ID ini berubah — update dari
# network inspector browser (F12 → Network → cari /transfers/prepare).
_ADMIN_DSO       = "DSO::1220b1431ef217342db44d516bb9befde802be7d8899637d290895fa58880f19accc"
_ADMIN_CETH      = "rails-cethMain-1::12200350ba6e96e3b701c3048b5aa013a8c1c08833e8ebf54339cff581055c29003a"
_ADMIN_USDCX     = "decentralized-usdc-interchain-rep::12208115f1e168dd7e792320be9c4ca720c751a02a3053c7606e1c1cd3dad9bf60ef"


def get_mode_config(mode):
    """
    Mode trading yang tersedia (pair CC/cETH sudah PAUSED):

      CC_TO_USDC   : CC → USDCx   (kirim CC, terima USDCx)
      USDC_TO_CETH : USDCx → cETH (kirim USDCx, terima cETH)  ← pair baru
      CETH_TO_USDC : cETH → USDCx (kirim cETH, terima USDCx)  ← pair baru
      USDC_TO_CC   : USDCx → CC   (kirim USDCx, terima CC)

    receive_asset dipakai oleh execute_transaction untuk polling balance.
    """
    mode = mode.upper()

    if mode == "CC_TO_USDC":
        return {
            "quote_payload": {
                "fromChain": "CC", "fromAsset": "0x0",
                "toChain":   "CC", "toAsset":   "USDCX",
            },
            "instrument_admin_id": _ADMIN_DSO,
            "instrument_id":       "Amulet",
            "receive_asset":       "usdc",
        }

    elif mode == "USDC_TO_CETH":
        return {
            "quote_payload": {
                "fromChain": "CC", "fromAsset": "USDCX",
                "toChain":   "CC", "toAsset":   "CETH",
            },
            "instrument_admin_id": _ADMIN_USDCX,
            "instrument_id":       "USDCx",
            "receive_asset":       "ceth",
        }

    elif mode == "CETH_TO_USDC":
        return {
            "quote_payload": {
                "fromChain": "CC", "fromAsset": "CETH",
                "toChain":   "CC", "toAsset":   "USDCX",
            },
            "instrument_admin_id": _ADMIN_CETH,
            "instrument_id":       "cETH",
            "receive_asset":       "usdc",
        }

    elif mode == "USDC_TO_CC":
        return {
            "quote_payload": {
                "fromChain": "CC", "fromAsset": "USDCX",
                "toChain":   "CC", "toAsset":   "0x0",
            },
            "instrument_admin_id": _ADMIN_USDCX,
            "instrument_id":       "USDCx",
            "receive_asset":       "canton",
        }

    # ── Legacy (PAUSED) — tetap ada agar tidak error jika dipanggil ──────────
    elif mode == "BUY":
        raise RuntimeError(
            "Mode BUY (cETH→CC) sudah PAUSED oleh Cantor8 sejak 20 Mei 2025. "
            "Gunakan USDC_TO_CC atau CETH_TO_USDC."
        )
    elif mode == "SELL":
        raise RuntimeError(
            "Mode SELL (CC→cETH) sudah PAUSED oleh Cantor8 sejak 20 Mei 2025. "
            "Gunakan CC_TO_USDC atau USDC_TO_CETH."
        )

    else:
        raise ValueError(f"MODE tidak dikenal: {mode}. Pilihan: CC_TO_USDC, USDC_TO_CETH, CETH_TO_USDC, USDC_TO_CC")


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


ORDER_TERMINAL_SUCCESS = {"COMPLETED"}
ORDER_TERMINAL_FAIL    = {"CANCELLED", "FAILED", "MARKED_FOR_REFUND", "REFUNDING", "REFUNDED"}
ORDER_TERMINAL         = ORDER_TERMINAL_SUCCESS | ORDER_TERMINAL_FAIL


def wait_for_order_completion(hs: HumanSession, vector_token, order_id, timeout=600):
    """
    Poll endpoint /orders/{id} sampai status terminal.
    Lebih akurat dari balance polling — tidak akan false-positive atau missed.
    Interval: 10 detik. Timeout default: 10 menit.
    """
    deadline  = time.time() + timeout
    order_url = f"{config.VECTOR_BASE}/orders/{order_id}"

    while time.time() < deadline:
        time.sleep(10)
        try:
            r = hs.get(
                order_url,
                headers={**hs.vector_headers, "authorization": f"Bearer {vector_token}"},
                use_cantor=False,
                timeout=30,
            )
            if r.status_code >= 500 or r.status_code != 200:
                continue
            status = r.json().get("status", "").upper()
            if status in ORDER_TERMINAL_SUCCESS:
                return "COMPLETED"
            if status in ORDER_TERMINAL_FAIL:
                return status
        except Exception:
            continue

    return "TIMEOUT"


def execute_transaction(hs: HumanSession, cantor_token, vector_token, prepared, signing_key, mode, order_id):
    """
    Kirim transaksi ke Cantor, lalu poll order status di Vector sampai COMPLETED.
    Jauh lebih reliable dari balance-polling — tidak perlu threshold atau loop 300x.
    """
    cfg          = get_mode_config(mode)
    receive_asset = cfg["receive_asset"]

    before_canton, before_usdc, before_ceth = get_balance(hs, cantor_token)

    sig = sign_hash_b64(signing_key, prepared["hash_b64"])
    payload = {
        "command_id":             prepared["command_id"],
        "prepared_tx_b64":        prepared["prepared_tx_b64"],
        "hashing_scheme_version": prepared["hashing_scheme_version"],
        "signature_b64":          sig,
    }
    r = hs.post(
        config.EXECUTE_URL,
        headers={**hs.cantor_headers, "authorization": f"Bearer {cantor_token}"},
        json=payload,
        timeout=300,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Execute failed: {r.text}")

    status = wait_for_order_completion(hs, vector_token, order_id)
    if status != "COMPLETED":
        return "TIMEOUT_NO_BALANCE_CHANGE" if status == "TIMEOUT" else "SKIP_CYCLE"

    after_canton, after_usdc, after_ceth = get_balance(hs, cantor_token)
    if receive_asset == "canton":
        return max(after_canton - before_canton, 0)
    if receive_asset == "usdc":
        return max(after_usdc - before_usdc, 0)
    if receive_asset == "ceth":
        return max(after_ceth - before_ceth, 0)
    return 0


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

    # Step pengurangan kalau amount ditolak (slippage guard)
    if mode in ("CC_TO_USDC", "USDC_TO_CC"):
        step = 0.05      # CC/USDCx — unit besar
    elif mode in ("USDC_TO_CETH", "CETH_TO_USDC"):
        step = 0.0001    # USDCx/cETH — unit kecil
    else:
        step = 0.000001  # fallback

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
        result = execute_transaction(
            hs, cantor_token, vector_token, prepared, signing_key,
            mode, order.get("generatedOrderId", "UNKNOWN")
        )
        if result in ("TIMEOUT_NO_BALANCE_CHANGE", "SKIP_CYCLE"):
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
    """
    Cycle trading baru (sejak pair CC/cETH PAUSED 20 Mei 2025):
      Step 1 · CC  → USDCx   (CC_TO_USDC)
      Step 2 · USDCx → cETH  (USDC_TO_CETH)  ← pair utama baru
      Step 3 · cETH → USDCx  (CETH_TO_USDC)  ← pair utama baru
      Step 4 · USDCx → CC    (USDC_TO_CC)

    Jeda 30 menit antar step (seperti sebelumnya) agar terdeteksi sebagai
    aktivitas organik oleh sistem reward Cantor.
    """
    WAIT = 1800  # 30 menit

    def log(msg):
        if status_callback:
            status_callback(msg)

    cantor_token = cantor_login(hs, party_id, signing_key)

    # ── Post-confirm: tanda tangani tx pending kalau ada ─────────────────────
    txs = post_confirm(hs, cantor_token)
    if txs:
        log(f"Found {len(txs)} pending tx — finalising...")
        finalise_transaction(hs, cantor_token, signing_key, txs)

    # ── Cek open offers — kalau ada yang di-accept, skip cycle ini ───────────
    if check_and_accept_offers(hs, cantor_token, signing_key):
        log("Open offer accepted — skipping trade cycle")
        return True

    vector_token = vector_login(hs, party_id)
    current_canton, current_usdc, current_ceth = get_balance(hs, cantor_token)

    log(f"Balance awal: CC={current_canton:.4f}  USDCx={current_usdc:.4f}  cETH={current_ceth:.6f}")

    # ── Guard: CC minimum untuk mulai cycle ──────────────────────────────────
    MIN_CC = 26
    if current_canton < MIN_CC:
        # Cek apakah ada sisa USDCx / cETH yang bisa di-recover
        if current_usdc > 0.5:
            log(f"CC rendah ({current_canton:.4f}), ada USDCx={current_usdc:.4f}. Lanjut dari Step 4 (USDCx→CC).")
            result = safe_create_prepare_execute(
                hs, cantor_token, vector_token, signing_key,
                "USDC_TO_CC", round(current_usdc - 0.001, 4), party_id
            )
            if result in ("SERVICE_DOWN", "SKIP_CYCLE", None):
                log(f"Step 4 recovery gagal: {result}")
                return False
            log(f"Step 4 recovery OK: +{result:.4f} CC")
            time.sleep(WAIT)
            return True

        if current_ceth > 0.0005:
            log(f"CC rendah ({current_canton:.4f}), ada cETH={current_ceth:.6f}. Lanjut dari Step 3 (cETH→USDCx).")
            result = safe_create_prepare_execute(
                hs, cantor_token, vector_token, signing_key,
                "CETH_TO_USDC", round(current_ceth - 0.000001, 6), party_id
            )
            if result in ("SERVICE_DOWN", "SKIP_CYCLE", None):
                log(f"Step 3 recovery gagal: {result}")
                return False
            log(f"Step 3 recovery OK: +{result:.4f} USDCx")
            time.sleep(WAIT)
            # lanjut ke Step 4
            _, current_usdc, _ = get_balance(hs, cantor_token)
            result4 = safe_create_prepare_execute(
                hs, cantor_token, vector_token, signing_key,
                "USDC_TO_CC", round(current_usdc - 0.001, 4), party_id
            )
            if result4 in ("SERVICE_DOWN", "SKIP_CYCLE", None):
                log(f"Step 4 recovery gagal: {result4}")
                return False
            log(f"Step 4 recovery OK: +{result4:.4f} CC")
            time.sleep(WAIT)
            return True

        log(f"CC={current_canton:.4f}, USDCx={current_usdc:.4f}, cETH={current_ceth:.6f} — semua rendah. Skip 30 menit.")
        time.sleep(WAIT)
        return False

    # ── Step 1: CC → USDCx ───────────────────────────────────────────────────
    log("Step 1: CC → USDCx")
    sell_pct    = random.uniform(0.997, 0.998)
    sell_cc     = round(current_canton * sell_pct - 0.05, 6)
    if sell_cc <= 0:
        log("ERROR: CC tidak cukup untuk Step 1.")
        return False

    log(f"  Mengirim {sell_cc:.6f} CC ({sell_pct*100:.2f}%)")
    usdc_recv = safe_create_prepare_execute(
        hs, cantor_token, vector_token, signing_key,
        "CC_TO_USDC", sell_cc, party_id
    )
    if usdc_recv in ("SERVICE_DOWN", "SKIP_CYCLE", None):
        log(f"Step 1 gagal: {usdc_recv}")
        return False
    log(f"  Step 1 OK: +{usdc_recv:.4f} USDCx")
    time.sleep(WAIT)

    # ── Step 2: USDCx → cETH ─────────────────────────────────────────────────
    log("Step 2: USDCx → cETH")
    _, current_usdc, _ = get_balance(hs, cantor_token)
    if current_usdc <= 0.001:
        log(f"ERROR: USDCx tidak cukup untuk Step 2 ({current_usdc:.4f}).")
        return False

    send_usdc = round(current_usdc - 0.001, 4)
    log(f"  Mengirim {send_usdc:.4f} USDCx")
    ceth_recv = safe_create_prepare_execute(
        hs, cantor_token, vector_token, signing_key,
        "USDC_TO_CETH", send_usdc, party_id
    )
    if ceth_recv in ("SERVICE_DOWN", "SKIP_CYCLE", None):
        log(f"Step 2 gagal: {ceth_recv}")
        return False
    log(f"  Step 2 OK: +{ceth_recv:.6f} cETH")
    time.sleep(WAIT)

    # ── Step 3: cETH → USDCx ─────────────────────────────────────────────────
    log("Step 3: cETH → USDCx")
    _, _, current_ceth = get_balance(hs, cantor_token)
    if current_ceth <= 0.000001:
        log(f"ERROR: cETH tidak cukup untuk Step 3 ({current_ceth:.6f}).")
        return False

    send_ceth = round(current_ceth - 0.000001, 6)
    log(f"  Mengirim {send_ceth:.6f} cETH")
    usdc_recv2 = safe_create_prepare_execute(
        hs, cantor_token, vector_token, signing_key,
        "CETH_TO_USDC", send_ceth, party_id
    )
    if usdc_recv2 in ("SERVICE_DOWN", "SKIP_CYCLE", None):
        log(f"Step 3 gagal: {usdc_recv2}")
        return False
    log(f"  Step 3 OK: +{usdc_recv2:.4f} USDCx")
    time.sleep(WAIT)

    # ── Step 4: USDCx → CC ───────────────────────────────────────────────────
    log("Step 4: USDCx → CC")
    _, current_usdc, _ = get_balance(hs, cantor_token)
    if current_usdc <= 0.001:
        log(f"ERROR: USDCx tidak cukup untuk Step 4 ({current_usdc:.4f}).")
        return False

    send_usdc2 = round(current_usdc - 0.001, 4)
    log(f"  Mengirim {send_usdc2:.4f} USDCx")
    cc_recv = safe_create_prepare_execute(
        hs, cantor_token, vector_token, signing_key,
        "USDC_TO_CC", send_usdc2, party_id
    )
    if cc_recv in ("SERVICE_DOWN", "SKIP_CYCLE", None):
        log(f"Step 4 gagal: {cc_recv}")
        return False
    log(f"  Step 4 OK: +{cc_recv:.4f} CC")
    time.sleep(WAIT)

    final_canton, _, _ = get_balance(hs, cantor_token)
    log(f"Cycle selesai. CC: {current_canton:.4f} → {final_canton:.4f}")
    return True


def run_trader_worker(mnemonic, proxy_list, status_callback=None, stop_event=None):
    """
    Worker per wallet. Session HTTP dibuka & ditutup tiap cycle (bukan tiap 20 cycle)
    supaya RAM stabil di Railway free tier (512MB).
    party_id + signing_key di-cache setelah pertama kali resolve — tidak perlu
    derive ulang setiap cycle.
    """
    def log(msg):
        if status_callback:
            status_callback(msg)

    fail_count  = 0
    MAX_FAIL_WAIT = 1800  # 30 menit

    # Cache keypair & party_id — derive sekali saja, pakai terus
    signing_key, _ = build_keypair_from_mnemonic(mnemonic)
    party_id       = None
    cycle_count    = 0

    while True:
        if stop_event and stop_event.is_set():
            return

        # Buka session baru tiap cycle — langsung dibuang setelah selesai
        hs = HumanSession(proxy_list)
        try:
            # Resolve party_id sekali, cache untuk cycle berikutnya
            if not party_id:
                party_id = get_party_id(hs, mnemonic)
                if not party_id:
                    fail_count += 1
                    wait = min(60 * fail_count, MAX_FAIL_WAIT)
                    log(f"ERROR: Gagal dapat party_id. Retry in {wait}s...")
                    hs.close()
                    time.sleep(wait)
                    continue
                fail_count = 0

            if stop_event and stop_event.is_set():
                hs.close()
                return

            log(f"Cycle #{cycle_count + 1} dimulai")
            success = trader_cycle(hs, mnemonic, party_id, signing_key, status_callback)

            if success:
                cycle_count += 1
                fail_count  = 0
            else:
                fail_count += 1
                wait = min(300 * fail_count, MAX_FAIL_WAIT)
                log(f"Cycle gagal (attempt {fail_count}). Retry in {wait}s...")
                hs.close()
                time.sleep(wait)
                continue

        except Exception as e:
            fail_count += 1
            wait = min(120 * fail_count, MAX_FAIL_WAIT)
            log(f"ERROR: {e}. Restart in {wait}s... (attempt {fail_count})")
            # Reset party_id kalau error berat — kemungkinan token expired
            if fail_count >= 3:
                party_id = None
        finally:
            # Session SELALU ditutup di sini — tidak ada session yang menggantung
            hs.close()

# ═══════════════════════════════════════════════════
#  BATCH TRADER RUNNER (5 wallet per batch)
# ═══════════════════════════════════════════════════

def run_trader_batch(mnemonics, proxy_list, batch_size=10, batch_delay=(60, 120),
                     status_callback=None, stop_event=None):
    """
    Jalankan trader dalam batch. Default:
      batch_size=10  → 10 wallet per batch (50 wallet = 5 batch)
      batch_delay=(60,120)s → jeda 1-2 menit antar batch
    Session per-cycle sudah dihandle di run_trader_worker — RAM aman di Railway.
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
