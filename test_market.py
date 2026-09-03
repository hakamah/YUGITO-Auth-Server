import importlib.util
import os
import tempfile
import unittest


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".sqlite3")
        os.environ["YUGITO_AUTH_DB"] = self.path
        spec = importlib.util.spec_from_file_location("yugito_market_test_server", "yugito_auth_server.py")
        self.server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.server)
        self.conn = self.server.db()
        t = self.server.now()
        for aid, balance in (("seller", 100), ("buyer", 5000)):
            self.conn.execute(
                "INSERT INTO accounts(account_id,google_sub,pseudo,pseudo_norm,yt_balance,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (aid, "google-" + aid, aid, aid, balance, t, t),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_instances_keep_progression_in_public_listing(self):
        instance = self.server._new_instance(self.conn, "seller", "itachi", 1500)
        self.conn.execute(
            "UPDATE card_instances SET current_potential=100,overjet_nin=4,listed=1 WHERE instance_id=?",
            (instance["instance_id"],),
        )
        t = self.server.now()
        self.conn.execute(
            "INSERT INTO market_listings(listing_id,instance_id,seller_id,card_id,price_yt,status,created_at,updated_at) VALUES(?,?,?,?,?,'active',?,?)",
            ("sale-1", instance["instance_id"], "seller", "itachi", 1200, t, t),
        )
        self.conn.commit()
        listing = self.server.market_listings(self.conn)[0]
        self.assertEqual(100, listing["current_potential"])
        self.assertEqual(4, listing["overjet_nin"])
        self.assertEqual(4, listing["bonus_total"])
        self.assertEqual(104, listing["total_percent"])
        self.assertFalse(listing["is_full"])
        self.assertEqual("seller", listing["seller_pseudo"])
        state = self.server.economy_state(self.conn, "seller", False)
        self.assertIn("itachi", state["owned_card_ids"])
        self.assertNotIn("itachi", state["playable_owned_card_ids"])
        if "itachi" not in state["free_card_ids"]:
            self.assertNotIn("itachi", state["available_card_ids"])

    def test_full_listing_is_exactly_exposed_and_filterable_at_110(self):
        instance = self.server._new_instance(self.conn, "seller", "naruto", 1500)
        self.conn.execute(
            "UPDATE card_instances SET current_potential=100,overjet_hp=3,overjet_tai=3,overjet_nin=2,overjet_gen=2,listed=1 WHERE instance_id=?",
            (instance["instance_id"],),
        )
        t = self.server.now()
        self.conn.execute(
            "INSERT INTO market_listings(listing_id,instance_id,seller_id,card_id,price_yt,status,created_at,updated_at) VALUES(?,?,?,?,?,'active',?,?)",
            ("sale-full", instance["instance_id"], "seller", "naruto", 2250, t, t),
        )
        self.conn.commit()
        total_sql = "(i.current_potential+i.overjet_hp+i.overjet_tai+i.overjet_nin+i.overjet_gen)"
        rows = self.server.market_listings(
            self.conn,
            "l.status='active' AND " + total_sql + ">=? AND l.price_yt<=? AND i.current_potential>=100 AND (i.overjet_hp+i.overjet_tai+i.overjet_nin+i.overjet_gen)>=10",
            (110, 2500),
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("naruto", rows[0]["card_id"])
        self.assertEqual(10, rows[0]["bonus_total"])
        self.assertEqual(110, rows[0]["total_percent"])
        self.assertTrue(rows[0]["is_full"])

    def test_legacy_migration_is_idempotent(self):
        t = self.server.now()
        self.conn.execute("INSERT INTO owned_cards(account_id,card_id,purchased_at,price_yt) VALUES(?,?,?,?)", ("seller", "madara", t, 2000))
        self.conn.commit()
        self.server._SCHEMA_READY = False
        self.server._ensure_schema(self.conn)
        self.server._SCHEMA_READY = False
        self.server._ensure_schema(self.conn)
        count = self.conn.execute("SELECT COUNT(*) AS n FROM card_instances WHERE owner_id='seller' AND card_id='madara'").fetchone()["n"]
        self.assertEqual(1, count)


if __name__ == "__main__":
    unittest.main()
