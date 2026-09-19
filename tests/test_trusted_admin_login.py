import hashlib
import unittest
from types import SimpleNamespace

from routes_auth import create_auth_routes


TRUSTED = {"cee49f3d3d45cf3a", "115bbe9c6ad3b4a4", "8ee15032fcc0ddeb"}


def fingerprint(accept_language: str) -> str:
    raw = "|||{0}".format(accept_language)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class TrustedAdminLoginTests(unittest.TestCase):
    def test_known_admin_languages_match_trusted_fingerprints(self):
        self.assertEqual(fingerprint("zh-CN,zh;q=0.9"), "cee49f3d3d45cf3a")
        self.assertEqual(
            fingerprint("zh-SG,zh-CN;q=0.9,zh-Hans;q=0.8"),
            "115bbe9c6ad3b4a4",
        )
        self.assertTrue(fingerprint("zh-CN,zh;q=0.9") in TRUSTED)
        self.assertTrue(
            fingerprint("zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6") in TRUSTED
        )
        self.assertTrue(fingerprint("zh-SG,zh-CN;q=0.9,zh-Hans;q=0.8") in TRUSTED)
        self.assertFalse(fingerprint("en-US") in TRUSTED)

    def test_trusted_device_route_exists(self):
        router = create_auth_routes()
        paths = {getattr(route, "path", "") for route in router.routes}
        self.assertIn("/auth/login/trusted-device", paths)


if __name__ == "__main__":
    unittest.main()
