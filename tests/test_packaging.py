"""Exercise the actual hooks/launchers with fake snapd and service executables.

These test lifecycle decisions, not PostgreSQL, Silo or snap confinement.
Run with: python3 -m unittest discover -s tests -v
"""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snap = self.root / "snap"
        shutil.copytree(ROOT / "snap/local", self.snap)
        self.common = self.root / "common"
        for name in ("silo", "postgresql", "run/postgresql"):
            (self.common / name).mkdir(parents=True, exist_ok=True)
        (self.common / "secret-key").write_text("a" * 64)
        self.config = self.root / "config.json"
        self.config.write_text("{}")
        self.log = self.root / "commands.jsonl"
        self.env = dict(os.environ, SNAP=str(self.snap), SNAP_COMMON=str(self.common),
                        TEST_CONFIG=str(self.config), TEST_LOG=str(self.log),
                        PATH=f"{self.snap}/usr/bin:{os.environ['PATH']}")
        self.script("usr/bin/snapctl", '''
import json, os, sys
if sys.argv[1] == "get":
    print(json.load(open(os.environ["TEST_CONFIG"])).get(sys.argv[2], ""))
else:
    with open(os.environ["TEST_LOG"], "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
''')
        self.script("usr/bin/id", '''
import os
print("584792" if os.environ.get("TEST_DROPPED") else "0")
''')
        self.script("usr/bin/setpriv", '''
import os, sys
assert sys.argv[1:5] == ["--clear-groups", "--reuid=_daemon_", "--regid=_daemon_", "--"]
os.environ["TEST_DROPPED"] = "1"
os.execv(sys.argv[5], sys.argv[5:])
''')
        self.script("usr/lib/postgresql/18/bin/initdb", '''
import os, pathlib, sys
assert os.environ.get("TEST_DROPPED") == "1"
target = pathlib.Path(sys.argv[sys.argv.index("-D") + 1])
(target / "PG_VERSION").write_text("18\\n")
''')
        capture = '''
import json, os, sys
assert os.environ.get("TEST_DROPPED") == "1"
with open(os.environ["TEST_LOG"], "a") as f:
    f.write(json.dumps({"command": sys.argv, "environment": {
        key: os.environ.get(key) for key in
        ["DATABASE_URL", "PGDATABASE", "SECRET_KEY", "PORT", "JF_PORT", "ABS_PORT"]
    }}) + "\\n")
'''
        for command in ("usr/bin/silo", "usr/lib/postgresql/18/bin/postgres",
                        "usr/lib/postgresql/18/bin/pg_dump"):
            self.script(command, capture)
        self.script("usr/bin/psql", '''
import os, pathlib, sys
if "pg_database" in sys.argv[-1]:
    if (pathlib.Path(os.environ["SNAP_COMMON"]) / "database-created").exists():
        print("1")
else:
    print("1")
''')
        self.script("usr/bin/createdb", '''
import os, pathlib
marker = pathlib.Path(os.environ["SNAP_COMMON"]) / "database-created"
assert not marker.exists(), "database must not be recreated"
marker.touch()
''')

    def script(self, name, code):
        path = self.snap / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/usr/bin/python3\n" + code)
        path.chmod(0o755)

    def run_script(self, path, *args, success=True):
        result = subprocess.run([str(path), *args], env=self.env,
                                text=True, capture_output=True, timeout=10)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def settings(self, **values):
        self.config.write_text(json.dumps(values))

    def records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def mock_confined_ownership(self, owners=None):
        """Model root's inability to chmod a file after handing it to _daemon_."""
        ownership = self.root / "owners.json"
        ownership.write_text(json.dumps(owners or {}))
        self.env["TEST_OWNERS"] = str(ownership)
        self.script("usr/bin/id", '''
import sys
print("584792" if sys.argv[-1] == "_daemon_" else "0")
''')
        self.script("usr/bin/stat", '''
import json, os, sys
assert sys.argv[1:3] == ["-c", "%u"]
print(json.load(open(os.environ["TEST_OWNERS"])).get(sys.argv[-1], 0))
''')
        self.script("usr/bin/chown", '''
import json, os, sys
assert sys.argv[1] == "_daemon_:root"
path = os.environ["TEST_OWNERS"]
owners = json.load(open(path))
assert owners.get(sys.argv[-1], 0) == 0
owners[sys.argv[-1]] = 584792
with open(path, "w") as f:
    json.dump(owners, f)
''')
        self.script("usr/bin/chmod", '''
import json, os, sys
owners = json.load(open(os.environ["TEST_OWNERS"]))
if owners.get(sys.argv[-1], 0) != 0:
    sys.exit("chmod: Operation not permitted after ownership transfer")
os.execv("/usr/bin/chmod", ["chmod", *sys.argv[1:]])
''')
        self.script("usr/bin/install", '''
import os, sys
if "-o" in sys.argv:
    sys.exit("install: cannot change owner and permissions: Operation not permitted")
os.execv("/usr/bin/install", ["install", *sys.argv[1:]])
''')
        return ownership

    def test_install_sets_modes_before_ownership_and_preserves_key_on_retry(self):
        ownership = self.mock_confined_ownership()
        # The silo layout creates this directory before the install hook runs.
        # The other service directories must be created by the hook itself.
        shutil.rmtree(self.common / "postgresql")
        shutil.rmtree(self.common / "run")
        key_path = self.common / "secret-key"
        key_path.unlink()
        self.run_script(ROOT / "snap/hooks/install")
        key = key_path.read_bytes()
        self.assertGreaterEqual(len(key.strip()), 32)
        self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
        owners = json.loads(ownership.read_text())
        for directory in ("silo", "postgresql", "run/postgresql"):
            path = self.common / directory
            self.assertEqual(owners[str(path)], 584792)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o750)
        self.run_script(ROOT / "snap/hooks/install")
        self.assertEqual(key_path.read_bytes(), key)
        self.assertEqual(json.loads(ownership.read_text()), owners)

    def test_install_recovers_after_partial_ownership_transfer(self):
        # The old hook could hand over ownership and then fail on chmod.
        self.mock_confined_ownership({str(self.common / "silo"): 584792})
        (self.common / "silo").chmod(0o700)
        existing = self.common / "silo/keep-me"
        existing.write_text("existing data")
        self.run_script(ROOT / "snap/hooks/install")
        self.assertEqual(existing.read_text(), "existing data")
        self.assertEqual((self.common / "secret-key").read_text(), "a" * 64)

    def test_install_refuses_unexpected_directory_owner(self):
        self.mock_confined_ownership({str(self.common / "silo"): 1234})
        result = self.run_script(ROOT / "snap/hooks/install", success=False)
        self.assertIn("Unexpected owner", result.stderr)

    def test_install_refuses_symlinked_service_directory(self):
        self.mock_confined_ownership()
        shutil.rmtree(self.common / "silo")
        target = self.root / "unrelated"
        target.mkdir(mode=0o700)
        (self.common / "silo").symlink_to(target, target_is_directory=True)
        result = self.run_script(ROOT / "snap/hooks/install", success=False)
        self.assertIn("Refusing symlinked service directory", result.stderr)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_configure_accepts_defaults_and_queues_restart(self):
        self.run_script(ROOT / "snap/hooks/configure")
        self.assertEqual(self.records(), [["restart", "silo-server"]])

    def test_invalid_config_does_not_restart_services(self):
        for settings in ({"database.mode": "sqlite"}, {"database.mode": "external"},
                         {"port": "80"}, {"port": "65536"}, {"port": "08090"},
                         {"port": "8096"}, {"port": "$(touch /tmp/should-not-exist)"},
                         {"redis.url": "http://example.test"}):
            with self.subTest(settings=settings):
                self.settings(**settings)
                self.run_script(ROOT / "snap/hooks/configure", success=False)
                self.assertFalse(self.log.exists())

    def test_external_mode_leaves_bundled_database_untouched(self):
        self.settings(**{"database.mode": "external",
                         "database.url": "postgresql://user:password@db.test/silo"})
        self.run_script(self.snap / "bin/postgres-launch")
        self.assertFalse((self.common / "postgresql/data").exists())
        self.assertFalse(self.log.exists())
        self.run_script(self.snap / "bin/silo-launch")
        env = self.records()[0]["environment"]
        self.assertEqual(env["DATABASE_URL"], "postgresql://user:password@db.test/silo")
        self.assertEqual(env["SECRET_KEY"], "a" * 64)
        self.assertFalse((self.common / "database-created").exists())

    def test_bundled_cluster_restart_preserves_data(self):
        self.run_script(self.snap / "bin/postgres-launch")
        data = self.common / "postgresql/data"
        (data / "keep-me").write_text("existing database")
        self.run_script(self.snap / "bin/postgres-launch")
        self.assertEqual((data / "keep-me").read_text(), "existing database")
        self.assertIn("peer map=silo", (data / "pg_hba.conf").read_text())
        self.assertEqual((data / "pg_ident.conf").read_text(), "silo _daemon_ silo\n")
        self.assertIn("listen_addresses=", self.records()[0]["command"])
        self.run_script(self.snap / "bin/silo-launch")
        self.run_script(self.snap / "bin/silo-launch")
        self.assertTrue((self.common / "database-created").exists())
        env = self.records()[-1]["environment"]
        self.assertIn(f"host={self.common}/run/postgresql", env["DATABASE_URL"])
        self.assertEqual(env["PORT"], "8090")

    def test_incompatible_cluster_is_preserved_and_refused(self):
        data = self.common / "postgresql/data"
        data.mkdir()
        version = data / "PG_VERSION"
        version.write_text("17\n")
        self.run_script(self.snap / "bin/postgres-launch", success=False)
        self.assertEqual(version.read_text(), "17\n")
        self.assertFalse(self.log.exists())

    def test_dump_uses_configured_url_without_putting_password_in_arguments(self):
        url = "postgresql://user:password@db.test/silo?sslmode=require"
        self.settings(**{"database.mode": "external", "database.url": url})
        self.run_script(self.snap / "bin/pg-dump", "--format=custom")
        result = self.records()[0]
        self.assertEqual(result["environment"]["PGDATABASE"], url)
        self.assertNotIn("password", " ".join(result["command"]))
        self.assertEqual(result["command"][-1], "--format=custom")


if __name__ == "__main__":
    unittest.main()
