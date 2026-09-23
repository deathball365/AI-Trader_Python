import asyncio
import unittest

import routes_ea
from ea_auth import EAIdentity


class _Request:
    async def json(self):
        return {
            "symbol": "GOLD#",
            "min_volume": 0.01,
            "volume_step": 0.01,
            "max_volume": 100,
            "volume_digits": 2,
            "contract_size": 100,
            "price_digits": 2,
            "tick_size": 0.01,
            "point_size": 0.01,
            "tick_value": 1.0,
            "swap_long": -7.14,
            "swap_short": 2.36,
            "swap_mode": 1,
            "swap_rollover3days": 3,
            "stops_level": 0,
            "source": "mt5",
        }


class _SpecRepository:
    calls = []

    def upsert(self, account_id, symbol, payload):
        self.calls.append((account_id, symbol, payload))
        return {"account_id": account_id, "symbol": symbol, **payload}


class EAInstrumentSpecTests(unittest.TestCase):
    def test_authenticated_ea_spec_is_scoped_to_account(self):
        original = routes_ea.InstrumentSpecRepository
        fake = _SpecRepository()
        routes_ea.InstrumentSpecRepository = lambda: fake
        try:
            router = routes_ea.create_ea_routes(None)
            route = next(item for item in router.routes if item.path == "/ea/instrument_specs")
            result = asyncio.run(route.endpoint(
                request=_Request(),
                identity=EAIdentity(7, 42, "account-key", "2.0.7"),
            ))
        finally:
            routes_ea.InstrumentSpecRepository = original

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["account_id"], 42)
        self.assertEqual(fake.calls[0][0:2], (42, "GOLD#"))
        self.assertEqual(fake.calls[0][2]["price_digits"], 2)
        self.assertEqual(fake.calls[0][2]["tick_size"], 0.01)
        self.assertEqual(fake.calls[0][2]["tick_value"], 1.0)
        self.assertEqual(fake.calls[0][2]["swap_long"], -7.14)
        self.assertEqual(fake.calls[0][2]["stops_level"], 0)


if __name__ == "__main__":
    unittest.main()
