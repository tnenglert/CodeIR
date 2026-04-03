"""Tests for file classification."""

import pytest
from pathlib import Path
from ir.classifier import _classify_by_filename, _classify_by_directory, classify_domain


class TestClassifyByFilename:
    """Tests for filename-based category classification."""

    def test_init_files(self):
        """__init__.py files are classified as init."""
        assert _classify_by_filename(Path("src/__init__.py")) == "init"
        assert _classify_by_filename(Path("package/subpackage/__init__.py")) == "init"

    def test_test_files(self):
        """Test files are classified as tests."""
        assert _classify_by_filename(Path("test_something.py")) == "tests"
        assert _classify_by_filename(Path("something_test.py")) == "tests"
        assert _classify_by_filename(Path("conftest.py")) == "tests"

    def test_config_files(self):
        """Config files are classified as config."""
        assert _classify_by_filename(Path("config.py")) == "config"
        assert _classify_by_filename(Path("settings.py")) == "config"

    def test_exception_files(self):
        """Exception files are classified as exceptions."""
        assert _classify_by_filename(Path("exceptions.py")) == "exceptions"
        assert _classify_by_filename(Path("errors.py")) == "exceptions"

    def test_constants_files(self):
        """Constants files are classified as constants."""
        assert _classify_by_filename(Path("constants.py")) == "constants"
        assert _classify_by_filename(Path("consts.py")) == "constants"

    def test_unclassified_returns_none(self):
        """Unknown filenames return None."""
        assert _classify_by_filename(Path("utils.py")) is None
        assert _classify_by_filename(Path("main.py")) is None


class TestClassifyByDirectory:
    """Tests for directory-based category classification."""

    def test_tests_directory(self):
        """Files in tests/ directories are classified as tests."""
        assert _classify_by_directory(Path("tests/test_foo.py")) == "tests"
        assert _classify_by_directory(Path("src/tests/conftest.py")) == "tests"

    def test_services_directory(self):
        """Files in services/ directories are classified as core_logic."""
        assert _classify_by_directory(Path("services/report_agent.py")) == "core_logic"
        assert _classify_by_directory(Path("src/services/reporting.py")) == "core_logic"

    def test_models_directory_no_longer_forces_schema(self):
        """models/ directories no longer auto-classify as schema."""
        assert _classify_by_directory(Path("models/user.py")) is None
        assert _classify_by_directory(Path("src/models/report_agent.py")) is None


class TestClassifyDomain:
    """Tests for domain classification."""

    def test_auth_domain(self):
        """Auth-related files get auth domain."""
        assert classify_domain(Path("auth.py"), None) == "auth"
        assert classify_domain(Path("authentication.py"), None) == "auth"
        assert classify_domain(Path("login.py"), None) == "auth"

    def test_http_domain(self):
        """HTTP-related files get http domain."""
        assert classify_domain(Path("requests.py"), None) == "http"
        assert classify_domain(Path("response.py"), None) == "http"

    def test_crypto_domain(self):
        """Crypto-related files get crypto domain."""
        assert classify_domain(Path("crypto.py"), None) == "crypto"
        assert classify_domain(Path("encryption.py"), None) == "crypto"
