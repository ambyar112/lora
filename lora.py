"""
Lora Finance Testnet - Auto Trade Bot (On-Chain) v7 FINAL
==========================================================
- Selector open : 0x3012b05c(uint256 amount, uint256 direction)
- Selector close: 0xa126d601(uint256 positionId)
- positionId    : dibaca dari Log 11 (address=market, topic[1]=posId, topic[2]=user)
- Amount        : 0.05 WETHx
- Direction     : 0 = LONG (UP)
- Durasi        : 20 detik lalu close
- Cooldown      : 24 jam
- Network       : MegaETH Testnet (chainId 6343)
"""

import asyncio
import time
from datetime import datetime
from web3 import Web3
from eth_account import Account
from eth_account.signers.local import LocalAccount

# ─── KONFIGURASI ──────────────────────────────────────────────────────────────
RPC_URL        = "https://carrot.megaeth.com/rpc"
MARKET_ADDRESS = Web3.to_checksum_address("0xeFF810eAbfE99925AC41f03C71f50b7b1da7eC23")
CHAIN_ID       = 6343
TRADE_DURATION = 20
COOLDOWN_HOURS = 24

AMOUNT_WEI     = 50_000_000_000_000_000   # 0.05 WETHx
DIRECTION_LONG = 0

SELECTOR_OPEN  = bytes.fromhex("3012b05c")
SELECTOR_CLOSE = bytes.fromhex("a126d601")

# topic[0] event PositionOpened di contract market
TOPIC_POSITION_OPENED = "0x3120c845c5d2c39308641201562a412527c1e7aff294f09c0c936f1c60a1b067"

# ─── LOAD PRIVATE KEY DARI FILE wallets.txt ───────────────────────────────────
def load_wallets(path: str = "wallets.txt") -> list:
    keys = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and not "PRIVATE_KEY" in line:
                    keys.append(line)
        print(f"[INFO] {len(keys)} wallet dimuat dari {path}")
    except FileNotFoundError:
        print(f"[ERROR] File {path} tidak ditemukan!")
    return keys

PRIVATE_KEYS = load_wallets()

# ─── SETUP WEB3 ───────────────────────────────────────────────────────────────
w3 = Web3(Web3.HTTPProvider(RPC_URL))


def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def encode_open() -> bytes:
    return SELECTOR_OPEN + AMOUNT_WEI.to_bytes(32, "big") + DIRECTION_LONG.to_bytes(32, "big")


def encode_close(position_id: int) -> bytes:
    return SELECTOR_CLOSE + position_id.to_bytes(32, "big")


def build_and_send(account: LocalAccount, data: bytes) -> str | None:
    try:
        nonce = w3.eth.get_transaction_count(account.address, "pending")
        tx = {
            "chainId":              CHAIN_ID,
            "nonce":                nonce,
            "to":                   MARKET_ADDRESS,
            "value":                0,
            "data":                 data,
            "gas":                  1_700_000,
            "maxFeePerGas":         w3.eth.gas_price,
            "maxPriorityFeePerGas": 0,
            "type":                 2,
        }
        signed  = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()
    except Exception as e:
        log(f"   [ERROR] TX gagal: {e}")
        return None


def wait_receipt(tx_hash: str, timeout: int = 60):
    try:
        return w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
    except Exception:
        return None


def get_position_id(receipt, user_address: str) -> int | None:
    """
    Ambil positionId dari log terakhir milik MARKET_ADDRESS yang punya >= 2 topics.
    topic[1] = positionId (uint256 indexed)
    """
    market_lower = MARKET_ADDRESS.lower()

    def to_hex(t):
        return t.hex() if isinstance(t, bytes) else str(t)

    for entry in reversed(receipt.logs):
        addr = entry.get("address", "").lower()
        if addr != market_lower:
            continue
        topics = entry.get("topics", [])
        if len(topics) < 2:
            continue
        t1 = to_hex(topics[1])
        pos_id = int(t1, 16)
        # Sanity check: positionId harus angka kecil (< 10000)
        if 0 < pos_id < 10000:
            return pos_id

    return None


