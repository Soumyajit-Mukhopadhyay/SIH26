"""Step 1's stated test: every credential in .env loads, and is masked in logs.

The masking tests are not decoration. A screen-shared demo terminal streaming
log lines is the most likely way these keys leak, so the scrubber is treated as a
correctness requirement with its own regression tests.
"""

from __future__ import annotations

import logging

import pytest

from orca.config import REPO_ROOT, DbDriver, Settings, mask_dsn, reload_settings
from orca.obs.logging import SecretScrubber

#: Credentials the plan says are verified working, so their absence is a real
#: regression rather than a not-yet-supplied case.
REQUIRED_CAPABILITIES = (
    "groq",
    "gemini",
    "openrouter",
    "sarvam",
    "cmems",
    "earthdata",
    "cdse",
    "ais",
    "gfw",
    "langfuse",
    "supabase_auth",
)

#: Documented as not supplied. Listed explicitly so that if one starts working,
#: this test fails and reminds us to light up the adapter.
KNOWN_DORMANT = ("mosdac", "imd", "bhashini")


@pytest.fixture(scope="module")
def settings() -> Settings:
    return reload_settings()


class TestEnvFile:
    def test_env_file_exists(self):
        assert (REPO_ROOT / ".env").exists(), "copy .env.example to .env and fill it in"

    def test_env_example_is_committed_and_has_no_secrets(self):
        example = REPO_ROOT / ".env.example"
        assert example.exists()
        text = example.read_text(encoding="utf-8")
        # Fingerprints of the real keys must never appear in the committed template.
        for marker in (
            "gsk_",
            "sk_m",
            "sk-or-v1-",
            "AQ.Ab",
            "hf_",
            "pk-lf-",
            "sk-lf-",
            "sb_secret_",
            "sb_publishable_",
        ):
            assert marker not in text, f"{marker!r} leaked into .env.example"

    def test_env_example_declares_every_key_that_env_does(self):
        def keys(path):
            return {
                line.split("=", 1)[0].strip()
                for line in (REPO_ROOT / path).read_text(encoding="utf-8").splitlines()
                if "=" in line and not line.lstrip().startswith("#")
            }

        missing = keys(".env") - keys(".env.example")
        assert not missing, f".env.example is missing: {sorted(missing)}"


class TestCapabilities:
    @pytest.mark.parametrize("capability", REQUIRED_CAPABILITIES)
    def test_verified_credential_is_loaded(self, settings, capability):
        assert settings.capabilities()[capability] is True

    @pytest.mark.parametrize("capability", KNOWN_DORMANT)
    def test_documented_gap_is_still_a_gap(self, settings, capability):
        assert settings.capabilities()[capability] is False, (
            f"{capability} now has credentials — light up the adapter and move it out of "
            "KNOWN_DORMANT and out of the 'Not supplied' table in CREDENTIALS_VERIFIED.md"
        )

    def test_blank_value_reads_as_absent_not_as_empty_string(self, monkeypatch):
        monkeypatch.setenv("IMD_API_KEY", "   ")
        s = reload_settings()
        assert s.imd_api_key is None
        assert s.has_imd is False

    def test_at_least_one_llm_provider_is_available(self, settings):
        assert settings.has_groq or settings.has_gemini


class TestPersistenceSelection:
    def test_auto_driver_prefers_the_hosted_dsn(self, settings):
        assert settings.orca_db_driver is DbDriver.AUTO
        url = settings.effective_database_url
        assert url is not None
        assert "pooler.supabase.com" in url

    def test_explicit_sqlite_driver_ignores_a_present_dsn(self, monkeypatch):
        # Setting the driver must settle the question; a stray DSN in .env must
        # not quietly re-enable Postgres.
        monkeypatch.setenv("ORCA_DB_DRIVER", "sqlite")
        s = reload_settings()
        assert s.effective_database_url is None
        assert s.has_postgis is False

    def test_sqlite_path_resolves_under_the_repo(self, settings):
        assert settings.sqlite_file.is_absolute()
        assert settings.sqlite_file.is_relative_to(REPO_ROOT)


