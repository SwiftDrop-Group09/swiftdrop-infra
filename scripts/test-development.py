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
        self.environment.update(self.secrets)
        self.environment.update(
            PATH=str(self.bin) + os.pathsep + self.environment["PATH"],
            MOCK_CALLS=str(self.calls).replace("\\", "/"),
            ConnectionStrings__Database="stale-unscoped-runtime",
            ConnectionStrings__MigrationDatabase="stale-unscoped-migration",
        )
        self.write_tool("curl", "exit 0\n")
        self.write_tool("npm", "exit 0\n")
        self.write_tool(
            "docker", 'printf "%s\\n" "$@" > "$MOCK_CALLS/docker.args"\n'
        )
        self.write_tool(
            "dotnet",
            'arguments=$(printf "%s\\n" "$@")\n'
            'while [ "$#" -gt 0 ]; do\n'
            '  if [ "$1" = --project ]; then shift; project=$1; fi\n'
            '  shift\n'
            'done\n'
            'name=${project##*/}\n'
            'printf "%s\\n" "$arguments" > "$MOCK_CALLS/$name.args"\n'
            'env > "$MOCK_CALLS/$name.env"\n',
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
            [find_shell(), str(self.development / "run.sh"), *args],
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

    def test_host_up_scopes_all_four_databases_and_never_migrates(self):
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

    def test_delivery_host_migration_receives_only_delivery_database(self):
        result = self.run_runner("migrate", "--mode", "host", "delivery-service")
        self.assertEqual(0, result.returncode, result.stdout)
        self.assert_scoped(
            "SwiftDrop.Delivery.Api", "ConnectionStrings__Database",
            self.secrets["DELIVERY_DATABASE_CONNECTION_STRING"],
        )
        self.assertIn("--migrate", (self.calls / "SwiftDrop.Delivery.Api.args").read_text())
        self.assertEqual(2, len(list(self.calls.iterdir())))

    def test_identity_host_migration_receives_only_identity_migration_database(self):
        result = self.run_runner("migrate", "--mode", "host", "identity-service")
        self.assertEqual(0, result.returncode, result.stdout)
        self.assert_scoped(
            "SwiftDrop.Identity.Api", "ConnectionStrings__MigrationDatabase",
            self.secrets["IDENTITY_MIGRATION_CONNECTION_STRING"],
        )
        self.assertIn("--migrate", (self.calls / "SwiftDrop.Identity.Api.args").read_text())

    def test_local_file_is_data_and_supports_windows_line_endings(self):
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
        self.environment["DELIVERY_DATABASE_CONNECTION_STRING"] = "REPLACE_WITH_PRIVATE_CONNECTION"
        result = self.run_runner("migrate", "--mode", "host", "delivery-service")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Configure the owning migration connection", result.stdout)
        self.assertFalse(list(self.calls.iterdir()))

    def test_empty_export_overrides_populated_local_connection(self):
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
        for args in (("migrate",), ("migrate", "merchant-service")):
            with self.subTest(args=args):
                result = self.run_runner(*args)
                self.assertEqual(2, result.returncode)
                self.assertFalse(list(self.calls.iterdir()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
