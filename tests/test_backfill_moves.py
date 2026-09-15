"""Test backfill horizon reaksi + perbaikan cache parsial Binance (offline).

Jalankan:  python -m unittest tests.test_backfill_moves -v
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import backfill_moves as bf  # noqa: E402
from src.providers import binance  # noqa: E402

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)

PPI = {
    "ts_utc": "2026-09-10T13:32:03+00:00", "indicator": "PPI", "release_utc": "2026-09-10T12:30+00:00",
    "verdict": "BEARISH",
    "moves": {"BTC/USD": {"15m": -0.8569, "1h": -1.1092, "4h": None}, "ETH/USD": {"15m": -0.9078, "1h": -1.6103, "4h": None}},
    "baseline": "naive-previous", "source": "bls-api-fast",
}
NFP_FULL = {
    "indicator": "NFP", "release_utc": "2026-09-04T12:30+00:00", "verdict": "BEARISH",
    "moves": {"BTC/USD": {"15m": -2.0, "1h": -2.5, "4h": -2.2}},
}


def fake_returns(values: dict):
    calls = []

    def fn(asset, release_utc, horizons):
        calls.append((asset, release_utc, tuple(horizons)))
        return {h: values.get((asset, h)) for h in horizons}

    fn.calls = calls
    return fn


class BackfillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "live_events.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *lines):
        self.path.write_text("".join(l if isinstance(l, str) else json.dumps(l) + "\n" for l in lines), encoding="utf-8")

    def run_bf(self, fn, now=NOW, dry=False):
        with redirect_stdout(io.StringIO()):
            return bf.backfill(self.path, now, fn, dry_run=dry)

    def rows(self):
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines()]

    def test_mengisi_null_yang_jatuh_tempo_tanpa_menimpa_nilai_ada(self):
        self.write(NFP_FULL, PPI)
        fn = fake_returns({("BTC/USD", "4h"): -1.5, ("ETH/USD", "4h"): -2.1, ("BTC/USD", "1h"): 99.0})
        res = self.run_bf(fn)
        self.assertTrue(res["written"])
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], NFP_FULL, "baris tanpa null tidak disentuh")
        self.assertEqual(rows[1]["moves"]["BTC/USD"], {"15m": -0.8569, "1h": -1.1092, "4h": -1.5})
        self.assertEqual(rows[1]["moves"]["ETH/USD"]["4h"], -2.1)
        self.assertEqual(list(rows[1].keys()), list(PPI.keys()), "urutan key dan field lain dipertahankan")
        self.assertNotIn("moves_note", rows[1])
        self.assertTrue(Path(str(self.path) + ".bak").exists())
        self.assertEqual(len(fn.calls), 2, "NFP penuh tidak memanggil provider")

    def test_idempoten_run_kedua_tidak_menulis(self):
        self.write(PPI)
        fn = fake_returns({("BTC/USD", "4h"): -1.5, ("ETH/USD", "4h"): -2.1})
        self.run_bf(fn)
        after_first = self.path.read_bytes()
        res = self.run_bf(fn)
        self.assertFalse(res["written"])
        self.assertEqual(self.path.read_bytes(), after_first)

    def test_horizon_belum_jatuh_tempo_dibiarkan_tanpa_catatan(self):
        # Rilis 10:00; sekarang 12:00 -> 4h (14:00 + 30m) belum jatuh tempo.
        rec = {"indicator": "CPI", "release_utc": "2026-09-15T10:00+00:00",
               "moves": {"BTC/USD": {"15m": 0.1, "1h": None, "4h": None}}}
        self.write(rec)
        fn = fake_returns({("BTC/USD", "1h"): 0.4, ("BTC/USD", "4h"): 0.9})
        self.run_bf(fn, now=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc))
        row = self.rows()[0]
        self.assertEqual(row["moves"]["BTC/USD"], {"15m": 0.1, "1h": 0.4, "4h": None})
        self.assertNotIn("moves_note", row)

    def test_batas_grace_30_menit(self):
        rec = {"indicator": "CPI", "release_utc": "2026-09-15T10:00+00:00", "moves": {"BTC/USD": {"1h": None}}}
        self.write(rec)
        fn = fake_returns({("BTC/USD", "1h"): 0.4})
        self.run_bf(fn, now=datetime(2026, 9, 15, 11, 29, tzinfo=timezone.utc))
        self.assertIsNone(self.rows()[0]["moves"]["BTC/USD"]["1h"])
        self.run_bf(fn, now=datetime(2026, 9, 15, 11, 30, tzinfo=timezone.utc))
        self.assertEqual(self.rows()[0]["moves"]["BTC/USD"]["1h"], 0.4)

    def test_tidak_tersedia_tetap_null_dengan_catatan_bukan_nol(self):
        self.write(PPI)
        res = self.run_bf(fake_returns({("ETH/USD", "4h"): -2.1}))
        self.assertTrue(res["written"])
        row = self.rows()[0]
        self.assertIsNone(row["moves"]["BTC/USD"]["4h"])
        self.assertEqual(row["moves"]["ETH/USD"]["4h"], -2.1)
        self.assertEqual(list(row["moves_note"]), ["BTC/USD|4h"])
        self.assertIn("candle tidak tersedia", row["moves_note"]["BTC/USD|4h"])
        # Run berikutnya tanpa data baru: catatan tidak diubah, berkas tidak ditulis.
        before = self.path.read_bytes()
        later = datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc)
        self.assertFalse(self.run_bf(fake_returns({}), now=later)["written"])
        self.assertEqual(self.path.read_bytes(), before)
        # Setelah candle tersedia: terisi dan catatannya hilang.
        self.run_bf(fake_returns({("BTC/USD", "4h"): -1.5}), now=later)
        row = self.rows()[0]
        self.assertEqual(row["moves"]["BTC/USD"]["4h"], -1.5)
        self.assertNotIn("moves_note", row)

    def test_dry_run_tidak_menulis_apa_pun(self):
        self.write(PPI)
        before = self.path.read_bytes()
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = bf.backfill(self.path, NOW, fake_returns({("BTC/USD", "4h"): -1.5}), dry_run=True)
        self.assertFalse(res["written"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(Path(str(self.path) + ".bak").exists())
        self.assertIn("baris 0 PPI", buf.getvalue())
        self.assertIn("BTC/USD 4h: null -> -1.5", buf.getvalue())

    def test_baris_rusak_dan_kosong_dipertahankan_apa_adanya(self):
        self.write("{ini bukan json\n", PPI, "\n")
        self.run_bf(fake_returns({("BTC/USD", "4h"): -1.5, ("ETH/USD", "4h"): -2.1}))
        lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], "{ini bukan json\n")
        self.assertEqual(lines[2], "\n")
        self.assertEqual(json.loads(lines[1])["moves"]["BTC/USD"]["4h"], -1.5)

    def test_batal_menulis_bila_berkas_berubah_selama_run(self):
        self.write(PPI)

        def racing(asset, release_utc, horizons):
            if asset == "BTC/USD":  # tick.py append SEKALI di tengah run
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(NFP_FULL) + "\n")
            return {h: -1.0 for h in horizons}

        res = self.run_bf(racing)
        self.assertFalse(res["written"])
        self.assertEqual(len(self.rows()), 2, "event yang baru di-append tidak hilang")
        self.assertIsNone(self.rows()[0]["moves"]["BTC/USD"]["4h"])

    def test_berkas_tidak_ada(self):
        self.assertFalse(self.run_bf(fake_returns({}))["written"])


class BinancePartialCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(binance, "CACHE_DIR", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    @staticmethod
    def candles(n):
        return [[i, "1", "1", "1", str(100 + i), "1"] for i in range(n)]

    def urlopen_returning(self, n, calls):
        def fake(url, timeout=0):
            calls.append(url)
            return io.BytesIO(json.dumps(self.candles(n)).encode())
        return fake

    def test_respons_parsial_tidak_di_cache(self):
        calls = []
        with mock.patch.object(binance.urllib.request, "urlopen", self.urlopen_returning(3, calls)):
            res = binance.get_klines_15m("BTCUSDT", 1000, limit=18, throttle=0)
        self.assertTrue(res.ok)
        self.assertEqual(len(res.candles), 3)
        self.assertFalse(binance._cache_path("BTCUSDT", 1000, 18).exists())

    def test_cache_parsial_lama_dianggap_miss_dan_diambil_ulang(self):
        path = binance._cache_path("BTCUSDT", 1000, 18)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(self.candles(3)))  # ditulis oleh versi lama saat rilis
        calls = []
        with mock.patch.object(binance.urllib.request, "urlopen", self.urlopen_returning(18, calls)):
            res = binance.get_klines_15m("BTCUSDT", 1000, limit=18, throttle=0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(res.candles), 18)
        self.assertEqual(len(json.loads(path.read_text())), 18, "cache lengkap menggantikan yang parsial")

    def test_cache_lengkap_tetap_dipakai_tanpa_jaringan(self):
        path = binance._cache_path("BTCUSDT", 1000, 18)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(self.candles(18)))
        with mock.patch.object(binance.urllib.request, "urlopen", side_effect=AssertionError("jaringan dipanggil")):
            res = binance.get_klines_15m("BTCUSDT", 1000, limit=18, throttle=0)
        self.assertEqual(res.detail, "dari cache")

    def test_returns_after_horizon_4h_terisi_setelah_cache_parsial(self):
        release = datetime(2026, 9, 10, 12, 30, tzinfo=timezone.utc)
        start_ms = int(datetime(2026, 9, 10, 12, 15, tzinfo=timezone.utc).timestamp() * 1000)
        path = binance._cache_path("BTCUSDT", start_ms, 18)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(self.candles(6)))  # 15m & 1h ada, 4h belum
        calls = []
        with mock.patch.object(binance.urllib.request, "urlopen", self.urlopen_returning(18, calls)):
            out = binance.returns_after("BTC/USD", release)
        self.assertIsNotNone(out["4h"])
        self.assertEqual(out["4h"], round((116 / 100 - 1) * 100, 4))


if __name__ == "__main__":
    unittest.main()