class TestRedaction:
    def test_no_secret_survives_redaction(self, settings):
        rendered = repr(settings.redacted())
        for secret in settings.secret_values():
            assert secret not in rendered, "a raw secret appeared in the redacted view"

    def test_redaction_still_identifies_the_key(self, settings):
        # Fingerprints must be distinguishable — "which key is it using?" is the
        # first question in every integration bug.
        assert settings.redacted()["groq_api_key"].startswith("gsk_Gp")
        assert "..." in settings.redacted()["groq_api_key"]

    def test_dsn_redaction_keeps_the_host_and_drops_the_password(self):
        masked = mask_dsn(
            "postgresql://postgres.abc:p%40ssw0rd@aws-0-x.pooler.supabase.com:5432/postgres"
        )
        assert "aws-0-x.pooler.supabase.com:5432" in masked
        assert "p%40ssw0rd" not in masked
        assert ":***@" in masked

    def test_short_human_password_is_masked_whole(self, settings):
        # A 6-of-14-character reveal would disclose most of a human password.
        # Long provider keys keep their identifying prefix; short secrets do not.
        pw = settings.cmems_password.get_secret_value()
        assert len(pw) < 24, "fixture assumption: this is a human password, not an API key"
        rendered = settings.redacted()["cmems_password"]
        assert rendered == f"*** (len {len(pw)})"
        assert pw[:4] not in rendered

    def test_capabilities_expose_no_values(self, settings):
        assert all(isinstance(v, bool) for v in settings.capabilities().values())


class TestSecretScrubber:
    def test_known_secret_is_scrubbed_from_a_log_record(self, settings, caplog):
        from orca.obs.logging import configure_logging

        scrubber = configure_logging(settings)
        secret = settings.groq_api_key.get_secret_value()
        assert scrubber.scrub(f"calling groq with key {secret}") == (
            "calling groq with key [REDACTED]"
        )

    @pytest.mark.parametrize(
        "leaky",
        [
            "gsk_ThisIsAFakeGroqKeyLongEnough123",
            "sk-or-v1-abcdef0123456789abcdef0123",
            "AQ.Ab8RN6FakeGoogleKeyValueHere1234",
            "hf_abcdefghijklmnopqrstuvwxyz012345",
            (
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0MTIzNDU2Nzg5"
                "MCJ9.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
            ),
        ],
    )
    def test_unknown_but_credential_shaped_strings_are_scrubbed(self, leaky):
        # The second line of defence: a dependency logging a key we have never
        # seen must still not reach the terminal.
        out = SecretScrubber([]).scrub(f"request failed: {leaky}")
        assert leaky not in out
        assert "[REDACTED]" in out

    def test_password_in_a_dsn_is_scrubbed_but_the_host_survives(self):
        out = SecretScrubber([]).scrub(
            "could not connect to postgresql://orca:supersecret@db.example.com:5432/orca"
        )
        assert "supersecret" not in out
        assert "db.example.com:5432" in out

    def test_keyword_assignment_form_keeps_the_key_name(self):
        out = SecretScrubber([]).scrub("headers: api_key=abcdef123456789")
        assert "abcdef123456789" not in out
        assert "api_key=[REDACTED]" in out

    def test_scrubbing_is_applied_through_the_logging_pipeline(self, settings, caplog):
        from orca.obs.logging import configure_logging

        scrubber = configure_logging(settings)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="token is %s",
            args=(settings.gfw_api_token.get_secret_value(),),
            exc_info=None,
        )
        scrubber.filter(record)
        assert "[REDACTED]" in record.getMessage()

    def test_ordinary_text_is_untouched(self):
        msg = "PFZ rank 1 zone 42 km SE of Chennai, Hs 2.4 m, wind 26 kn"
        assert SecretScrubber([]).scrub(msg) == msg
