"""Exercise the real runner with synthetic secrets and isolated mocked tools."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SECRET_NAMES = (
    "RABBITMQ_DEFAULT_PASS",
    "IDENTITY_DATABASE_CONNECTION_STRING",
    "DELIVERY_DATABASE_CONNECTION_STRING",
    "MERCHANT_DATABASE_CONNECTION_STRING",
    "NOTIFICATIONS_DATABASE_CONNECTION_STRING",
    "IDENTITY_MIGRATION_CONNECTION_STRING",
    "DELIVERY_MIGRATION_CONNECTION_STRING",
    "MERCHANT_MIGRATION_CONNECTION_STRING",
)


def find_shell():
    configured = os.environ.get("SWIFTDROP_TEST_SHELL")
    if configured:
        return configured
    shell = shutil.which("sh")
    if shell:
        return shell
    git_shell = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    if git_shell.is_file():
        return str(git_shell)
    raise RuntimeError("Install a POSIX shell, or set SWIFTDROP_TEST_SHELL to its executable")


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="swiftdrop-runner-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.infra = self.root / "swiftdrop-infra"
        self.development = self.infra / "development"
        self.development.mkdir(parents=True)
        (self.root / "swiftdrop-backend").mkdir()
        frontend_bin = self.root / "swiftdrop-frontend/node_modules/.bin"
        frontend_bin.mkdir(parents=True)
        self.calls = self.root / "calls"
        self.calls.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("run.sh", ".env.example", "compose.yaml"):
            shutil.copyfile(REPO / "development" / name, self.development / name)
        self.secrets = {name: f"synthetic-{name}" for name in SECRET_NAMES}
        self.env_file = self.development / ".env"
        self.env_file.write_text(
            "".join(f"{name}=file-{name}\n" for name in SECRET_NAMES), encoding="utf-8"
        )
        self.original_env = self.env_file.read_bytes()
        self.environment = os.environ.copy()
        for name in ("IDENTITY_GOOGLE_WEB_CLIENT_ID", "VITE_GOOGLE_WEB_CLIENT_ID"):
            self.environment.pop(name, None)
        self.environment.update(self.secrets)
        self.environment.update(
            PATH=str(self.bin) + os.pathsep + self.environment["PATH"],
            MOCK_CALLS=str(self.calls).replace("\\", "/"),
            SWIFTDROP_TEST_BIN=str(self.bin).replace("\\", "/"),
            ConnectionStrings__Database="stale-unscoped-runtime",
            ConnectionStrings__MigrationDatabase="stale-unscoped-migration",
            Identity__Google__WebClientIds__0="stale-unscoped-google-client",
        )
        self.write_tool("curl",
            'attempt=0\n'
            'while :; do\n'
            '  ready=1\n'
            '  for name in SwiftDrop.Identity.Api SwiftDrop.Delivery.Api SwiftDrop.Merchant.Api SwiftDrop.Notification.Worker SwiftDrop.Gateway web; do\n'
            '    [ -f "$MOCK_CALLS/$name.env" ] || ready=0\n'
            '  done\n'
            '  [ "$ready" = 0 ] || exit 0\n'
            '  attempt=$((attempt + 1)); [ "$attempt" -lt 200 ] || exit 1; sleep 0.02\n'
            'done\n')
        self.write_tool("npm", "exit 0\n")
        self.write_tool(
            "docker", 'printf "%s\\n" "$@" > "$MOCK_CALLS/docker.args"\n'
            'printf "%s\\n" "$*" >> "$MOCK_CALLS/docker.sequence"\n'
            'case "$*" in *identity-migrate*) exit "${MOCK_MIGRATION_EXIT:-0}" ;; esac\n'
        )
        self.write_tool(
            "dotnet",
            'arguments=$(printf "%s\\n" "$@")\n'
            'if [ "$1" = build ]; then\n'
            '  printf "%s\\n" "$arguments" >> "$MOCK_CALLS/build.args"\n'
            '  env > "$MOCK_CALLS/build.env"\n'
            '  exit "${MOCK_BUILD_EXIT:-0}"\n'
            'fi\n'
            '[ -f "$MOCK_CALLS/build.args" ] || [ "$1" != run ] || '
            'case "$arguments" in *--migrate*) true ;; *) exit 9 ;; esac\n'
            'while [ "$#" -gt 0 ]; do\n'
            '  if [ "$1" = --project ]; then shift; project=$1; fi\n'
            '  shift\n'
            'done\n'
            'name=${project##*/}\n'
            'printf "%s\\n" "$arguments" > "$MOCK_CALLS/$name.args"\n'
            'env > "$MOCK_CALLS/$name.env"\n'
            'case "$arguments" in *--migrate*) printf "%s\\n" "$name" >> "$MOCK_CALLS/migrations"; exit "${MOCK_MIGRATION_EXIT:-0}" ;; esac\n'
            'attempt=0\n'
            'while [ ! -f "$MOCK_CALLS/web.env" ] || [ ! -f "$MOCK_CALLS/SwiftDrop.Gateway.env" ]; do\n'
            '  attempt=$((attempt + 1)); [ "$attempt" -lt 100 ] || exit 1; sleep 0.02\n'
            'done\n',
        )
        react_router = frontend_bin / "react-router"
        react_router.write_text(
            '#!/bin/sh\nenv > "$MOCK_CALLS/web.env"\n', encoding="utf-8", newline="\n"
        )
        react_router.chmod(0o755)

    def write_tool(self, name, body):
        tool = self.bin / name
        tool.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8", newline="\n")
        tool.chmod(0o755)

    def run_runner(self, *args):
        result = subprocess.run(
            [find_shell(), "-c", 'PATH="$(cd "$SWIFTDROP_TEST_BIN" && pwd):$PATH"; export PATH; . "$0"',
             str(self.development / "run.sh"), *args],
            cwd=self.infra,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
        )
        for value in self.secrets.values():
            self.assertNotIn(value, result.stdout)
        self.assertEqual(self.original_env, self.env_file.read_bytes())
        return result

    def process_environment(self, name):
        lines = (self.calls / f"{name}.env").read_text(encoding="utf-8").splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)

    def assert_scoped(self, name, setting=None, expected=None):
        process_env = self.process_environment(name)
        for secret in SECRET_NAMES:
            self.assertNotIn(secret, process_env, name)
        for candidate in ("ConnectionStrings__Database", "ConnectionStrings__MigrationDatabase"):
            if candidate == setting:
                self.assertEqual(expected, process_env[candidate], name)
            else:
                self.assertNotIn(candidate, process_env, name)

    def test_host_up_scopes_all_four_databases_after_migrations(self):
        result = self.run_runner("up", "--mode", "host")
        # Mock processes exit immediately; the runner detects that and cleans up.
        self.assertNotEqual(0, result.returncode)
        for name, secret in (
            ("SwiftDrop.Identity.Api", "IDENTITY_DATABASE_CONNECTION_STRING"),
            ("SwiftDrop.Delivery.Api", "DELIVERY_DATABASE_CONNECTION_STRING"),
            ("SwiftDrop.Merchant.Api", "MERCHANT_DATABASE_CONNECTION_STRING"),
            ("SwiftDrop.Notification.Worker", "NOTIFICATIONS_DATABASE_CONNECTION_STRING"),
        ):
            self.assert_scoped(name, "ConnectionStrings__Database", self.secrets[secret])
            self.assertNotIn("--migrate", (self.calls / f"{name}.args").read_text())
        self.assert_scoped("SwiftDrop.Gateway")
        self.assert_scoped("web")
        self.assertEqual(["SwiftDrop.Identity.Api", "SwiftDrop.Delivery.Api", "SwiftDrop.Merchant.Api"], (self.calls / "migrations").read_text().splitlines())
        self.assert_scoped("build")
        build_args = (self.calls / "build.args").read_text().splitlines()
        self.assertEqual(["build", "SwiftDrop.sln", "--configuration", "Debug"], build_args)
        for name in ("SwiftDrop.Identity.Api", "SwiftDrop.Delivery.Api",
                     "SwiftDrop.Merchant.Api", "SwiftDrop.Notification.Worker", "SwiftDrop.Gateway"):
            arguments = (self.calls / f"{name}.args").read_text().splitlines()
            self.assertIn("--no-build", arguments)
            self.assertIn("--no-restore", arguments)

    def test_host_google_audience_uses_web_client_and_stays_scoped(self):
        public_id = "web-client.apps.googleusercontent.com"
        self.environment["VITE_GOOGLE_WEB_CLIENT_ID"] = public_id
        self.run_runner("up", "--mode", "host")
        self.assertEqual(public_id, self.process_environment("SwiftDrop.Identity.Api")["Identity__Google__WebClientIds__0"])
        self.assertEqual(public_id, self.process_environment("web")["VITE_GOOGLE_WEB_CLIENT_ID"])
        for name in ("build", "SwiftDrop.Delivery.Api", "SwiftDrop.Merchant.Api", "SwiftDrop.Notification.Worker", "SwiftDrop.Gateway"):
            environment = self.process_environment(name)
            self.assertNotIn("Identity__Google__WebClientIds__0", environment)
            self.assertNotIn("VITE_GOOGLE_WEB_CLIENT_ID", environment)
        self.assertNotIn("VITE_GOOGLE_WEB_CLIENT_ID", self.process_environment("SwiftDrop.Identity.Api"))
        self.assertNotIn("Identity__Google__WebClientIds__0", self.process_environment("web"))

    def test_host_google_explicit_audience_overrides_web_fallback_in_watch_mode(self):
        self.environment["VITE_GOOGLE_WEB_CLIENT_ID"] = "web-client.apps.googleusercontent.com"
        self.environment["IDENTITY_GOOGLE_WEB_CLIENT_ID"] = "server-client.apps.googleusercontent.com"
        self.run_runner("up", "--mode", "host", "--watch")
        identity = self.process_environment("SwiftDrop.Identity.Api")
        self.assertEqual("server-client.apps.googleusercontent.com", identity["Identity__Google__WebClientIds__0"])
        self.assertNotIn("IDENTITY_GOOGLE_WEB_CLIENT_ID", identity)
        self.assertIn("watch", (self.calls / "SwiftDrop.Identity.Api.args").read_text().splitlines())
        self.assertEqual("web-client.apps.googleusercontent.com", self.process_environment("web")["VITE_GOOGLE_WEB_CLIENT_ID"])

    def test_host_google_client_id_reads_local_file_without_mutating_it(self):
        with self.env_file.open("a", encoding="utf-8") as local:
            local.write("IDENTITY_GOOGLE_WEB_CLIENT_ID=\nVITE_GOOGLE_WEB_CLIENT_ID=file-client.apps.googleusercontent.com\n")
        self.original_env = self.env_file.read_bytes()
        self.run_runner("up", "--mode", "host")
        self.assertEqual("file-client.apps.googleusercontent.com", self.process_environment("SwiftDrop.Identity.Api")["Identity__Google__WebClientIds__0"])
        self.assertEqual("file-client.apps.googleusercontent.com", self.process_environment("web")["VITE_GOOGLE_WEB_CLIENT_ID"])

    def test_proof_storage_is_delivery_only_and_export_overrides_file(self):
        mapping = {
            "ENDPOINT": "Endpoint", "BUCKET": "Bucket", "REGION": "Region",
            "ACCESS_KEY": "AccessKey", "SECRET_KEY": "SecretKey", "SESSION_TOKEN": "SessionToken",
        }
        with self.env_file.open("a", encoding="utf-8") as local:
            for suffix in mapping:
                local.write(f"DELIVERY_PROOF_STORAGE_{suffix}=file-{suffix}\n")
                self.environment[f"DELIVERY_PROOF_STORAGE_{suffix}"] = f"export-{suffix}"
                self.environment[f"ProofStorage__{mapping[suffix]}"] = "stale-unscoped"
        self.original_env = self.env_file.read_bytes()
        self.run_runner("up", "--mode", "host")
        for name in ("SwiftDrop.Identity.Api", "SwiftDrop.Delivery.Api", "SwiftDrop.Merchant.Api",
                     "SwiftDrop.Notification.Worker", "SwiftDrop.Gateway", "web", "build"):
            values = self.process_environment(name)
            for suffix, setting in mapping.items():
                self.assertNotIn(f"DELIVERY_PROOF_STORAGE_{suffix}", values)
                if name == "SwiftDrop.Delivery.Api":
                    self.assertEqual(f"export-{suffix}", values[f"ProofStorage__{setting}"])
                else:
                    self.assertNotIn(f"ProofStorage__{setting}", values)

    def test_delivery_receives_identity_public_verification_configuration(self):
        self.run_runner("up", "--mode", "host")
        values = self.process_environment("SwiftDrop.Delivery.Api")
        self.assertEqual("https://identity.swiftdrop.invalid", values["Identity__Tokens__Issuer"])
        self.assertEqual("swiftdrop-api-local", values["Identity__Tokens__Audience"])
        self.assertEqual("http://127.0.0.2:8080/.well-known/jwks.json", values["Identity__Tokens__JwksUrl"])
        self.assertEqual("true", values["Identity__Tokens__AllowInsecureJwks"])
        self.assertNotIn("Identity__Tokens__PrivateKeyPath", values)

    def test_failed_host_build_starts_no_processes(self):
        self.environment["MOCK_BUILD_EXIT"] = "7"
        result = self.run_runner("up", "--mode", "host")
        self.assertEqual(7, result.returncode, result.stdout)
        self.assertEqual(["build.env"], sorted(path.name for path in self.calls.glob("*.env")))

    def test_docker_build_and_migrations_precede_start(self):
        result = self.run_runner("up", "--build")
        self.assertEqual(0, result.returncode, result.stdout)
        calls = (self.calls / "docker.sequence").read_text().splitlines()
        self.assertTrue(calls[0].endswith(" build"))
        for call, task in zip(calls[1:4], ["identity-migrate", "delivery-migrate", "merchant-migrate"]):
            self.assertTrue(call.endswith("run --rm --no-deps " + task))
        self.assertTrue(calls[4].endswith("up -d"))

    def test_docker_migration_failure_prevents_start(self):
        self.environment["MOCK_MIGRATION_EXIT"] = "7"
        result = self.run_runner("up")
        self.assertNotEqual(0, result.returncode)
        calls = (self.calls / "docker.sequence").read_text().splitlines()
        self.assertEqual(1, len(calls))
        self.assertTrue(calls[0].endswith("identity-migrate"))

    def test_failed_migration_prevents_host_start(self):
        self.environment["MOCK_MIGRATION_EXIT"] = "7"
        result = self.run_runner("up", "--mode", "host")
        self.assertNotEqual(0, result.returncode)
        self.assertFalse((self.calls / "web.env").exists())
        self.assertFalse((self.calls / "SwiftDrop.Gateway.env").exists())
        self.assertEqual(["SwiftDrop.Identity.Api"], (self.calls / "migrations").read_text().splitlines())

    def test_delivery_host_migration_receives_only_delivery_database(self):
        result = self.run_runner("migrate", "--mode", "host", "delivery-service")
        self.assertEqual(0, result.returncode, result.stdout)
        self.assert_scoped(
            "SwiftDrop.Delivery.Api", "ConnectionStrings__Database",
            self.secrets["DELIVERY_MIGRATION_CONNECTION_STRING"],
        )
        self.assertIn("--migrate", (self.calls / "SwiftDrop.Delivery.Api.args").read_text())
        self.assertEqual(3, len(list(self.calls.iterdir())))

    def test_delivery_owner_credentials_are_only_used_for_migration(self):
        self.run_runner("migrate", "--mode", "host", "delivery-service")
        self.assert_scoped("SwiftDrop.Delivery.Api", "ConnectionStrings__Database", self.secrets["DELIVERY_MIGRATION_CONNECTION_STRING"])
        self.run_runner("up", "--mode", "host")
        self.assert_scoped("SwiftDrop.Delivery.Api", "ConnectionStrings__Database", self.secrets["DELIVERY_DATABASE_CONNECTION_STRING"])

    def test_identity_host_migration_receives_only_identity_migration_database(self):
        result = self.run_runner("migrate", "--mode", "host", "identity-service")
        self.assertEqual(0, result.returncode, result.stdout)
        self.assert_scoped(
            "SwiftDrop.Identity.Api", "ConnectionStrings__MigrationDatabase",
            self.secrets["IDENTITY_MIGRATION_CONNECTION_STRING"],
        )
        self.assertIn("--migrate", (self.calls / "SwiftDrop.Identity.Api.args").read_text())

    def test_local_file_is_data_and_supports_windows_line_endings(self):
        self.environment["DELIVERY_MIGRATION_CONNECTION_STRING"] = ""
        for secret in SECRET_NAMES:
            self.environment.pop(secret, None)
        connection = "Host=neon.invalid;Password=$(touch injected);Database=swiftdrop_delivery"
        content = "RABBITMQ_DEFAULT_PASS=synthetic-local-admin\r\n"
        content += f"DELIVERY_DATABASE_CONNECTION_STRING={connection}\r\n"
        self.env_file.write_bytes(content.encode())
        self.original_env = self.env_file.read_bytes()
        result = self.run_runner("migrate", "--mode", "host", "delivery-service")
        self.assertEqual(0, result.returncode, result.stdout)
        self.assert_scoped("SwiftDrop.Delivery.Api", "ConnectionStrings__Database", connection)
        self.assertFalse(list(self.root.rglob("injected")))

    def test_placeholder_migration_connection_fails_before_launch(self):
        self.environment["DELIVERY_MIGRATION_CONNECTION_STRING"] = ""
        self.environment["DELIVERY_DATABASE_CONNECTION_STRING"] = "REPLACE_WITH_PRIVATE_CONNECTION"
        result = self.run_runner("migrate", "--mode", "host", "delivery-service")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Configure the owning migration connection", result.stdout)
        self.assertFalse(list(self.calls.iterdir()))

    def test_empty_export_overrides_populated_local_connection(self):
        self.environment["DELIVERY_MIGRATION_CONNECTION_STRING"] = ""
        self.environment["DELIVERY_DATABASE_CONNECTION_STRING"] = ""
        result = self.run_runner("migrate", "--mode", "host", "delivery-service")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Configure the owning migration connection", result.stdout)
        self.assertFalse(list(self.calls.iterdir()))

    def test_docker_migrations_are_one_shot_without_dependencies(self):
        for service, task in (
            ("identity-service", "identity-migrate"),
            ("delivery-service", "delivery-migrate"),
        ):
            with self.subTest(service=service):
                result = self.run_runner("migrate", "--mode", "docker", service)
                self.assertEqual(0, result.returncode, result.stdout)
                arguments = (self.calls / "docker.args").read_text().splitlines()
                self.assertEqual(
                    ["--profile", "database-migrations", "run", "--rm", "--no-deps", task],
                    arguments[-6:],
                )

    def test_compose_config_validation_is_quiet(self):
        result = self.run_runner("config")
        self.assertEqual(0, result.returncode, result.stdout)
        arguments = (self.calls / "docker.args").read_text().splitlines()
        self.assertEqual(["config", "--quiet"], arguments[-2:])

    def test_migration_requires_an_implemented_owning_service(self):
        for args in (("migrate",), ("migrate", "gateway")):
            with self.subTest(args=args):
                result = self.run_runner(*args)
                self.assertEqual(2, result.returncode)
                self.assertFalse(list(self.calls.iterdir()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