async def trade_one_wallet(private_key: str):
    try:
        account = Account.from_key(private_key)
        addr    = account.address
        short   = f"{addr[:8]}...{addr[-6:]}"
        log(f"▶  Wallet : {short}")

        balance = w3.eth.get_balance(addr)
        log(f"   ETH gas : {w3.from_wei(balance, 'ether'):.6f} ETH")
        if balance == 0:
            log(f"   [SKIP] ETH 0 — tidak bisa bayar gas")
            return

        # ── OPEN LONG ─────────────────────────────────────────────
        amount_disp = w3.from_wei(AMOUNT_WEI, 'ether')
        log(f"   → Open LONG ({amount_disp} WETHx)...")
        open_hash = build_and_send(account, encode_open())
        if not open_hash:
            return

        log(f"   ✓ TX open : 0x{open_hash[:20]}...")
        receipt = wait_receipt(open_hash)

        if receipt is None:
            log(f"   [WARN] Receipt timeout")
            return

        if receipt.status != 1:
            log(f"   [ERROR] Open revert! Gas: {receipt.gasUsed:,}")
            return

        log(f"   ✓ Open confirmed ✅  gas: {receipt.gasUsed:,}")

        # Baca position ID dari event log
        pos_id = get_position_id(receipt, addr)
        if pos_id is None:
            log(f"   [ERROR] Position ID tidak ditemukan!")
            # Debug: tampilkan semua log untuk diagnosa
            for i, entry in enumerate(receipt.logs):
                topics = entry.get("topics", [])
                t0 = topics[0].hex() if topics and isinstance(topics[0], bytes) else (topics[0] if topics else "none")
                log(f"   Debug log[{i}] addr={entry.get('address','')} topic[0]={t0}")
            return

        log(f"   Position ID: {pos_id}")

        # ── TUNGGU 20 DETIK ───────────────────────────────────────
        log(f"   ⏳ Tunggu {TRADE_DURATION}s...")
        await asyncio.sleep(TRADE_DURATION)

        # ── CLOSE ─────────────────────────────────────────────────
        log(f"   → Close posisi (ID={pos_id})...")
        close_hash = build_and_send(account, encode_close(pos_id))
        if not close_hash:
            return

        log(f"   ✓ TX close: 0x{close_hash[:20]}...")
        close_rcpt = wait_receipt(close_hash)

        if close_rcpt and close_rcpt.status == 1:
            log(f"   ✓ Close confirmed ✅  gas: {close_rcpt.gasUsed:,}")
        else:
            gas = close_rcpt.gasUsed if close_rcpt else "?"
            log(f"   [WARN] Close revert (gas: {gas})")

        log(f"   ✅ Done | {short}")

    except Exception as e:
        log(f"   [ERROR] {e}")


async def run_all_wallets():
    valid = [pk for pk in PRIVATE_KEYS if pk and "PRIVATE_KEY" not in pk]
    log("=" * 60)
    log(f"🚀 Sesi trade | {len(valid)} wallet aktif")
    log("=" * 60)
    if not valid:
        log("[ERROR] Isi dulu PRIVATE_KEYS!")
        return
    await asyncio.gather(*[trade_one_wallet(pk) for pk in valid])
    log("=" * 60)
    log("✅ Semua wallet selesai")
    log("=" * 60)


def main():
    log("🤖 Lora Finance Auto Trade Bot v7 FINAL")
    log(f"   Market  : {MARKET_ADDRESS}")
    log(f"   Amount  : {w3.from_wei(AMOUNT_WEI, 'ether')} WETHx per trade")
    log(f"   Durasi  : {TRADE_DURATION}s | Cooldown: {COOLDOWN_HOURS}h\n")

    if not w3.is_connected():
        log("[ERROR] Tidak bisa connect ke RPC!")
        return

    log(f"✓ RPC OK | Block #{w3.eth.block_number}\n")

    while True:
        asyncio.run(run_all_wallets())

        total = COOLDOWN_HOURS * 3600
        log(f"\n⏳ Cooldown {COOLDOWN_HOURS} jam...")
        for remaining in range(total, 0, -60):
            h = remaining // 3600
            m = (remaining % 3600) // 60
            print(f"\r  ⏱  Sisa: {h:02d}j {m:02d}m    ", end="", flush=True)
            time.sleep(60)

        print()
        log("⏰ Mulai sesi berikutnya...\n")


if __name__ == "__main__":
    main()
